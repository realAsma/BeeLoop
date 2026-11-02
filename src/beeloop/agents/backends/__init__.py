"""Resolve adapters lazily so record and status readers need not import them."""

from __future__ import annotations

from importlib import import_module

from .base import (
    Backend,
    BackendError,
    Delivery,
    DeliveryMode,
    Fault,
    InputItem,
    Session,
)

_BACKENDS = {
    "claude_code": ".claude:ClaudeBackend",
    "codex": ".codex:CodexBackend",
    "fake": ".fake:FakeBackend",
}

# The registry owns the default role backend, keeping provider names out of callers.
DEFAULT = "claude_code"


def get(name: str) -> Backend:
    if name not in _BACKENDS:
        raise BackendError(
            f"unknown backend {name!r}; this build has "
            f"{', '.join(sorted(_BACKENDS))}"
        )
    module_name, class_name = _BACKENDS[name].split(":")
    return getattr(import_module(module_name, __name__), class_name)()


__all__ = [
    "Backend",
    "BackendError",
    "Delivery",
    "DeliveryMode",
    "Fault",
    "InputItem",
    "DEFAULT",
    "Session",
    "get",
]
