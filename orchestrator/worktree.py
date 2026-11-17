from __future__ import annotations

import subprocess
from pathlib import Path

from .config import RepoConfig


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True)


def branch_exists(repo: RepoConfig, branch: str) -> bool:
    result = subprocess.run(
        ['git', 'rev-parse', '--verify', '--quiet', branch],
        cwd=repo.path, capture_output=True, text=True,
    )
    return result.returncode == 0


def worktree_path(repo: RepoConfig, issue_number: int, slug: str) -> Path:
    return repo.worktrees_dir / f'{issue_number}-{slug}'


def ensure_worktree(repo: RepoConfig, branch: str, issue_number: int, slug: str) -> Path:
    """Create the branch+worktree off the repo's local default branch if this
    is the first time we've touched this issue; otherwise reuse the existing
    worktree so a revision round continues on the same branch. Branches
    explicitly off `repo.default_branch`, not whatever happens to be checked
    out in the primary worktree right now — you may be mid-work on something
    else there."""
    repo.worktrees_dir.mkdir(parents=True, exist_ok=True)
    path = worktree_path(repo, issue_number, slug)

    if path.exists():
        return path

    if branch_exists(repo, branch):
        _git('worktree', 'add', str(path), branch, cwd=repo.path)
    else:
        _git('worktree', 'add', '-b', branch, str(path), repo.default_branch, cwd=repo.path)

    # Deliberately NOT `git config user.name/email` here: a linked worktree's
    # `.git` is a plain file pointing back at the primary repo's config, so
    # `git config` run with cwd=path writes to the SAME shared config as the
    # primary checkout -- there is no implicit per-worktree scoping. Doing
    # this once corrupted the real repo's local git identity for every
    # worktree, including the primary one, and let a run commit under the
    # agent's name using what looked like your identity. The agent's git
    # identity is instead set via GIT_AUTHOR_*/GIT_COMMITTER_* env vars,
    # scoped to just the one container process -- see docker_run.py.
    return path


def remove_worktree(repo: RepoConfig, path: Path) -> None:
    """Only ever called for a worktree whose branch has been merged (status
    done) or abandoned — never as part of the normal review flow, so a
    worktree survives exactly as long as its issue is open."""
    subprocess.run(['git', 'worktree', 'remove', '--force', str(path)], cwd=repo.path, check=True)


def head_sha(path: Path) -> str | None:
    result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=path, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None
