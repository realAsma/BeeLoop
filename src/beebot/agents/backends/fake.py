"""A backend that talks to nothing.

Not scaffolding. With one implementation, "the contract is expressible without
naming a provider" is a claim nobody has tested, and every lock, drain and
hydration test would otherwise have to spend tokens to run.

It records what it was asked, in a file rather than in memory, because the tests
that matter most run it from more than one process.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Sequence

from .._agent import root
from .base import Backend, BackendError, Delivery, Fault, InputItem, Session


class FakeBackend(Backend):
    name = "fake"
    delivery_mode = "serial"

    def open(self) -> str:
        return str(uuid.uuid4())

    def deliver(self, session: Session, inputs: Sequence[InputItem]) -> Delivery:
        if not inputs:
            raise BackendError("nothing to deliver; an empty batch is a caller bug")
        # A session told to fail keeps failing until it is replaced, which is
        # what a lost session actually looks like.
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
    """Where this backend writes what it was handed.

    Through `root()` rather than reading BEEBOT_ROOT here, even though that
    costs this package its leaf status: `root()` is where the variable is
    checked, and a second reader means the fake would happily log into an
    unset or bogus root that every other path in the tree refuses.
    """
    return root() / "runtime" / "fake" / f"{agent_id}.jsonl"


def turns(agent_id: str) -> list[list[list[str]]]:
    """Every batch this backend was handed, in order, one row per turn."""
    path = log_path(agent_id)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]


def fail_session(session_id: str, message: str) -> None:
    """Make one session fail. Keyed on the session rather than the agent, so a
    refresh that mints a new id escapes it -- exactly like a real dead one."""
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
