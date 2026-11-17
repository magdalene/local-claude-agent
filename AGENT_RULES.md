# Agent house rules

General rules for the local agent that runs against the `backend` and `frontend`
repos. Every container run starts its prompt by pointing here; this is the one
place to change agent behaviour without touching the orchestrator or Docker
setup. Repo-specific conventions (test commands, lint, definition of done) live
in `AGENT_RULES.md` at the root of `backend`/`frontend` themselves — read that
one too, it's been passed to you alongside this file.

## Where you are

You're running inside a Docker container, in a `git worktree` checked out on
your own branch. This is a local, no-GitHub setup: there is no PR, no CI, no
remote. Everything you do is either a commit on your branch or an edit to the
issue file — nothing you do here reaches `origin` or the real `main` unless a
human explicitly merges it later.

**Never run `git push`, `git checkout main`, or anything that touches a branch
other than your own.** There's no branch protection net here like there would
be on GitHub — the only thing stopping you from clobbering `main` locally is
not trying to.

There's also no `origin` to talk to — no credentials in the container, and
nothing fetches for you. `main` (the local branch) is the up-to-date one;
`origin/main` is whatever was last fetched on the host and may be stale.
Rebase or merge onto `main`, never `origin/main`, and don't `git fetch`.

`main` is also the only way to pick up code that isn't yours. Never merge,
rebase onto, or cherry-pick from any other branch, including another
ticket's `agent/...` branch, even if that ticket is `done`. Those branches
usually land on `main` as a squash commit, so their original commits on your
branch become a duplicate history that conflicts with `main`. If code your
ticket depends on isn't on `main` yet, don't work around it: set `needs-info`
and name the ticket whose code is missing.

## Running commands

Your Bash commands are checked against an allowlist, and in this
non-interactive run anything off it is simply denied — each denial is a
wasted turn. To stay on it:

- Run git from your working directory. No `git -C <path>` or `git -c
  key=value` — both get denied even for otherwise-allowed subcommands.
- No `$VAR`, `$?`, or `$(...)` in commands; they get denied outright. Check
  a command's exit status from its output instead of echoing `$?`.
- Don't `cd` elsewhere before running git.
- For files outside your worktree (this file, the issue file, the repo's
  `AGENT_RULES.md`), use the Read/Edit tools, not `cat`/`tail`/`grep` in
  Bash — Bash is limited to your worktree and `/tmp`.
- Scratch files (an old version of a file, a patch) go in `/tmp`, not the
  worktree.
- `git stash` (other than `stash list`) isn't available (the stash is shared with the human's
  checkout). Commit work-in-progress to your branch instead.

## The issue file

You've been given the path to one issue file (`$ISSUE_FILE`), a markdown file
with a YAML frontmatter header:

```yaml
---
number: 17
area: backend
slug: fix-timezone-bug
status: ready
priority: medium
requires_plan: false
attempts: 1
branch: agent/17-fix-timezone-bug
completed_at: null
---
```

- `priority` (`high` / `medium` / `low`, optional, defaults to `medium`) is
  set by humans to order the queue. Never change it.
- `completed_at` is managed by the orchestrator (stamped when a human marks
  the ticket `done`). Never set or change it.
- **Never remove or rewrite content a human wrote.** Append new `##` sections;
  don't edit the description or existing feedback sections.
- **The status field drives everything.** Set it precisely to the value your
  task instructions below tell you to, and nothing else — the orchestrator
  checks it changed as expected and treats a wrong/missing status as a failed
  run.
- A ticket may start with a `Blocked by #N` (or `Blocked by: #N, #M`) line at
  the top of the description; a blocker may also be written
  `some-feature/#N` if that reads more clearly — it resolves identically,
  since issue numbers are unique across the whole `issues/` tree including
  feature subdirectories. You won't be handed a ticket whose blockers aren't
  all `done` — the orchestrator gates on that before you ever run.

## Status machine

| Status | Set by | Meaning |
|---|---|---|
| `draft` | human | still being written, not ready yet — ignored entirely (see below) |
| `paused` | human | work deliberately paused — ignored entirely, like `draft` |
| `ready` | human | ready to work on |
| `in-progress` | orchestrator | claimed, a run is active |
| `needs-plan-review` | you | plan posted, waiting on a human |
| `plan-approved` | human | plan approved, implement it |
| `has-plan-feedback` | human | plan sent back with comments, revise it |
| `needs-code-review` | you | branch ready, waiting on a human |
| `has-code-feedback` | human | review comments left, address them |
| `needs-info` | you | you're blocked on a question only a human can answer |
| `error` | orchestrator | gave up after repeated failed attempts |
| `done` | human | merged into `main`, blockers on this issue are now free |

You only ever write `needs-plan-review`, `needs-code-review`, or `needs-info`.
Everything else is set by the orchestrator or by hand.

A `draft` or `paused` ticket is invisible to you — you'll never be handed one
to work on. It still counts as a real blocker, though: if you're handed a
ticket that says `Blocked by #N` and #N is a draft or paused, you won't see
that ticket either, since the orchestrator won't hand it to you until #N
reaches `done`.

## Branch naming

Your branch is `agent/<issue-number>-<slug>` — already created and checked out
for you by the orchestrator (`$BRANCH`, working directory `$WORKTREE_PATH`).
Just commit to it.

## Escalate instead of guessing

If the ticket is ambiguous, underspecified, or you're blocked on something you
can't resolve from the code:

1. Append a `## Question` section to the issue file with your **specific**
   question — not "please clarify", but what you'd need to know and what the
   candidate readings are.
2. Set `status: needs-info`.
3. Stop. Do not guess and implement anyway, and do not leave the ticket
   `in-progress` — that reads as a run that's still going, not one that's
   stuck.

A confident wrong implementation costs more review time than an unanswered
question.

## Finish inside this one turn

This is a single non-interactive run, not a session — nothing will resume you
and no one will answer you mid-run. If you can't finish, say so in the issue
file and set the appropriate status rather than leaving work half-done with no
explanation. Don't delegate to a subagent and wait for it; that tool is
unavailable here for the same reason.

## Revision rounds (plan or code feedback)

When you're re-invoked on a ticket with `has-plan-feedback` or
`has-code-feedback`:

- For code feedback: `git rebase main` in your worktree before reading the
  feedback (a `## Code review feedback` section in the issue file), so you're
  not reasoning about a stale branch. Resolve mechanical conflicts yourself;
  if a conflict turns on what someone *intended* rather than a mechanical
  merge, that's a `needs-info` situation.
- Address every point raised, either with a change or by appending a reply in
  the issue file explaining why you disagree. Silently skipping a point is not
  an option — leave visible evidence you engaged with it.
- Then re-check the definition of done in the repo-specific `AGENT_RULES.md`
  before setting status back to `needs-plan-review` / `needs-code-review`.
