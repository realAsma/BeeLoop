"""Provider-free backend for lock, drain, and hydration tests without model cost.

Batches are logged to disk because tests cross process boundaries.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Sequence

from ..records import root
from .base import Backend, BackendError, Delivery, Fault, InputItem, Session


class FakeBackend(Backend):
    name = "fake"
    delivery_mode = "serial"

    def open(self) -> str:
        return str(uuid.uuid4())

    def deliver(self, session: Session, inputs: Sequence[InputItem]) -> Delivery:
        if not inputs:
            raise BackendError("nothing to deliver; an empty batch is a caller bug")
        if fail := _failure(session.session_id):
            raise BackendError(fail)
        _log(session, [(item.source, item.content) for item in inputs])
        return Delivery(
            text=" | ".join(item.content for item in inputs),
            updates={"session_id": session.session_id, "status": "active"},
            cost_usd=0.0,
        )

    def close(self, session: Session) -> None:
        return None

    def classify(self, message: str) -> Fault:
        if "terminal" in message:
            return "terminal"
        if "context" in message:
            return "context_full"
        return "transient"


def log_path(agent_id: str) -> Path:
    """Use the shared root resolver so logs target the same deployment."""
    return root() / "runtime" / "fake" / f"{agent_id}.jsonl"


def turns(agent_id: str) -> list[list[list[str]]]:
    """Every batch this backend was handed, in order, one row per turn."""
    path = log_path(agent_id)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]


def fail_session(session_id: str, message: str) -> None:
    """Fail a session until replaced; a fresh session ID escapes the failure."""
    path = log_path(f"fail-{session_id}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(message, encoding="utf-8")


def _failure(session_id: str) -> str | None:
    path = log_path(f"fail-{session_id}")
    return path.read_text("utf-8") if path.exists() else None


def _log(session: Session, batch: list[tuple[str, str]]) -> None:
    path = log_path(session.agent_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps([list(pair) for pair in batch]) + "\n")
