"""Dispatch of `python -m ingest czso ...` commands."""

from __future__ import annotations

import json
from datetime import datetime

from ..config import Config, git_sha
from ..db import connect
from .client import Client
from .download import latest_release_dir, load_datasets


def _ordered(codes: list[str] | None) -> list[str]:
    """Configured datasets, highest-priority tier first."""
    cfg = load_datasets()
    unknown = set(codes or []) - set(cfg["datasets"])
    if unknown:
        raise SystemExit(f"Not in datasets.yaml: {sorted(unknown)}")
    wanted = codes or list(cfg["datasets"])
    return sorted(wanted, key=lambda c: cfg["tiers"][cfg["datasets"][c]["tier"]])


def _release_dirs(cfg: Config, codes: list[str] | None) -> list:
    out = []
    for code in _ordered(codes):
        directory = latest_release_dir(cfg.data_folder, code)
        if directory is None:
            print(f"{code}: not downloaded, skipping")
        else:
            out.append(directory)
    return out


def _write_report(cfg: Config, name: str, payload) -> None:
    directory = cfg.data_folder / "czso" / "_reports"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{datetime.now():%Y%m%dT%H%M%S}_{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"report: {path}")


def run(args, cfg: Config) -> int:
    cmd = args.command
    if cmd == "download":
        from .download import download
        client = Client(cfg.user_agent, cfg.request_delay_s)
        try:
            download(client, cfg.data_folder, _ordered(args.dataset))
        finally:
            print(f"{client.requests} requests")
            client.close()
        return 0

    if cmd == "geography":
        from . import geography
        client = Client(cfg.user_agent, cfg.request_delay_s)
        try:
            directory = geography.fetch(client, cfg.data_folder)
        finally:
            client.close()
        with connect(cfg) as conn:
            geography.load(conn, directory)
        return 0

    if cmd == "indicators":
        from . import indicators
        with connect(cfg) as conn:
            indicators.sync(conn, cfg.data_folder)
        return 0

    if cmd == "load":
        from .load import load_release
        sha = git_sha(allow_dirty=args.allow_dirty)
        reports = []
        with connect(cfg) as conn:
            for directory in _release_dirs(cfg, args.dataset):
                report = load_release(conn, directory, sha)
                if report:
                    reports.append(report)
            if reports:
                conn.execute("SELECT refresh_current_observation(false)")
                conn.commit()
                print("current_observation refreshed")
        if reports:
            _write_report(cfg, "load", reports)
        return 0

    if cmd == "refresh":
        with connect(cfg) as conn:
            conn.execute("SELECT refresh_current_observation()")
        return 0

    if cmd == "verify":
        from .verify import verify
        client = Client(cfg.user_agent, cfg.request_delay_s)
        try:
            with connect(cfg) as conn:
                results, ok = verify(conn, client, _release_dirs(cfg, args.dataset), args.spot)
        finally:
            client.close()
        _write_report(cfg, "verify", results)
        return 0 if ok else 1

    if cmd == "check-aggregation":
        from .aggregation import check
        with connect(cfg) as conn:
            results = check(conn, _release_dirs(cfg, args.dataset))
        _write_report(cfg, "aggregation", results)
        return 0

    raise SystemExit(f"unknown command {cmd!r}")
