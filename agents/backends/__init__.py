"""Name to adapter, resolved lazily.

Lazily because a caller that only reads records -- a sweep, a status listing --
should never drag a subprocess adapter into its process just by importing this.
"""

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
    "fake": ".fake:FakeBackend",
}

# The backend a role gets when it does not name one. It lives here rather than
# beside the roles because naming an adapter is this module's whole job, and
# every other module should be able to be grepped for a provider name and come
# up empty.
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
