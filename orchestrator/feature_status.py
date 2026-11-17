"""Show every ticket in one feature, grouped by stage:
`python -m orchestrator.feature_status upcoming-calls`

Order: invalid status (missing, or not one of issue.STATUSES), then draft,
paused, error, in progress (everything between ready and done -- the status column
still shows which), ready, done -- each group sorted by priority (high
first), then issue number, with the same columns as list_issues. Empty groups
aren't shown. The feature argument may also be given as a path
(`issues/upcoming-calls/`); only its last segment is used.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import issue as issue_mod
from .config import load_config
from .list_issues import format_row, status_label

GROUPS = [
    ('DRAFT', {'draft'}),
    ('PAUSED', {'paused'}),
    ('ERROR', {'error'}),
    ('IN PROGRESS', {
        'in-progress',
        'needs-plan-review',
        'plan-approved',
        'has-plan-feedback',
        'needs-code-review',
        'has-code-feedback',
        'needs-info',
    }),
    ('READY', {'ready'}),
    ('DONE', {'done'}),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('feature', help='Feature directory name under issues/, e.g. upcoming-calls')
    args = parser.parse_args()
    name = Path(args.feature).name

    config = load_config()
    issues = issue_mod.load_all_issues(config.issues_dir)
    members = [i for i in issues if i.feature == name]
    if not members:
        known = sorted({i.feature for i in issues if i.feature is not None})
        parser.error(f'unknown feature {name!r}; known features: {", ".join(known) or "(none)"}')

    # post.get, not iss.status -- a missing status key must be listed here,
    # not crash the listing.
    invalid = [i for i in members if i.post.get('status') not in issue_mod.STATUSES]
    groups = [('INVALID STATUS', invalid)] + [
        (label, [i for i in members if i.post.get('status') in statuses])
        for label, statuses in GROUPS
    ]

    for label, matches in groups:
        if not matches:
            continue
        print(label)
        # Pad to the longest status in this group, so a mixed group (IN
        # PROGRESS, INVALID STATUS) still lines up.
        width = max(len(status_label(i)) for i in matches)
        for iss in sorted(matches, key=issue_mod.sort_key):
            print(format_row(iss, config.issues_dir, width))
        print()


if __name__ == '__main__':
    sys.exit(main())
