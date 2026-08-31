"""The routing table: which agent owns a key, and what to do when none does.

A HELPER for a key that is derived and guessable, never the authority on
identity. Delete the file and every key still computes; the cost is duplicate
agents, not unaddressable ones. That is why a torn read, an unparseable line and
a row pointing at a deleted record all resolve to "start fresh": each is
recoverable, and failing the tick instead would drop an envelope, which is not.
"""

from __future__ import annotations

import fcntl
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

import agents as ag

from dispatch.envelope import Envelope

DEFAULT_ROLE = "orchestrator"


def routes_path() -> Path:
    """Runtime state, not source: it lives with the agent records, not beside
    the code that writes it."""
    return ag.runtime() / "routes.jsonl"


# The routing key, and the order its fields appear in a row. Declared once and
# read by every comparison: whole-dict equality would mean the day a payload
# field is added to a row, every existing route silently stops matching.
KEY = ("source", "cwd", "role", "instance")


@dataclass(frozen=True)
class Route:
    """What a row is keyed on: who caused this, where it works, as what, and
    which of several.

    RESOLVED, never the raw envelope. `role` is optional on the wire and `cwd`
    is optional twice over -- it may come from the role instead, and it may be
    relative. Keying on what arrived would make `{source=X}` and
    `{source=X, role=orchestrator}` two keys that must be one agent, and every
    such mismatch is a duplicate agent nobody asked for.

    `instance` is what lets one triple own more than one agent: same role, same
    tree, same source, told apart by nothing else.
    """

    source: str
    cwd: str  # absolute, and byte-identical to the agent record's own
    role: str
    instance: str | None = None  # None is the default agent for the triple

    @classmethod
    def of(cls, envelope: Envelope) -> "Route":
        """Resolve an envelope's addressing into the key it routes on.

        Both failures this can raise -- a role that names no directory, and a
        role that says nothing about where to work when the envelope does not
        either -- used to surface a layer down inside `create`. They are not
        routable either way, so raising here only makes them arrive sooner.
        """
        role = envelope.role or DEFAULT_ROLE
        return cls(
            source=envelope.source,
            cwd=str(ag.workspace(ag.load_role(role), envelope.cwd)),
            role=role,
            instance=envelope.instance,
        )

    def row(self, agent_id: str | None) -> dict[str, Any]:
        """The key plus its answer. `None` is a tombstone."""
        return {**{field: getattr(self, field) for field in KEY}, "agent_id": agent_id}

    def resolve(self) -> str | None:
        """The last row wins, and a null one is a tombstone. No lock: a torn read
        costs a duplicate agent, and a duplicate agent beats a dropped envelope."""
        found = None
        for row in _rows():
            if _matches(row, self):
                found = row.get("agent_id")
        return found

    def new(self, agent_id: str) -> None:
        with open(routes_path(), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(self.row(agent_id)) + "\n")

    def remove(self) -> None:
        """End the route, not the agent. The record and transcript are
        untouched and it stays reachable by `agent_id`; what ends is this key's
        answer, so the next envelope on it creates a fresh agent."""
        with open(routes_path(), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(self.row(None)) + "\n")


def _rows() -> Iterator[dict[str, Any]]:
    """Replay top to bottom, discarding a line that will not parse.

    A half-written trailing row is the normal cost of an append-only file that
    nobody fsyncs. Losing it starts a new agent, which is wrong but not corrupt;
    failing the tick over it would drop an envelope, which is worse.
    """
    path = routes_path()
    if not path.exists():
        return
    for line in path.read_text("utf-8").splitlines():
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except ValueError:
            continue


def _matches(row: Mapping[str, Any], key: Route) -> bool:
    """Every key field, and only the key fields. A row written before a field
    existed reads as `None` there -- which is why `instance` defaults to `None`
    and not `""`: pre-`instance` rows keep matching a key that names none, so
    widening needed no migration. A field with no such default matches nothing,
    starting a fresh agent -- wrong, self-correcting, and not a mis-delivery."""
    return all(row.get(field) == getattr(key, field) for field in KEY)


def _restored(agent_id: str) -> ag.Agent | None:
    """A route that cannot be turned back into an agent is no route.

    Not just a missing record: an unregistered `type` and a role directory that
    has been deleted both leave a row pointing at nothing usable, and all three
    mean the same thing here. Letting those two out would kill the tick over a
    stale row, which drops an envelope -- strictly worse than the duplicate
    agent that starting fresh costs.
    """
    try:
        return ag.restore(agent_id)
    except ag.AgentError:
        return None


def agent_for(key: Route) -> ag.Agent:
    """Resolve a routing key to the one agent that owns it, creating if needed.

    The check-and-append under the lock is the only race in this system, and the
    single surviving piece of what used to be a lease: two ticks seeing no route
    for the same key would each spawn an agent, so the winner takes the lock,
    RE-READS, and appends only if still unrouted.

    Holding the lock across create() is free -- reserving a session id costs no
    model call -- and the append is the serialization point.
    """
    if (agent_id := key.resolve()) and (found := _restored(agent_id)):
        return found

    routes_path().parent.mkdir(parents=True, exist_ok=True)
    with open(routes_path().with_suffix(".lock"), "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if (agent_id := key.resolve()) and (found := _restored(agent_id)):
                return found
            # Omitted rather than None, so a default agent's record stays
            # byte-identical to a pre-`instance` one.
            extra = {"instance": key.instance} if key.instance else {}
            new_agent = ag.create(key.role, cwd=key.cwd, **extra)
            key.new(new_agent.agent_id)
            return new_agent
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
