"""Create a new issue file: `python -m orchestrator.new_issue backend "Fix timezone bug"`"""

from __future__ import annotations

import argparse
import sys

from . import issue as issue_mod
from .config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('area', choices=issue_mod.AREAS)
    parser.add_argument('title')
    parser.add_argument('--requires-plan', action='store_true')
    parser.add_argument(
        '--priority',
        choices=issue_mod.PRIORITIES,
        default=issue_mod.DEFAULT_PRIORITY,
    )
    parser.add_argument(
        '--feature',
        help='Place the ticket under issues/<feature>/ (created if it doesn\'t exist yet). '
             'One level only -- no slashes.',
    )
    args = parser.parse_args()

    if args.feature and ('/' in args.feature or '\\' in args.feature):
        parser.error('--feature must be a single directory name, not a path')

    config = load_config()
    number = issue_mod.next_issue_number(config.issues_dir)
    slug = issue_mod.slugify(args.title)

    body = f"""---
number: {number}
area: {args.area}
slug: {slug}
status: draft
priority: {args.priority}
requires_plan: {str(args.requires_plan).lower()}
attempts: 0
branch: null
completed_at: null
---

## Description

{args.title}
"""
    target_dir = config.issues_dir / args.feature if args.feature else config.issues_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f'{number}-{args.area}-{slug}.md'
    path.write_text(body)
    print(f'Created {path}')


if __name__ == '__main__':
    sys.exit(main())
