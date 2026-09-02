"""Provider-neutral backend contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal, Mapping, Sequence

DeliveryMode = Literal["serial", "merge_active"]
Fault = Literal["transient", "context_full", "terminal"]


class BackendError(RuntimeError):
    """Anything an adapter refuses or cannot make sense of."""


# ----------------------------------------------------------------- the bundles


@dataclass
class InputItem:
    """One thing that happened.

    A pair rather than a string because `source` must reach the MODEL. It is the
    only handle an agent has on where to reply, and a batch is exactly where two
    unrelated events get conflated into one. The gateway compares it for
    equality and never parses it.
    """

    source: str
    content: str


@dataclass
class Session:
    """Everything one call needs, so an adapter can hold nothing between calls.

    NEVER PERSISTED. Rebuilt per call from two places with different lifetimes:
    the mutable agent record, and the static role. Role fields are late-bound
    deliberately -- snapshotting them into the record would mean editing a role
    only affected agents created afterwards, and the whole point of a role being
    a directory is that a human edits it and the next turn picks it up.
    """

    agent_id: str  # record
    agent_dir: Path  # runtime/agents/<agent_id>, supplied to bound tools
    session_id: str  # record
    # From the record's status. The ONLY create-versus-resume guard there is:
    # an adapter must decide from this and never by probing the provider.
    prepared: bool
    # From the record, NOT the role. A worker's directory comes from its
    # delegation, and a provider may adopt whatever directory it was last
    # invoked from -- so this is BeeBot's state, not something to infer back.
    cwd: Path
    permissions: str  # role. A profile NAME; the adapter owns the catalog.
    options: Mapping[str, Any] = field(default_factory=dict)  # role, opaque


@dataclass
class Delivery:
    """What one turn produced.

    `updates` are record fields for the CALLER to apply. An adapter that wrote
    the record itself would have to know about tasks, roles and routing, which
    is exactly the knowledge this contract exists to withhold from it.
    """

    text: str
    updates: Mapping[str, Any] = field(default_factory=dict)
    cost_usd: float | None = None
    # Queued behind a turn already in flight: no text, no updates, and no stats,
    # because it was not a turn.
    parked: bool = False


# ----------------------------------------------------------------- the contract


class Backend(ABC):
    """A stateless adapter; every durable detail arrives in a Session."""

    name: ClassVar[str]
    delivery_mode: ClassVar[DeliveryMode]

    @abstractmethod
    def open(self) -> str:
        """Allocate a resumable handle WITHOUT a model call, and return it.

        May be lazy: reserving the id the first invocation will use is a
        complete implementation. Either way the handle is persisted before
        anything is spent, which closes the window between paying for a
        response and learning how to resume it.
        """

    @abstractmethod
    def deliver(self, session: Session, inputs: Sequence[InputItem]) -> Delivery:
        """Resume and accept one ORDERED batch."""

    @abstractmethod
    def close(self, session: Session) -> None:
        """Retire. Idempotent, and archival -- nothing already written is
        deleted."""

    @abstractmethod
    def classify(self, message: str) -> Fault:
        """Decide what a failure means. Retry-versus-refresh is the caller's."""
