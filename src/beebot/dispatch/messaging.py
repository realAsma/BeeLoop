"""Authorized asynchronous messaging between BeeBot agents."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from beebot import agents as ag
from beebot.agents.records import agent_path, root
from beebot.agents.roles import load_role, workspace
from beebot.dispatch.envelope import Envelope, serialize
from beebot.dispatch.routes import Route, agent_for


class MessagingError(RuntimeError):
    pass


@dataclass(frozen=True)
class Grants:
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


def send(
    sender_directory: Path,
    receiver: str | dict[str, Any],
    msg: str,
) -> dict[str, str]:
    """Authorize and asynchronously submit one agent-to-agent message."""
    if not msg.strip():
        raise MessagingError("msg must not be empty")

    sender = _identity(sender_directory)
    grants = _grants(sender_directory / "config.toml", "send")
    recipient = _resolve(receiver, sender, grants)
    receive = _grants(agent_path(recipient.agent_id) / "config.toml", "receive")
    if not receive.allows(sender["role"], sender["agent_id"]):
        raise MessagingError(
            f"agent {recipient.agent_id!r} does not allow messages from "
            f"role {sender['role']!r} or agent {sender['agent_id']!r}"
        )

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


def _identity(directory: Path) -> dict[str, str]:
    path = directory / "record.json"
    try:
        record = json.loads(path.read_text("utf-8"))
        identity = {"agent_id": record["agent_id"], "role": record["role"]}
        if not all(isinstance(value, str) and value for value in identity.values()):
            raise TypeError("agent_id and role must be non-empty strings")
        return identity
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise MessagingError(f"cannot read agent identity from {path}: {exc}") from exc


def _grants(path: Path, direction: str) -> Grants:
    try:
        config = tomllib.loads(path.read_text("utf-8")) if path.exists() else {}
        rules = config.get("messaging", {}).get(direction, {})
        roles = _string_list(rules.get("roles", []), path, direction, "roles")
        ids = _string_list(rules.get("ids", []), path, direction, "ids")
    except (OSError, tomllib.TOMLDecodeError, AttributeError) as exc:
        raise MessagingError(f"cannot read messaging rules from {path}: {exc}") from exc
    return Grants(frozenset(roles), frozenset(ids))


def _string_list(
    value: Any, path: Path, direction: str, field: str
) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise MessagingError(
            f"{path}: messaging.{direction}.{field} must be a list of strings"
        )
    return value


def _resolve(
    receiver: str | dict[str, Any],
    sender: dict[str, str],
    send: Grants,
) -> ag.Agent:
    if isinstance(receiver, str):
        recipient = ag.restore(receiver)
    elif isinstance(receiver, dict):
        route = _route(receiver, sender["agent_id"])
        existing = route.resolve()
        recipient = _restore(existing) if existing else None
        if recipient is None:
            if not send.allows_role(route.role):
                raise MessagingError(
                    f"role {route.role!r} is not allowed to create a message route"
                )
            role_config = root() / "configs" / "roles" / route.role / "role.toml"
            receive = _grants(role_config, "receive")
            if not receive.allows(sender["role"], sender["agent_id"]):
                raise MessagingError(
                    f"role {route.role!r} does not allow messages from "
                    f"role {sender['role']!r} or agent {sender['agent_id']!r}"
                )
            recipient = agent_for(route)
    else:
        raise MessagingError("receiver must be an agent ID or a route object")

    if not send.allows(recipient.record["role"], recipient.agent_id):
        raise MessagingError(
            f"agent {sender['agent_id']!r} may not send to role "
            f"{recipient.record['role']!r} or agent {recipient.agent_id!r}"
        )
    return recipient


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
    return Route(
        source=f"agent:{sender_id}",
        cwd=str(workspace(load_role(role), receiver.get("cwd"))),
        role=role,
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
            # while PATH could select a different BeeBot installation.
            [sys.executable, "-m", "beebot.dispatch.dispatch"],
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
