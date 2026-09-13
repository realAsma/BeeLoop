"""The Claude Code adapter: a subprocess wrapper around one durable field.

No daemon, no socket, no protocol. The process exits after every batch while the
conversation stays resumable on disk, so the whole backend is the session id.
Nothing else is stored -- a PID, an output file or a transcript path would all
go stale between ticks.

This is the only file in the tree that may contain a CLI flag.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from typing import Sequence
from xml.sax.saxutils import quoteattr

from .base import (
    Backend,
    BackendError,
    Delivery,
    Fault,
    InputItem,
    Session,
)

# Verified against 2.1.251: an unknown session id exits 1 with exactly this.
_NO_SESSION = "No conversation found with session ID"

# A named profile compiles to the CLI's fixed enum. There is no profile-name
# indirection in the tool itself, so the catalog is adapter data -- and until it
# has been demonstrated end to end, the ceiling it describes is CLAIMED, not
# enforced.
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

    def open(self) -> str:
        """Mint the id the first invocation will use. Spawns nothing, spends
        nothing, contacts nothing -- so losing a race to allocate costs zero."""
        return str(uuid.uuid4())

    def deliver(self, session: Session, inputs: Sequence[InputItem]) -> Delivery:
        argv = _argv(session, _render(inputs))
        done = subprocess.run(
            argv,
            cwd=str(session.cwd),
            capture_output=True,
            text=True,
            # The binding, and the only thing this backend tells the plugin.
            # Verified against 2.1.257: the CLI passes its own environment down
            # to stdio MCP servers, so the loop server reads this binding.
            # Out-of-band on purpose -- an
            # argument or a prompt line would let the model inside choose whose
            # identity it sends under.
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
        """Idempotent by being nothing.

        The provider has no retire operation. The record's status is what stops
        resumes, and leaving the transcript on disk IS the archival behaviour the
        contract asks for.
        """

    def classify(self, message: str) -> Fault:
        """Unknown text means `transient` deliberately.

        An unrecognized failure is far more likely to be a blip than a dead
        session, and calling it `terminal` throws away a live conversation. The
        retry budget belongs to the caller, not here.
        """
        if _NO_SESSION in message:
            return "terminal"
        return "transient"


# -------------------------------------------------------------------- the argv


def _argv(session: Session, prompt: str) -> list[str]:
    """The command line, rebuilt in full on every batch.

    There is deliberately no "the first call carries the config" branch. Verified
    against 2.1.251: a secret placed in --append-system-prompt at create is
    unknown after a --resume that omits the flag, so anything passed on the
    command line has to be passed again, forever.

    What survives is DISCOVERY. The process is new every batch and reads
    CLAUDE.md, skills and MCP config out of `cwd` and the installed plugins each
    time -- verified the same way, with a codeword that was only ever in a
    discovered CLAUDE.md. So this passes none of that: no `--plugin-dir` and no
    `--mcp-config`, because the loop plugin is installed rather than handed over
    per invocation, and `cwd` is the subprocess's, never a flag. What is left is
    the session, the profile and the model -- the three things the directory
    cannot know. The per-agent binding the plugin needs travels in the
    environment `deliver` builds, not here.
    """
    mode = _PROFILES.get(session.permissions)
    if mode is None:
        raise BackendError(
            f"permission profile {session.permissions!r} is not in this "
            f"backend's catalog; use one of "
            f"{', '.join(sorted(_PROFILES))}, or add it to _PROFILES."
        )

    argv = [
        _binary(),
        "-p",
        prompt,
        "--output-format",
        "json",
        "--permission-mode",
        mode,
    ]
    # From the record, never by probing the provider: create and resume are not
    # interchangeable, and reusing a live id forks the conversation silently.
    argv += (
        ["--session-id", session.session_id]
        if session.prepared
        else ["--resume", session.session_id]
    )
    if model := session.options.get("model"):
        argv += ["--model", str(model)]
    return argv


def _binary() -> str:
    found = os.environ.get("BEELOOP_CLAUDE_BIN") or shutil.which("claude")
    if not found:
        raise BackendError(
            "the `claude` CLI is not on PATH; install it or set BEELOOP_CLAUDE_BIN"
        )
    return found


def _render(inputs: Sequence[InputItem]) -> str:
    """One prompt, N items, each keeping its source and its boundary.

    A batch is several things that happened, not one long message. Collapsing
    them would lose which source caused what, and `source` is the only address an
    agent has for a reply.
    """
    if not inputs:
        raise BackendError("nothing to deliver; an empty batch is a caller bug")

    framed = "\n".join(
        f"<input source={quoteattr(item.source)}>\n{item.content}\n</input>"
        for item in inputs
    )
    if len(inputs) == 1:
        return framed
    return (
        f"{len(inputs)} inputs arrived, in the order they were received.\n\n"
        f"{framed}"
    )
