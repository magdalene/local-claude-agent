"""Container entrypoint. Installs the repo's dependencies into this throwaway
container, builds a task-specific prompt, and runs one non-interactive Claude
Code turn. Everything it needs comes in as env vars, set by
orchestrator/docker_run.py -- see that file for what each one means.

Exit code is what orchestrator/main.py uses to decide whether the run
succeeded, alongside checking the issue file's status actually changed as
expected -- see main.py's run_task().
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

ALLOWED_TOOLS = ','.join([
    'Read', 'Edit', 'Write', 'Grep', 'Glob',
    'Bash(git add:*)', 'Bash(git commit:*)', 'Bash(git status:*)',
    'Bash(git diff:*)', 'Bash(git log:*)', 'Bash(git show:*)', 'Bash(git rebase:*)',
    'Bash(git rm:*)', 'Bash(git mv:*)', 'Bash(rm:*)', 'Bash(mv:*)',
    # Read-only git plumbing the agent reaches for while untangling a rebase.
    'Bash(git ls-tree:*)', 'Bash(git ls-files:*)', 'Bash(git rev-parse:*)',
    'Bash(git merge-base:*)', 'Bash(git merge-tree:*)', 'Bash(git diff-index:*)',
    'Bash(git hash-object:*)', 'Bash(git check-attr:*)',
    'Bash(git update-index --really-refresh)', 'Bash(git remote -v)', 'Bash(git --version)',
    # Git writes that stay inside this worktree: its own index, working tree,
    # and branch. Only `--soft` reset -- it moves this branch's HEAD and keeps
    # the changes staged, so no work is lost; `--hard`/`--mixed` stay unlisted
    # (and so denied, since -p has no one to approve them). Not `git stash`:
    # refs/stash is shared across every worktree, so the agent's `stash pop`
    # can pop a human's stash from the primary checkout.
    'Bash(git merge:*)', 'Bash(git restore:*)',
    'Bash(git apply:*)', 'Bash(git revert:*)', 'Bash(git reset --soft:*)',
    # Test/lint tooling for the example backend (Python/Django) and frontend
    # (npm) repos -- edit to match your own stack.
    'Bash(pytest)', 'Bash(pytest:*)', 'Bash(python -m pytest:*)', 'Bash(python3 -m pytest:*)',
    'Bash(python manage.py:*)', 'Bash(python3 manage.py:*)', 'Bash(ruff:*)', 'Bash(semgrep:*)',
    'Bash(npm run:*)', 'Bash(npm test:*)', 'Bash(npx:*)',
    'Bash(ls:*)', 'Bash(wc:*)', 'Bash(echo:*)',
    'Bash(cat:*)', 'Bash(head:*)', 'Bash(tail:*)', 'Bash(grep:*)', 'Bash(sort:*)',
    'Bash(which:*)', 'Bash(printenv:*)',
])
# Deleting/moving files (rm, git rm, mv, git mv) is allowed -- the container
# is a throwaway `--rm` process on the agent's own worktree, so there's no
# reason to withhold it, and it's needed for things like dropping a
# superseded migration file during a rebase (see issue #14's code review
# reply for what it looks like when this is missing).
# git push / checkout / hard reset stay out on purpose -- see the top-level
# AGENT_RULES.md "Where you are" section. Deny rules beat allow rules, so
# `git reset` is denied flag-by-flag rather than wholesale, or the
# `git reset --soft` allow above would never match. Refs (branches, tags,
# `main`) are shared with the primary checkout across every worktree, so
# git fetch/branch/tag/update-ref/worktree are deliberately left unlisted
# too. Task/Agent are out because a run that delegates and waits for a
# subagent never resumes here; see AGENT_RULES.md "Finish inside this one turn".
DISALLOWED_TOOLS = ','.join([
    'Bash(git push:*)', 'Bash(git checkout:*)',
    'Bash(git reset --hard:*)', 'Bash(git reset --mixed:*)', 'Bash(git reset --merge:*)',
    'Bash(git reset --keep:*)', 'Task', 'Agent',
])

PROMPTS = {
    'plan': """\
Read {general_rules} and {repo_rules} and follow them both.

This is a PLAN-ONLY run for issue #{number}, at {issue_file}. Do NOT write \
code or commit anything.

Read the issue file, the repo's CLAUDE.md, and whatever source you need. \
Then append a `## Plan` section to the issue file: which files you'll \
change, the approach, the tests you'll add, and any assumptions you're \
making.

Set `status: needs-plan-review` in the frontmatter when done. If the ticket \
is too ambiguous to plan, follow the escalation rule in {general_rules} \
instead of guessing.
""",
    'implement': """\
Read {general_rules} and {repo_rules} and follow them both.

Implement issue #{number} (file at {issue_file}) on branch {branch} in \
{worktree_path}. Dependencies are already installed.

1. Read the issue file -- including its `## Plan` section if one exists -- \
   and the repo's CLAUDE.md. No plan section means this ticket didn't \
   require one; work from the description directly.
2. Implement the change and satisfy every item in the definition of done in \
   {repo_rules}.
3. Commit your work (a git identity is already configured in this worktree).
4. Append an `## Implementation notes` section to the issue file: what you \
   did, and anything you deliberately left out or decided by judgement.
5. Set `status: needs-code-review` in the frontmatter.

If you get stuck, follow the escalation rule in {general_rules} instead of \
guessing.
""",
    'plan_revision': """\
Read {general_rules} and {repo_rules} and follow them both.

Issue #{number} ({issue_file}) has plan feedback waiting -- a human left \
comments and sent it back for changes. Read the feedback, then append a \
*revised* `## Plan` section that addresses it directly. Say briefly what you \
changed and why; don't restate the original plan with minor edits.

Set `status: needs-plan-review` when done.
""",
    'code_revision': """\
Read {general_rules} and {repo_rules} and follow them both.

Issue #{number} ({issue_file}), branch {branch} in {worktree_path}, has code \
review feedback waiting. Follow the "Revision rounds" section of \
{general_rules}: rebase onto the repo's default branch first, then address \
every point in the feedback with a code change or a reply in the issue file \
explaining why you disagree.

Re-check the definition of done in {repo_rules}, commit, and set \
`status: needs-code-review` when done.
""",
}


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f'missing required env var {name}', file=sys.stderr)
        sys.exit(1)
    return value


def render_event(event: dict) -> str | None:
    """One short human-readable line per stream-json event, or None to skip
    it. `-p`'s default text output format only prints the final result --
    nothing until the whole run finishes, which makes a mid-flight run
    indistinguishable from a hung one in the log. --output-format stream-json
    emits one JSON object per turn as it happens instead; this renders each
    into something worth tailing. Event shape matches what the reference
    GitHub Action's own diagnostics step parses with jq (system/assistant/
    user/result top-level types)."""
    kind = event.get('type')

    if kind == 'system' and event.get('subtype') == 'init':
        return f'[session] model={event.get("model")} cwd={event.get("cwd")}'

    if kind == 'assistant':
        lines = []
        for block in event.get('message', {}).get('content', []):
            if block.get('type') == 'text' and block.get('text', '').strip():
                lines.append(f'[assistant] {block["text"].strip()}')
            elif block.get('type') == 'tool_use':
                arg_preview = json.dumps(block.get('input', {}))[:200]
                lines.append(f'[tool] {block.get("name")} {arg_preview}')
        return '\n'.join(lines) or None

    if kind == 'user':
        for block in event.get('message', {}).get('content', []):
            if block.get('type') == 'tool_result':
                content = block.get('content')
                text = content if isinstance(content, str) else json.dumps(content)
                tag = 'tool-error' if block.get('is_error') else 'tool-result'
                return f'[{tag}] {text[:300]}'
        return None

    if kind == 'result':
        return (
            f'[result] {event.get("subtype")} turns={event.get("num_turns")} '
            f'cost=${event.get("total_cost_usd", 0):.4f} '
            f'duration={event.get("duration_ms", 0) // 1000}s'
        )

    return None


def main() -> int:
    task_kind = env('TASK_KIND')
    issue_file = env('ISSUE_FILE')
    worktree_path = env('WORKTREE_PATH')
    branch = env('BRANCH')
    general_rules = env('GENERAL_RULES')
    repo_rules = env('REPO_RULES')
    install_cmd = env('INSTALL_CMD')
    max_turns = env('MAX_TURNS')
    # Issue number is embedded in the filename's leading digits; the prompt
    # only uses it for readability, so a cheap parse is fine here.
    number = os.path.basename(issue_file).split('-', 1)[0]

    if task_kind not in PROMPTS:
        print(f'unknown TASK_KIND {task_kind!r}', file=sys.stderr)
        return 1

    print(f'--- installing dependencies: {install_cmd}')
    install = subprocess.run(install_cmd, shell=True, cwd=worktree_path)
    if install.returncode != 0:
        print(f'dependency install failed (exit {install.returncode})', file=sys.stderr)
        return install.returncode

    prompt = PROMPTS[task_kind].format(
        general_rules=general_rules,
        repo_rules=repo_rules,
        issue_file=issue_file,
        branch=branch,
        worktree_path=worktree_path,
        number=number,
    )

    print(f'--- running claude ({task_kind}, max {max_turns} turns)')
    proc = subprocess.Popen(
        [
            'claude', '-p', prompt,
            # stream-json is what actually gets us per-turn output as it
            # happens; --verbose is required alongside it. See render_event()
            # for why plain --verbose on its own doesn't do this.
            '--output-format', 'stream-json',
            '--verbose',
            '--allowedTools', ALLOWED_TOOLS,
            # Container-local scratch space (the container is --rm), so the
            # agent can stash a file's old version or a patch outside the
            # worktree instead of littering it with _tmp files.
            '--add-dir', '/tmp',
            # Without this, Bash `>` redirects and rm/touch are refused even
            # inside the worktree. It only covers the worktree and /tmp --
            # writes anywhere else are still blocked.
            '--permission-mode', 'acceptEdits',
            '--disallowedTools', DISALLOWED_TOOLS,
            '--max-turns', max_turns,
        ],
        cwd=worktree_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            print(line)  # not a JSON event -- pass it through rather than drop it
            continue
        rendered = render_event(event)
        if rendered:
            print(rendered)
    return proc.wait()


if __name__ == '__main__':
    sys.exit(main())
