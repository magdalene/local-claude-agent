# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local, no-GitHub orchestrator that runs a Docker-sandboxed Claude Code
instance against issue tickets for two sibling repos, `backend` and
`frontend` (checked out as siblings of this repo under `workspace_root` in
`config.yml`). Issues are markdown files with YAML frontmatter under
`issues/`; the orchestrator picks one at a time, works in a `git worktree` on
its own branch, and a human reviews/merges locally. Nothing here ever pushes
anywhere or touches GitHub — see `AGENT_RULES.md` for the full design and the
status machine that drives everything.

This repo (`local-claude-agent`) is the orchestrator itself — the thing that
*runs* the agent, not the agent's target codebase. There is no build/lint/test
suite here; changes are verified by running a tick by hand and reading
`logs/`.

## Commands

```
.venv/bin/python3 -m orchestrator.new_issue <backend|frontend> "title" [--requires-plan] [--priority high|medium|low]
.venv/bin/python3 -m orchestrator.main            # run one tick
.venv/bin/python3 -m orchestrator.list_issues      # what needs human attention
.venv/bin/python3 -m orchestrator.list_done [--since YYYY-MM-DD] [--detail]  # completed tickets/features, newest first
.venv/bin/python3 -m orchestrator.feature_status <feature>  # one feature's tickets by stage
```

Dependencies: `.venv/bin/pip install -r orchestrator/requirements.txt`
(`pyyaml`, `python-frontmatter`). Rebuild the Docker image
(`docker build -t local-claude-agent:latest .`) after editing `Dockerfile` or
`docker/run_task.py` — the orchestrator does not do this for you.

To verify a change to orchestrator logic, run a tick by hand and check
`logs/orchestrator.log` and the per-run `logs/run-<issue>-<timestamp>.log`.

## Architecture

**One tick, one unit of work.** `orchestrator/main.py`'s `main()` acquires a
single-run lock (`orchestrator/lock.py` — PID file, liveness-checked, a held
lock just skips the tick rather than queuing), loads all issues, asks
`select_task.py` for at most one `Task`, runs it, and exits. There is no
scheduler loop in-process; `com.example.agent.plist` (launchd) is what calls
`main()` repeatedly.

**Task selection is a priority queue over issue status**
(`orchestrator/select_task.py`): revision rounds (`has-code-feedback`,
`has-plan-feedback`) always beat starting new work; then approved plans
(`plan-approved`); then plain `ready` tickets (which go to a `plan` task if
`requires_plan` else straight to `implement`). Within a bucket, highest
`priority` first (optional frontmatter `high`/`medium`/`low`, missing =
`medium`; it never overrides the bucket order), then oldest issue number;
blocked tickets (`Blocked by #N` in the body, resolved via
`issue.is_unblocked`) are skipped, not stalling.

**The issue file is the state machine.** `orchestrator/issue.py` wraps
`python-frontmatter`; `status:` in the YAML frontmatter is authoritative.
Filenames (`{number}-{area}-{slug}.md`) are stable and never renamed by the
orchestrator — see the status table in `AGENT_RULES.md` for who's allowed to
set which status. The orchestrator only ever writes `in-progress`, `error`,
or reverts to the pre-run status on failure; everything else is either a
human or the containerized agent. The one exception is
`completed_at`: every tick, `issue.sync_completed_at` stamps it on `done`
tickets that lack it (using the file's mtime, not today, so a stretch where
no ticks ran doesn't skew it) and clears it on tickets reopened out of `done`.

Tickets live either directly under `issues/` or one level down in a feature
directory (`issues/<feature>/`, created via `new_issue --feature`);
`load_all_issues`/`next_issue_number` search both, numbering stays global
across features, and blocker resolution (`is_unblocked`) looks tickets up
purely by number — a `some-feature/#N` blocker reference and a bare `#N` are
equivalent, since `_REF_RE` is an unanchored substring match.

**Per-tick flow** (`main.py`'s `run_task`): resolve/create the worktree
(`orchestrator/worktree.py` — branches off the repo's local
`default_branch`, never off whatever's checked out in the primary worktree),
sync backend API docs into the frontend worktree if `area == 'frontend'`
(`orchestrator/docs_sync.py` — `git archive`s `backend/docs` at backend's
local tip on every run, gitignored in frontend, so the frontend agent always
sees backend's current contract), check the attempts cap for capped kinds
(`plan`/`implement`; revision kinds are uncapped since they're already
in-flight work), flip status to `in-progress`, run the container
(`orchestrator/docker_run.py`), then reload the issue file from disk
(the container edited it directly, so the in-memory copy is stale) and
validate the run: exit code 0, status landed on the kind's expected value
(`EXPECTED_STATUS`), and — for `implement`/`code_revision` — new commits
landed (`EXPECTS_NEW_COMMITS`, checked via HEAD sha before/after). Any
mismatch reverts status and appends an `## Agent run failed` section with the
diagnosis; capped kinds that exhaust `max_attempts` go to `error` instead of
reverting.

**Setup failures skip, they don't stall.** If worktree creation or docs sync
throws (e.g. the ticket's branch is checked out in the primary repo, so `git
worktree add` refuses), `run_task` records it via `issue.record_setup_error`
and raises `SetupFailed`; `main()` adds the number to `select_task`'s
`exclude` set and picks the next task in the same tick. Status and attempts
are untouched, and the orchestrator never touches the primary checkout to fix
it. The error is overwritten state, not history: frontmatter
`setup_error`/`setup_error_since` plus one `## Agent setup failed` section,
written only when the message changes, so a ticket stuck for days doesn't
pile up sections. The next successful setup clears the fields; `list_issues`
shows stuck tickets under SETUP FAILED. Before this, an uncaught setup
exception left the ticket top of its bucket and re-failed every tick,
starving the whole queue with nothing in `orchestrator.log`.

**Git identity is scoped per-container-process via env vars**
(`GIT_AUTHOR_NAME`/`GIT_COMMITTER_NAME`/etc in `docker_run.py`), deliberately
*not* `git config` in the worktree — a linked worktree's `.git` is a file
pointing at the primary repo's shared config, so `git config` there once
corrupted the real repo's identity for every worktree including the primary
one. Don't reintroduce that.

**The container only sees what's in git, plus its ticket**
(`docker_run.py`'s `_mount_args`): the ticket's worktree, the repo's `.git`
dir (its `config` and `hooks` read-only, since both take effect on the host),
`issues/`, and both `AGENT_RULES.md` files extracted with `git show`. This
repo's comes from `HEAD`, the target repo's from `default_branch`. Nothing
else under `workspace_root` is mounted (primary checkouts, `.env`, `.local`,
other worktrees, this repo's code and logs). **Edits to either
`AGENT_RULES.md` only reach the agent once committed.**

**The container is single-shot and non-interactive**
(`docker/run_task.py`): installs the target repo's dependencies with its
configured `install_cmd`, builds one of four prompts (`PROMPTS` dict — plan /
implement / plan_revision / code_revision) from env vars set by
`docker_run.py`, and runs `claude -p` once with `--output-format
stream-json` (parsed by `render_event` into readable log lines — plain
`--verbose` alone doesn't stream per-turn). `Task`/`Agent` are deliberately
disallowed tools, and `git push`/`checkout`/`reset` too — see
`AGENT_RULES.md` "Where you are" / "Finish inside this one turn". The
container never resumes a run; if the agent gets stuck it must set
`needs-info` and stop, not wait for anything.

**Config is one YAML file** (`config.yml`, loaded by `orchestrator/config.py`)
holding both repos' paths, worktree dirs, `install_cmd`, and orchestrator
tunables (`max_attempts`, `max_turns`, `container_timeout_minutes`). Secrets
(`CLAUDE_CODE_OAUTH_TOKEN`) live only in the gitignored `.env`, loaded by
`orchestrator/env.py` and passed into the container via a temp `--env-file`
(never `-e` on the command line, so it doesn't leak into `ps`).

## Working in this repo

- `AGENT_RULES.md` at the repo root is the rulebook every containerized run
  reads first — it defines the status machine, escalation rules, and branch
  rules. If you're changing orchestrator behavior around statuses, task
  kinds, or the agent's allowed actions, that file and `select_task.py` /
  `issue.py` need to agree with each other and with `docker/run_task.py`'s
  `PROMPTS`.
- `issues/` is tracked in git but the orchestrator only edits files there —
  it never commits. Committing issue changes is a human/user action.
- `logs/` (`orchestrator.log`, per-run `run-<issue>-<timestamp>.log`,
  `launchd.{out,err}.log`) is the only observability here; there's no test
  suite to lean on instead when debugging a change.
