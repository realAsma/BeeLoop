"""Source-owned replies outside an agent's shell sandbox."""

from __future__ import annotations

import os
import re
import subprocess

from beeloop.config import root


class InputError(RuntimeError):
    pass


def reply(source: str, text: str) -> str:
    """Pass a reply to the configured source helper with BeeLoop's interpreter."""
    name, separator, session = source.partition(":")
    if not separator or not session or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        raise InputError("source must be <source-name>:<session-key>")
    if not text.strip():
        raise InputError("reply text must not be empty")
    runtime = root() / "runtime"
    sources = runtime / "sources"
    helper = (sources / name / name).resolve()
    if not helper.is_relative_to(sources.resolve()) or not helper.is_file():
        raise InputError(f"no reply helper for source {name!r}")
    try:
        python = (runtime / "python").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise InputError("start the BeeLoop gateway to configure its Python interpreter") from exc
    if not python or not os.path.isabs(python):
        raise InputError("invalid gateway Python binding; restart the BeeLoop gateway")
    try:
        result = subprocess.run(
            [str(helper), "reply", "--source", source, "--text", "-"],
            input=text,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "BEELOOP_PYTHON": python},
        )
    except subprocess.TimeoutExpired as exc:
        raise InputError("reply timed out; delivery is unknown, check before retrying") from exc
    except OSError as exc:
        raise InputError(f"cannot run reply helper for {name!r}: {exc}") from exc
    if result.returncode:
        raise InputError(f"reply helper for {name!r} failed: {result.stderr.strip()}")
    return result.stdout.strip()
