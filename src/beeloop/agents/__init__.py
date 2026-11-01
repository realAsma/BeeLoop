"""Public agent lifecycle API."""

from __future__ import annotations

from .agent import (
    REGISTRY,
    Agent,
    NotResumable,
    agent_class,
    create,
    register,
    restore,
)
from .backends import Delivery, InputItem, Session
from .records import (
    CLOSED,
    DORMANT,
    PREPARED,
    STAMP,
    UTC,
    AgentError,
    Turn,
    UnknownAgent,
    agent_path,
    claim,
    drain_or_release,
    files_lock,
    new_agent_id,
    now,
    queue_path,
    read,
    record_path,
    root,
    runtime,
    update,
    validate,
    write,
)
from .roles import (
    DEFAULT_BACKEND,
    DEFAULT_PERMISSIONS,
    DEFAULT_TYPE,
    Role,
    UnknownRole,
    load_role,
    workspace,
)

__all__ = [
    "CLOSED", "DORMANT", "DEFAULT_BACKEND", "DEFAULT_PERMISSIONS",
    "DEFAULT_TYPE",
    "PREPARED", "STAMP", "UTC",
    "AgentError", "NotResumable", "UnknownAgent", "UnknownRole",
    "agent_path", "queue_path", "record_path", "root", "runtime",
    "Turn", "claim", "drain_or_release", "new_agent_id", "now", "read",
    "files_lock", "update", "validate", "write",
    "Role", "load_role", "workspace",
    "REGISTRY", "Agent", "agent_class", "create", "register", "restore",
    "Delivery", "InputItem", "Session",
]
