from __future__ import annotations

from dataclasses import dataclass

from . import issue as issue_mod
from .issue import Issue


@dataclass
class Task:
    kind: str  # 'plan' | 'implement' | 'plan_revision' | 'code_revision'
    issue: Issue


def _first_unblocked(candidates: list[Issue], index: dict[int, Issue]) -> Issue | None:
    for iss in sorted(candidates, key=issue_mod.sort_key):
        if issue_mod.is_unblocked(iss, index):
            return iss
    return None


def select_task(issues: list[Issue], exclude: set[int] | frozenset[int] = frozenset()) -> Task | None:
    """Priority order, mirroring the GitHub-based version this was adapted
    from: feedback on something already in flight always wins over starting
    new work, so review rounds drain the backlog instead of piling up behind
    fresh tickets. Within a bucket, highest ticket `priority` first, then
    oldest (lowest issue number), and a blocked ticket is skipped rather than
    stalling everything behind it. Ticket priority never overrides the bucket
    order -- a low-priority revision still beats a high-priority new ticket.

    `exclude` holds issue numbers already skipped this tick (their setup
    failed, see main.SetupFailed); they're passed over as if blocked. The
    blocker index is still built from every issue, so exclusion never changes
    whether some other ticket counts as unblocked."""
    index = issue_mod.index_by_number(issues)
    issues = [i for i in issues if i.number not in exclude]

    # Priority 1: revision rounds. Not blocker-gated -- work already started.
    code_feedback = [i for i in issues if i.status == 'has-code-feedback']
    if code_feedback:
        return Task('code_revision', min(code_feedback, key=issue_mod.sort_key))

    plan_feedback = [i for i in issues if i.status == 'has-plan-feedback']
    if plan_feedback:
        return Task('plan_revision', min(plan_feedback, key=issue_mod.sort_key))

    # Priority 2: a plan you've already approved.
    approved = [i for i in issues if i.status == 'plan-approved']
    chosen = _first_unblocked(approved, index)
    if chosen:
        return Task('implement', chosen)

    # Priority 3: a plain ready ticket -- plan-first or straight to implementing.
    ready = [i for i in issues if i.status == 'ready']
    chosen = _first_unblocked(ready, index)
    if chosen:
        return Task('plan' if chosen.requires_plan else 'implement', chosen)

    return None
