"""The Codex CLI adapter: one non-interactive process per turn."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from typing import Any, Sequence
from xml.sax.saxutils import quoteattr

from .base import Backend, BackendError, Delivery, Fault, InputItem, Session

_TERMINAL_SESSION_MARKERS = (
    "session not found:",
    "thread not found:",
)
_ARCHIVED_SESSION = re.compile(
    r"\b(?:session|thread)(?:\s+\S+)?\s+is\s+(?:already\s+)?archived\b"
)
_MISSING_ROLLOUT = re.compile(r"\bno rollout found for thread id \S+\b")
_PROFILES = {
    "approve_for_me": ["--approve-for-me"],
    "edit": ["--sandbox", "workspace-write"],
    "read": ["--sandbox", "read-only"],
    "yolo": ["--dangerously-bypass-approvals-and-sandbox"],
}


class CodexBackend(Backend):
    name = "codex"
    delivery_mode = "serial"

    def open(self) -> str:
        """Reserve a placeholder until the first exec call returns its thread id."""
        return str(uuid.uuid4())

    def deliver(self, session: Session, inputs: Sequence[InputItem]) -> Delivery:
        done = subprocess.run(
            _argv(session, _render(inputs)),
            cwd=str(session.cwd),
            capture_output=True,
            text=True,
            env={**os.environ, "BEEBOT_AGENT_DIR": str(session.agent_dir)},
        )
        if done.returncode != 0:
            message = "\n".join(
                part.strip() for part in (done.stderr, done.stdout) if part.strip()
            )
            raise BackendError(message)

        thread_id, text = _result(done.stdout)
        return Delivery(
            text=text,
            updates={"session_id": thread_id, "status": "active"},
        )

    def close(self, session: Session) -> None:
        """Archive a delivered thread; a prepared session is only a placeholder."""
        if session.prepared:
            return
        done = subprocess.run(
            [_binary(), "archive", session.session_id],
            capture_output=True,
            text=True,
        )
        if done.returncode == 0:
            return
        message = "\n".join(
            part.strip() for part in (done.stderr, done.stdout) if part.strip()
        )
        if _session_unavailable(message):
            return
        raise BackendError(message or "codex archive failed")

    def classify(self, message: str) -> Fault:
        if _session_unavailable(message):
            return "terminal"
        if "context window" in message.lower():
            return "context_full"
        return "transient"


def _session_unavailable(message: str) -> bool:
    normalized = " ".join(message.casefold().split())
    return any(
        marker in normalized for marker in _TERMINAL_SESSION_MARKERS
    ) or bool(
        _ARCHIVED_SESSION.search(normalized) or _MISSING_ROLLOUT.search(normalized)
    )


def _argv(session: Session, prompt: str) -> list[str]:
    profile = _PROFILES.get(session.permissions)
    if profile is None:
        raise BackendError(
            f"permission profile {session.permissions!r} is not in this "
            f"backend's catalog; use one of "
            f"{', '.join(sorted(_PROFILES))}, or add it to _PROFILES."
        )

    argv = [_binary(), "exec", "--json", "--skip-git-repo-check", *profile]
    if model := session.options.get("model"):
        argv += ["--model", str(model)]
    if not session.prepared:
        argv += ["resume", session.session_id]
    argv.append(prompt)
    return argv


def _binary() -> str:
    found = os.environ.get("BEEBOT_CODEX_BIN") or shutil.which("codex")
    if not found:
        raise BackendError(
            "the `codex` CLI is not on PATH; install it or set BEEBOT_CODEX_BIN"
        )
    return found


def _result(output: str) -> tuple[str, str]:
    thread_id: str | None = None
    text = ""
    for line in output.splitlines():
        try:
            event: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BackendError(f"codex emitted invalid JSONL: {exc}") from exc

        kind = event.get("type")
        if kind == "thread.started":
            thread_id = event.get("thread_id")
        elif kind == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message":
                text = item.get("text", "")
        elif kind == "turn.failed":
            message = event.get("error", {}).get("message", "Codex turn failed")
            raise BackendError(message)
        elif kind == "error":
            raise BackendError(event.get("message", "Codex failed"))

    if not thread_id:
        raise BackendError("codex output did not include a thread.started event")
    return thread_id, text


def _render(inputs: Sequence[InputItem]) -> str:
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
