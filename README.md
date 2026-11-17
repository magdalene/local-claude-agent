# local-claude-agent

A local, no-GitHub autonomous agent that works tickets against two sibling
repos, `backend` and `frontend`.
Issues live here as markdown files; a Docker-sandboxed Claude Code instance
picks one up at a time, plans and/or implements it in a `git worktree`, and
you review/merge locally. Nothing here ever pushes anywhere or touches
GitHub — see `AGENT_RULES.md` for the full design.

## Commands

- `python -m orchestrator.new_issue <backend|frontend> "title" [--feature <name>]` — create issue
- `python -m orchestrator.main` — run one tick
- `python -m orchestrator.list_issues` — show what needs attention
- `python -m orchestrator.list_done [--since YYYY-MM-DD] [--detail]` — show completed work
- `python -m orchestrator.feature_status <feature>` — show a feature's tickets by stage

## Statuses

- **draft** — being written, agent ignores it
- **paused** — work parked, agent ignores it until you set another status by hand (don't pause a ticket mid-run — the run's own status write wins)
- **ready** — queued for the agent
- **in-progress** — a run is active now
- **needs-plan-review** — plan posted, awaiting your review
- **plan-approved** — approved, agent will implement
- **has-plan-feedback** — you commented, agent will revise
- **needs-code-review** — code ready, awaiting your review
- **has-code-feedback** — you commented, agent will revise
- **needs-info** — agent has a question
- **error** — gave up after repeated failures
- **done** — merged into `main`, unblocks dependents

## Code review checklist

- `git worktree remove <worktree>`
- `git checkout <branch>`
- Do the review, add feedback in a `## Code review feedback` section
- `git checkout main` (to release the branch)
- When ready, set status to `has-code-feedback`

## One-time setup

0. **Point it at your repos:** edit `config.yml` (paths, `install_cmd`,
   `default_branch`), and add an `AGENT_RULES.md` to the root of each target
   repo (committed on its default branch) describing its test/lint commands
   and definition of done. Adjust the Bash allowlist in `docker/run_task.py`
   and the `Dockerfile` to fit your stack. The two repo names `backend` and
   `frontend` are currently hardcoded (`orchestrator/issue.py`'s `AREAS`,
   plus the docs sync in `orchestrator/docs_sync.py`).

1. **Build the image:**
   ```
   docker build -t local-claude-agent:latest .
   ```
2. **Auth:** on the host (not in a container), run `claude setup-token` to
   generate a long-lived OAuth token for your Pro/Max subscription. Copy
   `.env.sample` to `.env` and paste it in. `.env` is gitignored.
3. **Orchestrator venv:**
   ```
   python3 -m venv .venv
   .venv/bin/pip install -r orchestrator/requirements.txt
   ```
4. **Schedule it:**
   ```
   # first edit the paths in com.example.agent.plist
   cp com.example.agent.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.example.agent.plist
   ```
   Ticks every 15 minutes by default (`StartInterval` in the plist). Adjust
   to taste — it's a latency knob, not a correctness one: a tick that finds
   nothing to do costs nothing, and a tick that finds the lock held just
   skips (see `orchestrator/lock.py`).
5. **Sanity check before trusting the schedule:** run a tick by hand —
   ```
   .venv/bin/python3 -m orchestrator.main
   ```

## Day to day

**Writing tickets:**
```
.venv/bin/python3 -m orchestrator.new_issue backend "Fix timezone bug in reminders"
.venv/bin/python3 -m orchestrator.new_issue frontend "Add filter to users table" --requires-plan
.venv/bin/python3 -m orchestrator.new_issue backend "Add webhook endpoint" --feature payments-integration
```
Creates `issues/{N}-{area}-{slug}.md`, or `issues/{feature}/{N}-{area}-{slug}.md`
when `--feature <name>` is given (the directory is created if it doesn't
exist yet; one level only, not nested). Issue numbers are always sequential
across the whole `issues/` tree — a feature directory never restarts the
counter. New tickets start as `draft` — invisible to the agent — so you can
flesh out the description (and add a `Blocked by #N` line if it depends on
another ticket — `Blocked by: some-feature/#N` also works and resolves the
same way, since numbers are unique across every feature) without it getting
picked up half-written. Set `status: ready` when it's actually ready.

**Reviewing:** run `.venv/bin/python3 -m orchestrator.list_issues` — filenames
are stable (never renamed by the orchestrator), so status only lives in the
frontmatter; this is how you see it without opening every file. Each morning/
afternoon, work through what it lists, in the order it lists them:

- Plan review: read the `## Plan` section. Approve by setting
  `status: plan-approved`; request changes by appending your comments under a
  `## Plan feedback` section and setting `status: has-plan-feedback`.
- Code review: the issue file's `## Implementation notes` section names the
  branch and worktree. Since the worktree is a real local checkout sharing
  this machine's git object database (no push/pull involved — see
  `AGENT_RULES.md`), you can review it directly:
  ```
  cd ../backend-worktrees/17-fix-timezone-bug   # or wherever it landed
  git diff main
  ```
  Approve by merging the branch however you normally do (e.g. push it and
  merge a PR, then pull `main` locally), and set `status: done`. This is what
  frees up anything `Blocked by #17`. The agent always branches off and
  rebases onto your *local* `main`, so keep it up to date. Request changes the same way as a plan: `## Code review feedback` +
  `status: has-code-feedback`.

**Status changes you make by hand always win** — just edit the `status:`
frontmatter field. Filenames are never touched by the orchestrator; only the
frontmatter matters.

## Logs

- `logs/orchestrator.log` — one line per tick (what it picked, what happened).
- `logs/run-<issue>-<timestamp>.log` — full container output for one run.
- `logs/launchd.{out,err}.log` — launchd's own stdout/stderr capture.

A stuck/zombie run shows up as `logs/orchestrator.log` repeatedly printing
`skip: run already in progress (pid N)`. Check whether that pid is still
alive (`ps -p N`); if not, delete `logs/agent.lock` (the next tick would also
reclaim it automatically, since it checks liveness itself).

## API contract docs (backend → frontend)

`backend/docs/*-apis.md` document the request/response contract for backend
API surfaces. The backend agent is expected (per the backend's own
`AGENT_RULES.md`) to keep these current as part of its definition of done.

Before every frontend container run, `orchestrator/docs_sync.py` copies that
directory (as of backend's local `main`, via `git archive` — not whatever the
primary backend worktree happens to have checked out) into the frontend
worktree at `docs/`, gitignored there so it's never accidentally committed.
This is the frontend agent's only view into backend's API surface; see the
frontend's own `AGENT_RULES.md` → "Use the API docs, don't guess the
contract".

## Layout

- `issues/` — the ticket store, tracked in git. Tickets live either directly
  here or one level down in a feature directory (`issues/<feature>/`, see
  `new_issue --feature` above) — the orchestrator searches both. The
  orchestrator edits files here but never commits — that's on you, whenever
  you want. Most of what a commit message would say is already in the issue
  file itself anyway.
- `orchestrator/` — the Python script that runs one tick.
- `docker/run_task.py` + `Dockerfile` — the sandboxed agent itself.
- `AGENT_RULES.md` — general rules every run reads, regardless of repo.
  Repo-specific rules (test commands, definition of done) live in
  `AGENT_RULES.md` at the root of `backend`/`frontend`.
- `com.example.agent.plist` — the launchd schedule (macOS).

Rebuild the image (`docker build -t local-claude-agent:latest .`) after editing
`Dockerfile` or `docker/run_task.py` — the orchestrator does not do this for
you.
