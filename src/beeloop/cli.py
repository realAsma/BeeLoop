"""BeeLoop command-line interface."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from .config import ConfigError, root as configured_root, setup_root


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
        _setup_state(args.state_dir)
    except (ConfigError, RuntimeError) as exc:
        print(f"beeloop: {exc}", file=sys.stderr)
        return 2
    print(f"BeeLoop root: {root}")
    return 0


def _setup_state(state_dir: Path | None) -> None:
    executable = shutil.which("beeloop-state")
    if executable is None:
        raise RuntimeError(
            "`beeloop-state` is required; install BeeLoop State and ensure its "
            "executable is on PATH"
        )
    command = [executable, "setup"]
    if state_dir is not None:
        command += ["--state-dir", str(state_dir)]
    done = subprocess.run(command, capture_output=True, text=True)
    if done.returncode:
        detail = (done.stderr or done.stdout).strip()
        raise RuntimeError(f"BeeLoop State setup failed: {detail}")
