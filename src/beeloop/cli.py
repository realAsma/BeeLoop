"""BeeLoop command-line interface."""

from __future__ import annotations

import argparse
import sys

from .config import ConfigError, root as configured_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="beeloop")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("root", help="print the configured deployment root")
    parser.parse_args(argv)

    try:
        print(configured_root())
    except ConfigError as exc:
        print(f"beeloop: {exc}", file=sys.stderr)
        return 2
    return 0
