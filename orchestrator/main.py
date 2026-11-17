"""Entry point for one orchestrator tick. Invoked by launchd on a schedule
(see com.example.agent.plist) or manually with `python -m orchestrator.main`.

Does at most one unit of work (a plan, an implementation, or a feedback
revision) on at most one issue, then exits. See ../AGENT_RULES.md for the
status machine this drives."""

from __future__ import annotations

import datetime
import re
import subprocess
import sys
from pathlib import Path

from . import issue as issue_mod
from . import worktree
from .config import Config, load_config
from .docker_run import RunSetupError, run_container
from .docs_sync import sync_backend_docs
from .lock import LockHeld, acquire
from .select_task import Task, select_task

EXPECTED_STATUS = {
    'plan': 'needs-plan-review',
    'implement': 'needs-code-review',
    'plan_revision': 'needs-plan-review',
    'code_revision': 'needs-code-review',
}
EXPECTS_NEW_COMMITS = {'implement', 'code_revision'}
CAPPED_KINDS = {'plan', 'implement'}  # attempts/error apply only here

# Claude Code prints this verbatim (as the sole assistant turn) when it hits
# the account's session/usage limit mid-run. The CLI's exit code in that case
# is not reliable -- it's been observed as both 0 and 1 -- so detection here
# is based purely on the log content, never on exit_code. This isn't a ticket
# failure, just bad timing, so it's detected separately and excluded from the
# attempts counter.
SESSION_LIMIT_RE = re.compile(r"hit your (?:session|usage) limit", re.IGNORECASE)


def _hit_session_limit(log_path: Path) -> bool:
    try:
        text = log_path.read_text(errors='replace')
    except OSError:
        return False
    return any(
        SESSION_LIMIT_RE.search(line)
        for line in text.splitlines()
        if line.startswith('[assistant]')
    )


class SetupFailed(Exception):
    """Pre-container setup (worktree, docs sync) failed for this ticket. It's
    been recorded on the issue (issue.record_setup_error) with status and
    attempts untouched; main() skips it and picks the next task in the same
    tick, so one stuck ticket can't starve the queue."""


def _setup_error_message(e: Exception) -> str:
    # git's stderr ("fatal: '<branch>' is already used by worktree at ...")
    # says what's wrong; the CalledProcessError repr only has the argv.
    if isinstance(e, subprocess.CalledProcessError) and e.stderr:
        return e.stderr.strip()
    return f'{type(e).__name__}: {e}'


def log(config: Config, msg: str) -> None:
    line = f'{datetime.datetime.now().isoformat(timespec="seconds")} {msg}'
    print(line)
    config.log_dir.mkdir(parents=True, exist_ok=True)
    with open(config.log_dir / 'orchestrator.log', 'a') as f:
        f.write(line + '\n')


def run_task(config: Config, task: Task) -> None:
    iss = task.issue
    repo = config.repos[iss.area]
    branch = f'{config.branch_prefix}{iss.number}-{iss.slug}'

    pre_status = iss.status
    pre_attempts = iss.attempts

    # Anything failing here happens before the status flip, so without this
    # the ticket stays in its bucket and gets re-selected (and re-fails)
    # every tick, blocking everything behind it -- e.g. its branch left
    # checked out in the primary repo makes `git worktree add` refuse.
    try:
        path = worktree.ensure_worktree(repo, branch, iss.number, iss.slug)
        if iss.area == 'frontend':
            # Refreshed every run, not just on first creation, so a long-lived
            # ticket never works off a stale snapshot of the backend contract.
            sync_backend_docs(config, path)
    except Exception as e:
        message = _setup_error_message(e)
        issue_mod.record_setup_error(iss, message)
        log(config, f'#{iss.number} ({task.kind}): setup failed, skipping (left as {pre_status}): {message}')
        raise SetupFailed(message) from e

    if issue_mod.clear_setup_error(iss):
        log(config, f'#{iss.number}: setup succeeded, cleared setup_error')
    log(config, f'#{iss.number} ({task.kind}): {pre_status} -> in-progress')
    pre_head = worktree.head_sha(path)

    if task.kind in CAPPED_KINDS and iss.attempts >= config.max_attempts:
        issue_mod.set_status(iss, 'error', branch=branch)  # also persists a cleared setup_error
        log(config, f'#{iss.number}: already failed {iss.attempts} times, giving up without running')
        return

    extra = {'branch': branch}
    if task.kind in CAPPED_KINDS:
        extra['attempts'] = iss.attempts + 1
    issue_mod.set_status(iss, 'in-progress', **extra)

    log_path = config.log_dir / f'run-{iss.number}-{int(datetime.datetime.now().timestamp())}.log'
    try:
        exit_code = run_container(
            config,
            task_kind=task.kind,
            issue_number=iss.number,
            issue_file=iss.path,
            repo_path=repo.path,
            worktree_path=path,
            branch=branch,
            area=iss.area,
            repo_agent_rules=repo.agent_rules,
            default_branch=repo.default_branch,
            install_cmd=repo.install_cmd,
            repo_env=repo.env,
            log_path=log_path,
        )
    except RunSetupError as e:
        log(config, f'#{iss.number}: {e}')
        _fail(config, iss, pre_status, task.kind, reason=str(e))
        return

    # Re-read from disk: the container edited this file's frontmatter/content
    # directly, so the in-memory `iss` loaded before the run is stale.
    fresh = issue_mod.load_issue(iss.path, config.issues_dir)
    expected = EXPECTED_STATUS[task.kind]
    got_expected_status = fresh.status == expected
    got_commits = True
    if task.kind in EXPECTS_NEW_COMMITS:
        got_commits = worktree.head_sha(path) != pre_head

    if exit_code == 0 and got_expected_status and got_commits:
        if task.kind in CAPPED_KINDS:
            fresh.post['attempts'] = 0  # success clears the failure counter
        issue_mod.save(fresh)
        log(config, f'#{iss.number}: {task.kind} succeeded -> {fresh.status}')
        return

    if _hit_session_limit(log_path):
        if task.kind in CAPPED_KINDS:
            fresh.post['attempts'] = pre_attempts  # not the ticket's fault, don't count it
        issue_mod.set_status(fresh, pre_status)
        log(config, f'#{iss.number}: hit Claude session limit, leaving as {pre_status} to retry next tick (not counted as an attempt)')
        return

    reason = _diagnose(exit_code, got_expected_status, got_commits, log_path)
    log(config, f'#{iss.number}: {task.kind} did not complete cleanly ({reason})')
    _fail(config, fresh, pre_status, task.kind, reason=reason, log_path=log_path)


def _diagnose(exit_code: int, got_status: bool, got_commits: bool, log_path: Path) -> str:
    if exit_code == -1:
        return f'container timed out, see {log_path}'
    if exit_code != 0:
        return f'container exited {exit_code}, see {log_path}'
    if not got_status:
        return 'exited 0 but did not set the expected status'
    if not got_commits:
        return 'exited 0 and set the expected status but left no new commits'
    return 'unknown'


def _fail(config: Config, iss: issue_mod.Issue, pre_status: str, kind: str, *, reason: str, log_path: Path | None = None) -> None:
    note = f'Run did not complete: {reason}.'
    if log_path:
        note += f'\n\nFull container log: `{log_path}`'
    issue_mod.append_section(iss, 'Agent run failed', note)

    if kind in CAPPED_KINDS and iss.attempts >= config.max_attempts:
        issue_mod.set_status(iss, 'error')
        log(config, f'#{iss.number}: giving up after {iss.attempts} attempts')
    else:
        issue_mod.set_status(iss, pre_status)


def main() -> int:
    config = load_config()

    try:
        with acquire(config.lock_file):
            issues = issue_mod.load_all_issues(config.issues_dir)
            for iss, change in issue_mod.sync_completed_at(issues):
                log(config, f'completed_at #{iss.number} {change}')
            skipped: set[int] = set()
            while True:
                task = select_task(issues, exclude=skipped)
                if task is None:
                    if skipped:
                        nums = ', '.join(f'#{n}' for n in sorted(skipped))
                        log(config, f'nothing else to do (skipped {nums})')
                    else:
                        log(config, 'nothing to do')
                    return 0
                try:
                    run_task(config, task)
                    return 0
                except SetupFailed:
                    skipped.add(task.issue.number)
    except LockHeld as e:
        log(config, f'skip: run already in progress (pid {e.pid})')
        return 0


if __name__ == '__main__':
    sys.exit(main())
