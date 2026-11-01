"""BeeLoop command-line interface."""

from __future__ import annotations

import argparse
import sys

from .config import ConfigError, root as configured_root
from .gateway import GatewayError, run as run_gateway


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="beeloop",
        description="Run the BeeLoop gateway when no command is given.",
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("root", help="print the configured deployment root")
    args = parser.parse_args(argv)

    try:
        root = configured_root()
        if args.command == "root":
            print(root)
            return 0
        return run_gateway(root)
    except (ConfigError, GatewayError, OSError) as exc:
        print(f"beeloop: {exc}", file=sys.stderr)
        return 2
