"""Run prepared runtime scripts outside an agent's shell sandbox."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from beeloop.config import root


class ScriptError(RuntimeError):
    pass


def run(script: str, args: list[str] | None = None, stdin: str | None = None) -> dict[str, str | int]:
    """Run a runtime script with literal arguments and the MCP server environment."""
    deployment = root()
    runtime = (deployment / "runtime").resolve()
    relative = Path(script)
    if ("\0" in script or relative.is_absolute() or ".." in relative.parts
            or not relative.parts or relative.parts[0] not in {"scripts", "sources"}):
        raise ScriptError("script must be relative to runtime/scripts or runtime/sources")
    target = (runtime / relative).resolve()
    if (not any(target.is_relative_to(runtime / name) for name in ("scripts", "sources"))
            or not target.is_file()):
        raise ScriptError("script must be a file inside runtime/scripts or runtime/sources")
    command = [sys.executable, str(target)] if target.suffix == ".py" else [str(target)]
    try:
        result = subprocess.run(
            [*command, *(args or [])],
            input=stdin if stdin is not None else "",
            capture_output=True,
            text=True,
            cwd=deployment,
            timeout=30,
            env={
                **os.environ,
                "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
            },
        )
    except subprocess.TimeoutExpired as exc:
        raise ScriptError("script timed out; outcome is unknown, check before retrying") from exc
    except (OSError, ValueError) as exc:
        raise ScriptError(f"cannot run script {script!r}: {exc}") from exc
    return {"exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
