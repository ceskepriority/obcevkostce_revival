"""Command line: python -m ingest czso <command> [options]"""

from __future__ import annotations

import argparse
import sys

from . import config as config_mod


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ingest")
    sources = parser.add_subparsers(dest="source", required=True)
    czso = sources.add_parser("czso", help="Czech Statistical Office (DataStat)")
    cmds = czso.add_subparsers(dest="command", required=True)

    def add(name: str, help: str) -> argparse.ArgumentParser:
        p = cmds.add_parser(name, help=help)
        p.add_argument("--dataset", action="append", metavar="CODE",
                       help="limit to this dataset (repeatable); default: all configured")
        return p

    add("download", "download raw data to the data folder")
    add("geography", "load municipalities and their groups")
    add("indicators", "upsert indicators with their aggregation rules")
    p = add("load", "parse downloaded releases into the database")
    p.add_argument("--allow-dirty", action="store_true", help="tests only: load from uncommitted code")
    p = add("verify", "check that the database matches the raw files")
    p.add_argument("--spot", type=int, default=30, help="values per dataset to re-fetch from the API")
    add("check-aggregation", "compare aggregated municipalities with ČSÚ's SO ORP figures")
    cmds.add_parser("refresh", help="refresh the current_observation view")

    args = parser.parse_args(argv)
    cfg = config_mod.load()
    from .czso import commands
    return commands.run(args, cfg) or 0


if __name__ == "__main__":
    sys.exit(main())
