from __future__ import annotations

from pathlib import Path


def load_env_file(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser for a local .env file (no external
    dependency). Only used for secrets (CLAUDE_CODE_OAUTH_TOKEN) that
    shouldn't live in config.yml, which is tracked in git."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env
