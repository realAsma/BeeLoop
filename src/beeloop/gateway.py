"""Poll input adapters and dispatch their envelopes."""

from __future__ import annotations

import datetime as dt
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path


class GatewayError(RuntimeError):
    pass


INTERVAL_ENV = "BEELOOP_INPUT_INTERVAL"
_MESSAGE = re.compile(rb"(?:^|\n)msg=(.*)\Z", re.DOTALL)


def run(root: Path) -> int:
    """Run the gateway until interrupted."""
    interval = _interval()
    inputs = root / "inputs.d"
    logs = root / "logs"
    inputs.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    _log(
        logs / "loop.log",
        f"started; root={root} interval={interval:g}s "
        "dispatch=beeloop.dispatch.dispatch",
    )

    try:
        while True:
            poll(root)
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def poll(root: Path) -> None:
    """Poll each enabled input adapter once."""
    inputs = root / "inputs.d"
    logs = root / "logs"
    for adapter in sorted(inputs.iterdir()):
        if not adapter.is_file() or not os.access(adapter, os.X_OK):
            continue

        envelope = _run_adapter(adapter, root, logs / "inputs.log")
        if envelope is None:
            _log(logs / "loop.log", f"input failed: {adapter.name}")
            continue
        if not envelope or not _message_body(envelope).strip():
            continue

        _log(logs / "loop.log", f"input fired: {adapter.name}")
        _dispatch(envelope, root, logs / "dispatch.log")


def _interval() -> float:
    raw = os.environ.get(INTERVAL_ENV, "1")
    try:
        interval = float(raw)
    except ValueError:
        raise GatewayError(f"{INTERVAL_ENV} must be a positive number of seconds") from None
    if not math.isfinite(interval) or interval <= 0:
        raise GatewayError(f"{INTERVAL_ENV} must be a positive number of seconds")
    return interval


def _run_adapter(adapter: Path, root: Path, log_path: Path) -> bytes | None:
    with open(log_path, "ab", buffering=0) as log:
        try:
            completed = subprocess.run(
                [str(adapter)],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=log,
                check=False,
            )
        except OSError as exc:
            log.write(f"{adapter.name}: {exc}\n".encode())
            return None
    if completed.returncode:
        return None
    return completed.stdout.rstrip(b"\n")


def _message_body(envelope: bytes) -> bytes:
    match = _MESSAGE.search(envelope)
    return match.group(1) if match else b""


def _dispatch(envelope: bytes, root: Path, log_path: Path) -> None:
    with open(log_path, "ab", buffering=0) as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "beeloop.dispatch.dispatch"],
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        assert process.stdin is not None
        try:
            process.stdin.write(envelope)
            process.stdin.close()
        except BaseException:
            process.kill()
            raise


def _log(path: Path, message: str) -> None:
    stamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    line = f"[{stamp}] loop: {message}\n"
    with path.open("a", encoding="utf-8") as log:
        log.write(line)
    print(line, end="", file=sys.stderr, flush=True)
