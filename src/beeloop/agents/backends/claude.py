"""Claude Code adapter: a new process per batch, with resumable conversation on disk.

Only the session ID persists; PIDs, output files, and transcript paths would go
stale between turns. Claude CLI flags belong here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from typing import Any, Mapping, Sequence

from .base import (
    Backend,
    BackendError,
    Delivery,
    Fault,
    InputItem,
    Session,
    render_inputs as _render,
)

# Verified against 2.1.251: an unknown session id exits 1 with exactly this.
_NO_SESSION = "No conversation found with session ID"

# Profiles map directly to CLI enums; permission ceilings remain claimed until
# demonstrated end to end.
_PROFILES = {
    "approve_for_me": "auto",
    "yolo": "bypassPermissions",
    "edit": "acceptEdits",
    "read": "plan",
}


class ClaudeBackend(Backend):
    name = "claude_code"
    # Concurrent resumes can interleave branches in one session transcript.
    delivery_mode = "serial"

    def validate_config(self, options: Mapping[str, Any]) -> None:
        _model_args(options)

    def open(self) -> str:
        """Reserve a UUID without process, network, or model work.

        Allocation races cost nothing.
        """
        return str(uuid.uuid4())

    def deliver(self, session: Session, inputs: Sequence[InputItem]) -> Delivery:
        argv = _argv(session, _render(inputs))
        done = subprocess.run(
            argv,
            cwd=str(session.cwd),
            capture_output=True,
            text=True,
            # Verified against 2.1.257: stdio MCP servers inherit this environment.
            # Bind out of band so the model cannot choose its sender identity
            # through an argument or prompt line.
            env={**os.environ, "BEELOOP_AGENT_DIR": str(session.agent_dir)},
        )
        if done.returncode != 0:
            raise BackendError((done.stderr or done.stdout).strip())
        payload = json.loads(done.stdout)
        return Delivery(
            text=payload.get("result", ""),
            updates={"session_id": payload["session_id"], "status": "active"},
            cost_usd=payload.get("total_cost_usd"),
        )

    def close(self, session: Session) -> None:
        """The provider has no retire operation; record status stops resumes.

        Leaving its transcript on disk satisfies the archival contract.
        """

    def classify(self, message: str) -> Fault:
        """Unknown failures are transient to avoid discarding live conversations.

        The caller owns the retry budget.
        """
        if _NO_SESSION in message:
            return "terminal"
        return "transient"


# -------------------------------------------------------------------- the argv


def _argv(session: Session, prompt: str) -> list[str]:
    """Rebuild command options every batch.

    Verified against 2.1.251: --append-system-prompt values disappear on --resume
    when that flag is omitted, so command options must be replayed.

    Fresh processes rediscover CLAUDE.md, skills, and MCP configuration from cwd
    and installed plugins (verified with a codeword only in discovered CLAUDE.md).
    Thus no --plugin-dir or --mcp-config flags are needed; cwd is the subprocess
    directory, not a flag. Only session, profile, and model require flags; agent
    identity travels in deliver's environment.
    """
    argv = [
        _binary(),
        "-p",
        prompt,
        "--output-format",
        "json",
        "--permission-mode",
        _PROFILES[session.permissions],
    ]
    # Reusing a live ID via --session-id silently forks its conversation.
    argv += (
        ["--session-id", session.session_id]
        if session.prepared
        else ["--resume", session.session_id]
    )
    argv += _model_args(session.options)
    return argv


def _model_args(options: Mapping[str, Any]) -> list[str]:
    if "model" not in options:
        return []
    model = options["model"]
    if not isinstance(model, str) or not model:
        raise BackendError("Claude model must be a non-empty string")
    return ["--model", model]


def _binary() -> str:
    found = os.environ.get("BEELOOP_CLAUDE_BIN") or shutil.which("claude")
    if not found:
        raise BackendError(
            "the `claude` CLI is not on PATH; install it or set BEELOOP_CLAUDE_BIN"
        )
    return found
