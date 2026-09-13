"""Authorized asynchronous messaging between BeeLoop agents."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from beeloop import agents as ag
from beeloop.agents.records import root
from beeloop.agents.roles import load_role, workspace
from beeloop.dispatch.envelope import Envelope, serialize
from beeloop.dispatch.routes import Route, RouteError, agent_for, reassign


class MessagingError(RuntimeError):
    pass


@dataclass(frozen=True)
class AllowedReceivers:
    roles: frozenset[str]
    ids: frozenset[str]

    def allows(self, role: str, agent_id: str) -> bool:
        return (
            "*" in self.roles
            or "*" in self.ids
            or role in self.roles
            or agent_id in self.ids
        )

    def allows_role(self, role: str) -> bool:
        return "*" in self.roles or role in self.roles


ROUTE_FIELDS = frozenset({"role", "cwd", "instance"})


def agent_id(sender_directory: Path) -> str:
    """Return the identity bound to an agent directory."""
    return _identity(sender_directory)["agent_id"]


def create_agent(
    sender_directory: Path,
    role: str,
    cwd: str | None = None,
) -> dict[str, str]:
    """Authorize and create an agent without dispatching a turn."""
    allowed = _allowed_receivers(sender_directory / "config.toml")
    created = _create(allowed, role, cwd=cwd)
    return {
        "agent_id": created.agent_id,
        "role": created.record["role"],
        "cwd": str(created.cwd),
    }


def send(
    sender_directory: Path,
    receiver: str | dict[str, Any],
    msg: str,
) -> dict[str, str]:
    """Authorize and asynchronously submit one agent-to-agent message."""
    if not msg.strip():
        raise MessagingError("msg must not be empty")

    sender = _identity(sender_directory)
    allowed = _allowed_receivers(sender_directory / "config.toml")
    recipient = _resolve(receiver, sender, allowed)

    _submit(
        Envelope(
            role=None,
            agent_id=recipient.agent_id,
            source=f"agent:{sender['agent_id']}",
            msg=msg,
        )
    )
    return {
        "status": "accepted",
        "receiver_agent_id": recipient.agent_id,
        "receiver_role": recipient.record["role"],
    }


def route_source(
    sender_directory: Path,
    source: str,
    receiver_agent_id: str,
) -> dict[str, str]:
    """Authorize and reassign one persistent source owned by the caller."""
    if not isinstance(source, str) or not source:
        raise MessagingError("source must be a non-empty string")
    if not isinstance(receiver_agent_id, str) or not receiver_agent_id:
        raise MessagingError("receiver_agent_id must be a non-empty string")

    sender = _identity(sender_directory)
    instance = sender.get("instance")
    if not isinstance(instance, str) or not instance or instance == "fresh":
        raise MessagingError("the calling agent does not have a persistent instance")

    receiver = ag.restore(receiver_agent_id)
    allowed = _allowed_receivers(sender_directory / "config.toml")
    if not allowed.allows(receiver.record["role"], receiver.agent_id):
        raise MessagingError(
            f"agent {sender['agent_id']!r} may not route to role "
            f"{receiver.record['role']!r} or agent {receiver.agent_id!r}"
        )
    if (
        receiver.record["role"] != sender["role"]
        or str(receiver.cwd) != sender["cwd"]
        or receiver.record["backend"] != sender["backend"]
    ):
        raise MessagingError(
            "a routed source receiver must have the calling agent's role, cwd, "
            "and backend"
        )

    route = Route(source, sender["cwd"], sender["role"], sender["backend"], instance)
    try:
        reassign(route, sender["agent_id"], receiver.agent_id)
    except RouteError as exc:
        raise MessagingError(str(exc)) from exc
    return {
        "source": source,
        "receiver_agent_id": receiver.agent_id,
        "receiver_role": receiver.record["role"],
    }


def _identity(directory: Path) -> dict[str, str]:
    path = directory / "record.json"
    try:
        record = json.loads(path.read_text("utf-8"))
        identity = {
            field: record[field]
            for field in ("agent_id", "role", "cwd", "backend")
        }
        if "instance" in record:
            identity["instance"] = record["instance"]
        if not all(isinstance(value, str) and value for value in identity.values()):
            raise TypeError(
                "agent_id, role, cwd, and backend must be non-empty strings"
            )
        return identity
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise MessagingError(f"cannot read agent identity from {path}: {exc}") from exc


def _allowed_receivers(path: Path) -> AllowedReceivers:
    try:
        config = tomllib.loads(path.read_text("utf-8")) if path.exists() else {}
        rules = config.get("allowed_receivers", {})
        roles = _string_list(rules.get("roles", []), path, "roles")
        ids = _string_list(rules.get("ids", []), path, "ids")
    except (OSError, tomllib.TOMLDecodeError, AttributeError) as exc:
        raise MessagingError(
            f"cannot read allowed_receivers from {path}: {exc}"
        ) from exc
    return AllowedReceivers(frozenset(roles), frozenset(ids))


def _string_list(value: Any, path: Path, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise MessagingError(
            f"{path}: allowed_receivers.{field} must be a list of strings"
        )
    return value


def _resolve(
    receiver: str | dict[str, Any],
    sender: dict[str, str],
    allowed: AllowedReceivers,
) -> ag.Agent:
    if isinstance(receiver, str):
        recipient = ag.restore(receiver)
    elif isinstance(receiver, dict):
        route = _route(receiver, sender["agent_id"])
        existing = route.resolve()
        recipient = _restore(existing) if existing else None
        if recipient is None:
            if not allowed.allows_role(route.role):
                raise MessagingError(
                    f"role {route.role!r} is not allowed to create a message route"
                )
            recipient = agent_for(route, creator=partial(_create, allowed))
    else:
        raise MessagingError("receiver must be an agent ID or a route object")

    if not allowed.allows(recipient.record["role"], recipient.agent_id):
        raise MessagingError(
            f"agent {sender['agent_id']!r} may not send to role "
            f"{recipient.record['role']!r} or agent {recipient.agent_id!r}"
        )
    return recipient


def _create(
    allowed: AllowedReceivers,
    role: str,
    cwd: str | None = None,
    **record: Any,
) -> ag.Agent:
    if not allowed.allows_role(role):
        raise MessagingError(f"role {role!r} is not allowed to create an agent")
    return ag.create(role, cwd=cwd, **record)


def _route(receiver: dict[str, Any], sender_id: str) -> Route:
    if unknown := receiver.keys() - ROUTE_FIELDS:
        raise MessagingError(
            f"unknown receiver route field(s): {', '.join(sorted(unknown))}"
        )
    role = receiver.get("role")
    if not isinstance(role, str) or not role:
        raise MessagingError("a receiver route requires a non-empty role")
    for field in ("cwd", "instance"):
        if receiver.get(field) is not None and not isinstance(receiver[field], str):
            raise MessagingError(f"receiver route {field} must be a string")
    configured = load_role(role)
    return Route(
        source=f"agent:{sender_id}",
        cwd=str(workspace(configured, receiver.get("cwd"))),
        role=role,
        backend=configured.backend,
        instance=receiver.get("instance") or None,
    )


def _restore(agent_id: str | None) -> ag.Agent | None:
    if agent_id is None:
        return None
    try:
        return ag.restore(agent_id)
    except ag.AgentError:
        return None


def _submit(envelope: Envelope) -> None:
    log_path = root() / "logs" / "dispatch.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab", buffering=0) as log:
        process = subprocess.Popen(
            # Use this interpreter: it has already imported the installed SDK,
            # while PATH could select a different BeeLoop installation.
            [sys.executable, "-m", "beeloop.dispatch.dispatch"],
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        assert process.stdin is not None
        try:
            process.stdin.write(serialize(envelope).encode())
            process.stdin.close()
        except BaseException:
            process.kill()
            raise
