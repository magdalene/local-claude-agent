from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import Config
from .env import load_env_file


class RunSetupError(Exception):
    """Something the container needs couldn't be prepared. The run fails
    (status reverts, see main.py's _fail) rather than crashing the tick."""


class MissingCredential(RunSetupError):
    pass


def _oauth_token(config: Config) -> str:
    dotenv = load_env_file(config.general_rules.parent / '.env')
    token = dotenv.get('CLAUDE_CODE_OAUTH_TOKEN', '')
    if not token:
        raise MissingCredential(
            'CLAUDE_CODE_OAUTH_TOKEN not set. Run `claude setup-token` once (on the '
            'host, not in the container) and put CLAUDE_CODE_OAUTH_TOKEN=... in '
            '.env at the root of this repo (gitignored).'
        )
    return token


def _git_common_dir(worktree_path: Path) -> Path:
    """The primary repo's `.git` directory. A linked worktree's own `.git` is
    just a file pointing into it, and commits write their objects and refs
    there, so the container can't work without it."""
    result = subprocess.run(
        ['git', 'rev-parse', '--path-format=absolute', '--git-common-dir'],
        cwd=worktree_path, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RunSetupError(f'could not find the git dir for {worktree_path}: {result.stderr.strip()}')
    return Path(result.stdout.strip())


def _write_committed_file(repo_dir: Path, ref: str, file_path: Path, dest: Path) -> None:
    """Writes `file_path` as committed at `ref` to `dest`, so the agent reads
    the rules as committed, not whatever uncommitted edits sit in that
    checkout."""
    result = subprocess.run(
        ['git', 'show', f'{ref}:{file_path.relative_to(repo_dir).as_posix()}'],
        cwd=repo_dir, capture_output=True,
    )
    if result.returncode != 0:
        raise RunSetupError(f'{file_path} is not committed on {ref}: {result.stderr.decode().strip()}')
    dest.write_bytes(result.stdout)


# Committed placeholder copies of an env file -- no secrets, and the agent
# may legitimately need to read them (e.g. to see which settings exist).
_ENV_PLACEHOLDER_SUFFIXES = ('.template', '.sample', '.example')


def _worktree_secrets_to_mask(worktree_path: Path) -> tuple[list[Path], list[Path]]:
    """(dirs, files) in the worktree to hide, as a second layer behind only
    mounting what the run needs (see _mount_args). A worktree is a fresh
    checkout of tracked files, so neither should ever exist -- but `.local`
    (a conventional gitignored stash for local secrets/data exports) or a real
    `.env` (live API/DB credentials) copied in
    by hand would otherwise be readable, and the container has network
    access. Checks the worktree root and one level down."""
    dirs = [p for p in (worktree_path / '.local',) if p.is_dir()]
    files = [
        p
        for pattern in ('.env*', '*/.env*')
        for p in worktree_path.glob(pattern)
        if p.is_file() and not p.name.endswith(_ENV_PLACEHOLDER_SUFFIXES)
    ]
    return dirs, files


def _mount_args(
    config: Config,
    *,
    staging: Path,
    repo_path: Path,
    worktree_path: Path,
    repo_agent_rules: Path,
    default_branch: str,
) -> list[str]:
    """The container sees only what the run needs, each at its host path so
    the worktree's absolute `.git` pointers still resolve: the ticket's
    worktree, the repo's `.git` dir, the issues dir, and both AGENT_RULES.md
    files as committed. Nothing else under workspace_root -- primary
    checkouts' uncommitted or gitignored files, other worktrees, this repo's
    code/logs/`.env` -- is mounted at all, so something new dropped there
    later is covered without anyone remembering to mask it."""
    git_dir = _git_common_dir(worktree_path)

    # This repo's rules come from its own HEAD (it lives on main); the target
    # repo's from its default branch, same as the worktree branches off.
    general_rules_copy = staging / 'general-rules.md'
    repo_rules_copy = staging / 'repo-rules.md'
    _write_committed_file(config.general_rules.parent, 'HEAD', config.general_rules, general_rules_copy)
    _write_committed_file(repo_path, default_branch, repo_agent_rules, repo_rules_copy)

    args = [
        '-v', f'{worktree_path}:{worktree_path}',
        '-v', f'{git_dir}:{git_dir}',
        '-v', f'{config.issues_dir}:{config.issues_dir}',
        '-v', f'{general_rules_copy}:{config.general_rules}:ro',
        '-v', f'{repo_rules_copy}:{repo_agent_rules}:ro',
    ]
    # Read-only over the rw `.git` mount (a later mount shadows an earlier
    # one). The repo's config and hooks take effect on the host whenever you
    # use git in the primary checkout -- a hook script, or core.hooksPath/
    # core.fsmonitor in config -- so writing either would let the agent run
    # code outside the container.
    for protected in (git_dir / 'config', git_dir / 'hooks'):
        if protected.exists():
            args += ['-v', f'{protected}:{protected}:ro']
    # --tmpfs only mounts over directories; a file is shadowed by binding
    # /dev/null over it, so it reads as empty.
    secret_dirs, secret_files = _worktree_secrets_to_mask(worktree_path)
    for d in secret_dirs:
        args += ['--tmpfs', str(d)]
    for f in secret_files:
        args += ['-v', f'/dev/null:{f}:ro']
    return args


def run_container(
    config: Config,
    *,
    task_kind: str,
    issue_number: int,
    issue_file: Path,
    repo_path: Path,
    worktree_path: Path,
    branch: str,
    area: str,
    repo_agent_rules: Path,
    default_branch: str,
    install_cmd: str,
    repo_env: dict[str, str],
    log_path: Path,
) -> int:
    """Runs one non-interactive agent turn in the container, blocking until it
    exits or the configured timeout is hit. Returns the container's exit code,
    or -1 on timeout (the container is killed). stdout+stderr go to log_path.
    Raises RunSetupError if the container's inputs can't be prepared.

    Env vars are passed via a temp --env-file rather than `-e KEY=VALUE` on
    the command line, so the OAuth token doesn't show up in `ps` output.
    """
    env_vars = {
        # The repo's own `env:` from config.yml first, so nothing there can
        # override the orchestrator's vars below.
        **repo_env,
        'CLAUDE_CODE_OAUTH_TOKEN': _oauth_token(config),
        # Scopes the agent's git identity to just this container process via
        # env vars, rather than `git config` against the worktree -- see the
        # comment in worktree.py's ensure_worktree() for why that broke.
        'GIT_AUTHOR_NAME': 'Claude Agent',
        'GIT_AUTHOR_EMAIL': 'agent@local',
        'GIT_COMMITTER_NAME': 'Claude Agent',
        'GIT_COMMITTER_EMAIL': 'agent@local',
        'TASK_KIND': task_kind,
        'ISSUE_FILE': str(issue_file),
        'REPO_PATH': str(repo_path),
        'WORKTREE_PATH': str(worktree_path),
        'BRANCH': branch,
        'AREA': area,
        'GENERAL_RULES': str(config.general_rules),
        'REPO_RULES': str(repo_agent_rules),
        'INSTALL_CMD': install_cmd,
        'MAX_TURNS': str(config.max_turns),
    }

    container_name = f'local-claude-agent-{issue_number}'

    # Holds the env file and the rules copies for the life of the run.
    # mkdtemp makes it 0700, so the token isn't readable by other users.
    staging = Path(tempfile.mkdtemp(prefix='local-claude-agent-'))
    try:
        env_file_path = staging / 'container.env'
        env_file_path.write_text(''.join(f'{k}={v}\n' for k, v in env_vars.items()))

        cmd = [
            'docker', 'run', '--rm',
            '--name', container_name,
            '--env-file', str(env_file_path),
            *_mount_args(
                config, staging=staging, repo_path=repo_path, worktree_path=worktree_path,
                repo_agent_rules=repo_agent_rules, default_branch=default_branch,
            ),
            '-w', str(worktree_path),
            config.docker_image,
        ]

        timeout_seconds = config.container_timeout_minutes * 60
        with open(log_path, 'w') as log_f:
            proc = subprocess.Popen(cmd, stdout=log_f, stderr=subprocess.STDOUT)
            try:
                return proc.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                subprocess.run(['docker', 'kill', container_name], capture_output=True)
                proc.wait()
                return -1
    finally:
        # Loud rather than ignore_errors: container.env holds the OAuth token,
        # so a staging dir that survives the run is worth noticing.
        try:
            shutil.rmtree(staging)
        except OSError as e:
            print(f'warning: could not remove {staging} (holds the OAuth token): {e}', file=sys.stderr)
