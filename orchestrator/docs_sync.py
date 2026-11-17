from __future__ import annotations

import shutil
import subprocess
import tarfile
from io import BytesIO
from pathlib import Path

from .config import Config

DOCS_SUBDIR = 'docs'


def sync_backend_docs(config: Config, frontend_worktree: Path) -> None:
    """Copies backend/docs, as of backend's local default-branch tip -- not
    whatever the primary backend worktree happens to have checked out right
    now -- into frontend_worktree/docs. Called before every frontend
    container run (plan, implement, or a revision round) so the frontend
    agent always sees backend's current contract, not a stale snapshot from
    whenever this worktree was first created.

    `docs/` is gitignored in the frontend repo (see its .gitignore) so this
    never gets committed by the frontend agent.
    """
    backend = config.repos['backend']
    dest = frontend_worktree / DOCS_SUBDIR

    result = subprocess.run(
        ['git', 'archive', backend.default_branch, '--', DOCS_SUBDIR],
        cwd=backend.path, capture_output=True,
    )

    if dest.exists():
        shutil.rmtree(dest)

    if result.returncode != 0 or not result.stdout:
        # No docs/ on backend's default branch yet (or the pathspec matched
        # nothing). Leave an empty directory rather than erroring -- the
        # frontend AGENT_RULES.md already treats a missing doc as a
        # needs-info situation, not a crash.
        dest.mkdir(parents=True, exist_ok=True)
        return

    with tarfile.open(fileobj=BytesIO(result.stdout)) as tar:
        tar.extractall(frontend_worktree, filter='data')
