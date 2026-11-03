"""Provider-neutral backend contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal, Mapping, Sequence
from xml.sax.saxutils import quoteattr

DeliveryMode = Literal["serial", "merge_active"]
Fault = Literal["transient", "context_full", "terminal"]


class BackendError(RuntimeError):
    """Anything an adapter refuses or cannot make sense of."""


def validate_permissions(value: Any) -> str:
    """Validate the permission profiles shared by all BeeLoop backends."""
    profiles = ("read_only", "approve_for_me", "full_access")
    if not isinstance(value, str) or value not in profiles:
        raise BackendError(
            f"invalid permission profile {value!r}; use one of {', '.join(profiles)}"
        )
    return value


# ----------------------------------------------------------------- the bundles


@dataclass
class InputItem:
    """An event with its reply source kept separate from its content.

    The source must reach the model unchanged. The gateway compares this opaque
    reply handle for equality and never parses it.
    """

    source: str
    content: str


def render_inputs(inputs: Sequence[InputItem]) -> str:
    """Render ordered events with each source and message boundary intact."""
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


@dataclass
class Session:
    """A per-call view of the agent record and late-bound role configuration.

    Never persisted: rebuilding it each call lets role edits affect existing
    agents on their next turn and keeps adapters stateless.
    """

    agent_id: str  # record
    agent_dir: Path  # runtime/agents/<agent_id>, supplied to bound tools
    session_id: str  # record
    # Record status is the sole create/resume guard; never probe the provider.
    prepared: bool
    # Delegation fixes the worker's directory in its record. Do not infer it
    # from role defaults or the provider's last invocation.
    cwd: Path
    permissions: str  # Shared profile; adapters map it to native flags.
    options: Mapping[str, Any] = field(default_factory=dict)  # role, opaque

    def __post_init__(self) -> None:
        validate_permissions(self.permissions)


@dataclass
class Delivery:
    """Turn output and record updates for the caller to persist.

    Adapters do not own task, role, or routing state.
    """

    text: str
    updates: Mapping[str, Any] = field(default_factory=dict)
    cost_usd: float | None = None
    # Queued behind an active turn: no text, updates, or stats; no turn ran.
    parked: bool = False


# ----------------------------------------------------------------- the contract


class Backend(ABC):
    """A stateless adapter; every durable detail arrives in a Session."""

    name: ClassVar[str]
    delivery_mode: ClassVar[DeliveryMode]

    def validate_config(self, options: Mapping[str, Any]) -> None:
        """Validate adapter-specific options without opening a session."""

    @abstractmethod
    def open(self) -> str:
        """Reserve a resumable handle without a model call; allocation may be lazy.

        Persist it before spending, so paying for a response cannot precede
        knowing how to resume it.
        """

    @abstractmethod
    def deliver(self, session: Session, inputs: Sequence[InputItem]) -> Delivery:
        """Resume and accept one ORDERED batch."""

    @abstractmethod
    def close(self, session: Session) -> None:
        """Retire idempotently, preserving existing data for archival."""

    @abstractmethod
    def classify(self, message: str) -> Fault:
        """Decide what a failure means. Retry-versus-refresh is the caller's."""
