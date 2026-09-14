"""BeeLoop command-line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .agents.timers import install_adapter as install_timer_adapter
from .config import ConfigError, root as configured_root, setup_root, setup_state_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="beeloop")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup", help="configure the BeeLoop deployment")
    setup.add_argument("--root", type=Path)
    setup.add_argument("--state-dir", type=Path)
    commands.add_parser("root", help="print the configured deployment root")
    args = parser.parse_args(argv)

    if args.command == "root":
        try:
            print(configured_root())
        except ConfigError as exc:
            print(f"beeloop: {exc}", file=sys.stderr)
            return 2
        return 0

    try:
        root = setup_root(args.root)
        state_dir = setup_state_dir(args.state_dir)
        install_timer_adapter(root)
    except (ConfigError, OSError) as exc:
        print(f"beeloop: {exc}", file=sys.stderr)
        return 2
    print(f"BeeLoop root: {root}")
    print(f"BeeLoop State directory: {state_dir}")
    return 0
