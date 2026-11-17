from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import frontmatter

# Frontmatter is authoritative. Filenames are stable and never touched by
# the orchestrator -- renaming on every status change turned out to be more
# annoying in practice than useful (open editor tabs, git-mv churn). Use
# `python -m orchestrator.list_issues` to see status without relying on `ls`.
STATUSES = [
    'draft',
    'paused',
    'ready',
    'in-progress',
    'needs-plan-review',
    'plan-approved',
    'has-plan-feedback',
    'needs-code-review',
    'has-code-feedback',
    'needs-info',
    'error',
    'done',
]

AREAS = ('backend', 'frontend')

# Highest first -- index into this tuple is the sort rank. Optional in
# frontmatter; missing or unrecognized values count as DEFAULT_PRIORITY so a
# typo in one ticket can't crash the whole tick.
PRIORITIES = ('high', 'medium', 'low')
DEFAULT_PRIORITY = 'medium'

_FILENAME_NUMBER_RE = re.compile(r'^(\d+)-')
_BLOCKED_RE = re.compile(r'blocked by:?\s*(.+)', re.IGNORECASE)
# A plain substring search, not anchored to the start of a reference -- this
# deliberately also matches a feature-qualified "some-feature/#12" (numbers
# are globally unique across every feature directory, see load_all_issues, so
# the "some-feature/" prefix carries no information resolution needs and is
# simply ignored).
_REF_RE = re.compile(r'#(\d+)')


@dataclass
class Issue:
    path: Path
    post: frontmatter.Post
    feature: str | None = None

    @property
    def number(self) -> int:
        return int(self.post['number'])

    @property
    def area(self) -> str:
        return self.post['area']

    @property
    def slug(self) -> str:
        return self.post['slug']

    @property
    def status(self) -> str:
        return self.post['status']

    @property
    def requires_plan(self) -> bool:
        return bool(self.post.get('requires_plan', False))

    @property
    def priority(self) -> str:
        value = str(self.post.get('priority') or DEFAULT_PRIORITY).strip().lower()
        return value if value in PRIORITIES else DEFAULT_PRIORITY

    @property
    def attempts(self) -> int:
        return int(self.post.get('attempts', 0))

    @property
    def branch(self) -> str | None:
        return self.post.get('branch')

    @property
    def setup_error(self) -> str | None:
        return self.post.get('setup_error')

    @property
    def completed_at(self) -> date | None:
        # YAML already loads an unquoted 2026-09-25 as a date; a quoted or
        # hand-typed string is parsed, anything unparseable counts as unset.
        value = self.post.get('completed_at')
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value.strip())
            except ValueError:
                return None
        return None

    @property
    def content(self) -> str:
        return self.post.content

    def blocked_by(self) -> list[int]:
        """Issue numbers referenced by a "Blocked by #N" / "Blocked by: #N, #M"
        line anywhere in the body. A malformed "Blocked by" line (no #N found)
        is NOT treated as unblocked — see is_unblocked()."""
        refs: list[int] = []
        for line in self.content.splitlines():
            m = _BLOCKED_RE.match(line.strip())
            if not m:
                continue
            refs.extend(int(n) for n in _REF_RE.findall(m.group(1)))
        return refs

    def has_malformed_blocker_line(self) -> bool:
        for line in self.content.splitlines():
            m = _BLOCKED_RE.match(line.strip())
            if m and not _REF_RE.search(m.group(1)):
                return True
        return False


def load_issue(path: Path, issues_dir: Path) -> Issue:
    post = frontmatter.load(path)
    feature = path.parent.name if path.parent != issues_dir else None
    return Issue(path=path, post=post, feature=feature)


def load_all_issues(issues_dir: Path) -> list[Issue]:
    """Loads top-level issues plus one level of feature-directory issues
    (issues_dir/<feature>/*.md) -- feature directories are one level only,
    so a deeper glob is deliberately not used."""
    issues = []
    paths = list(issues_dir.glob('*.md')) + list(issues_dir.glob('*/*.md'))
    for path in sorted(paths):
        if path.name == 'TEMPLATE.md':
            continue
        issues.append(load_issue(path, issues_dir))
    return issues


def sort_key(issue: Issue) -> tuple[int, int]:
    """Highest priority first, then oldest (lowest issue number)."""
    return (PRIORITIES.index(issue.priority), issue.number)


def index_by_number(issues: list[Issue]) -> dict[int, Issue]:
    return {i.number: i for i in issues}


def is_unblocked(issue: Issue, index: dict[int, Issue]) -> bool:
    if issue.has_malformed_blocker_line():
        return False
    for n in issue.blocked_by():
        blocker = index.get(n)
        # Unresolvable or still-open blocker: fail closed, same as the
        # reference GitHub workflow does for a missing/unclosed dependency.
        if blocker is None or blocker.status != 'done':
            return False
    return True


def save(issue: Issue) -> Issue:
    """Write frontmatter+content to disk. Never renames the file -- the
    filename is whatever it was created with; only frontmatter `status`
    reflects current state."""
    frontmatter.dump(issue.post, str(issue.path))
    return issue


def set_status(issue: Issue, status: str, **extra_fields) -> Issue:
    if status not in STATUSES:
        raise ValueError(f'unknown status {status!r}, expected one of {STATUSES}')
    issue.post['status'] = status
    for k, v in extra_fields.items():
        issue.post[k] = v
    return save(issue)


def sync_completed_at(issues: list[Issue]) -> list[tuple[Issue, str]]:
    """Stamp `completed_at` on done tickets that lack it, and clear it on
    tickets that have since been reopened. Returns what changed, for logging.

    The date is the file's mtime, not today: a human flips status to done by
    editing the file, and the orchestrator may not tick again for days (e.g.
    the machine was off). Written as a date object so YAML emits it unquoted.
    Tickets with an invalid/missing status are left alone."""
    changes: list[tuple[Issue, str]] = []
    for iss in issues:
        status = iss.post.get('status')
        if status == 'done' and iss.completed_at is None:
            stamped = date.fromtimestamp(iss.path.stat().st_mtime)
            iss.post['completed_at'] = stamped
            save(iss)
            changes.append((iss, f'set {stamped.isoformat()}'))
        elif status in STATUSES and status != 'done' and iss.post.get('completed_at') is not None:
            iss.post['completed_at'] = None
            save(iss)
            changes.append((iss, 'cleared'))
    return changes


def append_section(issue: Issue, heading: str, body: str) -> Issue:
    """Append a `## heading` section to the issue body. Used by the
    orchestrator itself for bookkeeping notes; Claude appends its own
    plan/review sections directly while it runs."""
    sep = '\n\n' if issue.post.content and not issue.post.content.endswith('\n\n') else ''
    issue.post.content = f'{issue.post.content}{sep}## {heading}\n\n{body}\n'
    return save(issue)


def record_setup_error(issue: Issue, message: str) -> bool:
    """Record a pre-container setup failure (worktree/docs sync) as state that
    gets overwritten, not history that accumulates: setup is retried every
    tick, and a stuck ticket once got re-tried ~90 times over a weekend.
    Only a *new* message (first failure, or a different one) touches the
    file -- it sets `setup_error`/`setup_error_since` and appends one
    `## Agent setup failed` section. A repeat of the same message is a no-op,
    so there's no duplicate section and no mtime churn. Returns whether
    anything was written."""
    if issue.setup_error == message:
        return False
    issue.post['setup_error'] = message
    issue.post['setup_error_since'] = datetime.now().isoformat(timespec='seconds')
    append_section(issue, 'Agent setup failed', (
        'The orchestrator could not prepare this ticket\'s worktree, so no '
        'agent ran. It is skipped (status and attempts untouched) and retried '
        'every tick until setup succeeds, which clears `setup_error`.\n\n'
        f'```\n{message}\n```'
    ))
    return True


def clear_setup_error(issue: Issue) -> bool:
    """Drop the setup_error fields after a successful setup. Doesn't save --
    the caller's next set_status does. Returns whether there was one."""
    had = issue.post.get('setup_error') is not None
    issue.post.metadata.pop('setup_error', None)
    issue.post.metadata.pop('setup_error_since', None)
    return had


def next_issue_number(issues_dir: Path) -> int:
    max_n = 0
    paths = list(issues_dir.glob('*.md')) + list(issues_dir.glob('*/*.md'))
    for path in paths:
        if path.name == 'TEMPLATE.md':
            continue
        m = _FILENAME_NUMBER_RE.match(path.name)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return max_n + 1


def slugify(title: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
