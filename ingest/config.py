"""Project configuration (config.yaml in the repository root)."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    data_folder: Path
    connection: str
    user_agent: str
    request_delay_s: float
    temp_tablespace: str | None = None


def load(path: Path | None = None) -> Config:
    path = path or Path(os.environ.get("OVK_CONFIG", REPO_ROOT / "config.yaml"))
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Config(
        data_folder=Path(raw["data_folder"]),
        # OVK_CONNECTION lets tests point the loader at a scratch database.
        connection=os.environ.get("OVK_CONNECTION", raw["connection"]),
        user_agent=raw.get(
            "user_agent", "obcevkostce/0.1 (+https://obcevkostce.ceskepriority.cz)"
        ),
        request_delay_s=float(raw.get("request_delay_s", 1.0)),
        temp_tablespace=raw.get("temp_tablespace"),
    )


def git_sha(allow_dirty: bool = False) -> str:
    """HEAD commit of the loading code. Refuses a dirty tree so that every
    release can be traced to the exact code that produced it."""
    def run(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    if run("status", "--porcelain") and not allow_dirty:
        raise SystemExit(
            "Working tree has uncommitted changes; commit before loading so the "
            "release records the exact code version (or pass --allow-dirty for tests)."
        )
    return run("rev-parse", "HEAD")
