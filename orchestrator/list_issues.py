"""List issues that need your attention: `python -m orchestrator.list_issues`

Order: invalid status (missing, or not one of issue.STATUSES -- almost
certainly a typo, and the orchestrator silently never picks these up), then
setup failed (any status with `setup_error` set -- the orchestrator couldn't
prepare the worktree and skips the ticket every tick until you fix it), then
needs-info, then needs-plan-review, then needs-code-review, then failed
(error), then draft, then paused (hygiene reminders -- both are otherwise
invisible since the orchestrator ignores them) -- each group sorted by
priority (high first),
then issue number. Anything with a valid status outside those buckets (ready,
in-progress, plan-approved, has-*-feedback, done) isn't shown -- it's either
not your turn yet or already handled.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import issue as issue_mod
from .config import load_config

GROUPS = [
    ('NEEDS INFO', 'needs-info'),
    ('NEEDS PLAN REVIEW', 'needs-plan-review'),
    ('NEEDS CODE REVIEW', 'needs-code-review'),
    ('FAILED', 'error'),
    ('DRAFT', 'draft'),
    ('PAUSED', 'paused'),
]

PRIORITY_TAGS = {'high': '[HIGH]', 'medium': '[MED]', 'low': '[LOW]'}


def status_label(iss: issue_mod.Issue) -> str:
    return iss.post.get('status') or '(no status)'


def format_row(iss: issue_mod.Issue, issues_dir: Path, status_width: int = 0) -> str:
    """One ticket line, shared with feature_status so the columns match.
    `status_width` pads the status column, for groups mixing statuses."""
    status = status_label(iss).ljust(status_width)
    tag = PRIORITY_TAGS[iss.priority]
    return f'  #{iss.number}  {status}  {tag} {iss.path.relative_to(issues_dir)}'


def main() -> None:
    config = load_config()
    issues = issue_mod.load_all_issues(config.issues_dir)

    # post.get, not iss.status -- a missing status key must be listed here,
    # not crash the listing.
    invalid = [i for i in issues if i.post.get('status') not in issue_mod.STATUSES]
    # Any status: the ticket keeps its bucket (usually has-code-feedback or
    # ready) but is skipped every tick until the setup problem is fixed.
    setup_failed = [i for i in issues if i.setup_error]
    groups = [('INVALID STATUS', invalid), ('SETUP FAILED', setup_failed)] + [
        (label, [i for i in issues if i.post.get('status') == status])
        for label, status in GROUPS
    ]

    printed_any = False
    for label, matches in groups:
        if not matches:
            continue
        printed_any = True
        print(label)
        for iss in sorted(matches, key=issue_mod.sort_key):
            print(format_row(iss, config.issues_dir))
            if label == 'SETUP FAILED':
                for line in iss.setup_error.splitlines():
                    print(f'      {line}')
                print(f'      (since {iss.post.get("setup_error_since")})')
        print()

    if not printed_any:
        print('Nothing needs attention.')


if __name__ == '__main__':
    sys.exit(main())
