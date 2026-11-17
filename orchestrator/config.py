from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / 'config.yml'


@dataclass
class RepoConfig:
    area: str
    path: Path
    worktrees_dir: Path
    agent_rules: Path
    default_branch: str
    install_cmd: str
    env: dict[str, str]


@dataclass
class Config:
    workspace_root: Path
    repos: dict[str, RepoConfig]
    issues_dir: Path
    general_rules: Path
    lock_file: Path
    log_dir: Path
    docker_image: str
    max_attempts: int
    max_turns: int
    container_timeout_minutes: int
    branch_prefix: str


def load_config(path: Path = CONFIG_PATH) -> Config:
    raw = yaml.safe_load(path.read_text())

    repos = {}
    for area, r in raw['repos'].items():
        repos[area] = RepoConfig(
            area=area,
            path=Path(r['path']),
            worktrees_dir=Path(r['worktrees_dir']),
            agent_rules=Path(r['agent_rules']),
            default_branch=r['default_branch'],
            install_cmd=r['install_cmd'],
            env={k: str(v) for k, v in (r.get('env') or {}).items()},
        )

    return Config(
        workspace_root=Path(raw['workspace_root']),
        repos=repos,
        issues_dir=Path(raw['issues_dir']),
        general_rules=Path(raw['general_rules']),
        lock_file=Path(raw['lock_file']),
        log_dir=Path(raw['log_dir']),
        docker_image=raw['docker_image'],
        max_attempts=int(raw['max_attempts']),
        max_turns=int(raw['max_turns']),
        container_timeout_minutes=int(raw['container_timeout_minutes']),
        branch_prefix=raw['branch_prefix'],
    )
