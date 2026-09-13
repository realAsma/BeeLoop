"""Resolve routing keys to agents."""

from __future__ import annotations

import fcntl
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from beeloop import agents as ag
from beeloop.agents import backends
from beeloop.agents.records import runtime
from beeloop.agents.roles import load_role, workspace
from beeloop.dispatch.envelope import Envelope

DEFAULT_ROLE = "orchestrator"
KEY = ("source", "cwd", "role", "backend", "instance")


class RouteError(RuntimeError):
    pass


def routes_path() -> Path:
    return runtime() / "routes.jsonl"


@dataclass(frozen=True)
class Route:
    source: str
    cwd: str
    role: str
    backend: str
    instance: str | None = None

    @property
    def ephemeral(self) -> bool:
        return not self.instance or self.instance == "fresh"

    @classmethod
    def of(cls, envelope: Envelope) -> "Route":
        role = envelope.role or DEFAULT_ROLE
        configured = load_role(role)
        backend = envelope.backend or configured.backend
        backends.get(backend)
        return cls(
            source=envelope.source,
            cwd=str(workspace(configured, envelope.cwd)),
            role=role,
            backend=backend,
            instance=envelope.instance,
        )

    def row(self, agent_id: str | None) -> dict[str, Any]:
        return {**{field: getattr(self, field) for field in KEY}, "agent_id": agent_id}

    def resolve(self) -> str | None:
        if self.ephemeral:
            return None
        found = None
        for row in _rows():
            if _matches(row, self):
                found = row.get("agent_id")
        return found

    def new(self, agent_id: str) -> None:
        if self.ephemeral:
            return
        with open(routes_path(), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(self.row(agent_id)) + "\n")

    def remove(self) -> None:
        """Tombstone this route without deleting its agent."""
        if self.ephemeral:
            return
        with open(routes_path(), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(self.row(None)) + "\n")


def _rows() -> Iterator[dict[str, Any]]:
    """Skip torn rows so a partial append cannot drop an envelope."""
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
    return all(row.get(field) == getattr(key, field) for field in KEY)


def _restored(agent_id: str) -> ag.Agent | None:
    try:
        return ag.restore(agent_id)
    except ag.AgentError:
        return None


AgentCreator = Callable[..., ag.Agent]


def agent_for(key: Route, creator: AgentCreator = ag.create) -> ag.Agent:
    """Resolve or create an agent, serializing the final check and append."""
    if key.ephemeral:
        return creator(key.role, cwd=key.cwd, backend=key.backend)
    if (agent_id := key.resolve()) and (found := _restored(agent_id)):
        return found

    routes_path().parent.mkdir(parents=True, exist_ok=True)
    with open(routes_path().with_suffix(".lock"), "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if (agent_id := key.resolve()) and (found := _restored(agent_id)):
                return found
            extra = {"instance": key.instance} if key.instance else {}
            new_agent = creator(
                key.role, cwd=key.cwd, backend=key.backend, **extra
            )
            key.new(new_agent.agent_id)
            return new_agent
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def reassign(key: Route, owner_agent_id: str, receiver_agent_id: str) -> None:
    """Atomically reassign a persistent route owned by one agent."""
    if key.ephemeral:
        raise RouteError(f"source {key.source!r} does not have a persistent route")

    routes_path().parent.mkdir(parents=True, exist_ok=True)
    with open(routes_path().with_suffix(".lock"), "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            current = key.resolve()
            if current is None:
                raise RouteError(f"source {key.source!r} has no route")
            if current != owner_agent_id:
                raise RouteError(
                    f"source {key.source!r} is routed to another agent"
                )
            key.new(receiver_agent_id)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
