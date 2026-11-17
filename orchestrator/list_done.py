"""List completed work, most recently completed first:
`python -m orchestrator.list_done [--since YYYY-MM-DD] [--detail]`

By default, top-level tickets are listed individually and each feature
directory collapses to a single row -- shown only once *every* ticket in it is
done, dated by its most recently completed ticket. `--detail` lists every done
ticket individually with its priority instead, including those in features
that aren't finished yet.

`--since` is inclusive; without it everything is listed. A done ticket with no
`completed_at` yet (marked done after the last tick, before
issue.sync_completed_at could stamp it) -- or a feature containing one -- is
listed first as "(no date)" and is kept even with --since: it was almost
certainly finished just now. Read-only: this never stamps dates itself, that's
the orchestrator's job under its lock.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from . import issue as issue_mod
from .config import load_config
from .issue import Issue
from .list_issues import PRIORITY_TAGS


@dataclass
class Row:
    completed_at: date | None
    number: int  # tiebreak within a date; a feature uses its highest ticket number
    label: str


def _is_done(iss: Issue) -> bool:
    return iss.post.get('status') == 'done'


def _detail_rows(issues: list[Issue], config) -> list[Row]:
    return [
        Row(i.completed_at, i.number,
            f'{PRIORITY_TAGS[i.priority]} {i.path.relative_to(config.issues_dir)}')
        for i in issues if _is_done(i)
    ]


def _grouped_rows(issues: list[Issue], config) -> list[Row]:
    rows = [
        Row(i.completed_at, i.number, str(i.path.relative_to(config.issues_dir)))
        for i in issues if i.feature is None and _is_done(i)
    ]

    # Grouped over *all* issues, not just done ones -- a single unfinished
    # ticket is what keeps a feature off the list.
    features: dict[str, list[Issue]] = defaultdict(list)
    for i in issues:
        if i.feature is not None:
            features[i.feature].append(i)

    for name, members in features.items():
        if not all(_is_done(i) for i in members):
            continue
        dates = [i.completed_at for i in members]
        when = None if None in dates else max(dates)
        rows.append(Row(when, max(i.number for i in members), name))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--since',
        type=date.fromisoformat,
        metavar='YYYY-MM-DD',
        help='Only work completed on or after this date.',
    )
    parser.add_argument(
        '--detail',
        action='store_true',
        help='List individual tickets with priority, including those in unfinished features.',
    )
    args = parser.parse_args()

    config = load_config()
    issues = issue_mod.load_all_issues(config.issues_dir)
    rows = _detail_rows(issues, config) if args.detail else _grouped_rows(issues, config)

    undated = sorted((r for r in rows if r.completed_at is None), key=lambda r: r.number, reverse=True)
    dated = sorted(
        (r for r in rows if r.completed_at is not None and (args.since is None or r.completed_at >= args.since)),
        key=lambda r: (r.completed_at, r.number),
        reverse=True,
    )
    matches = undated + dated

    suffix = f' since {args.since.isoformat()}' if args.since else ''
    if not matches:
        print(f'Nothing done{suffix}.' if args.since else 'Nothing done yet.')
        return

    for row in matches:
        when = row.completed_at.isoformat() if row.completed_at else '(no date)'
        print(f'  {when.ljust(10)}  {row.label}')
    print(f'{len(matches)} done{suffix}')


if __name__ == '__main__':
    sys.exit(main())
