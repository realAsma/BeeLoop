"""Bound MCP tools for authorized asynchronous BeeLoop messaging.

The binding arrives in `BEEBOT_AGENT_DIR`, out of the model's reach. The
messaging SDK derives the sender's identity and grants from that directory, so
making it a tool argument would let a prompt speak as another agent.
"""

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from beebot.agents import timers as agent_timers
from beebot.dispatch import messaging
from mcp.server.fastmcp import FastMCP

MessagingError = messaging.MessagingError

AGENT_DIRECTORY: Path | None = None
mcp = FastMCP(
    "beeloop-tools",
    instructions="Bound asynchronous messaging and wake timers for BeeLoop agents.",
)


@mcp.tool()
def get_agent_id() -> str:
    """Return this agent's stable BeeLoop ID."""
    return messaging.agent_id(_agent_directory())


@mcp.tool()
def message(receiver: str | dict[str, Any], msg: str) -> dict[str, str]:
    """Asynchronously send a message to an agent ID or role route.

    A route is an object containing `role` and optional `cwd` and `instance`.
    Missing, empty, or `fresh` instances create a new agent; any other instance
    reuses the latest restorable agent created for that route.
    An accepted result identifies the resolved receiver and confirms background
    dispatch, not model completion.
    """
    return messaging.send(_agent_directory(), receiver, msg)


@mcp.tool()
def timer_create(
    message: str,
    after: str | None = None,
    every: str | None = None,
    duration: str | None = None,
) -> dict[str, Any]:
    """Create a wake timer. Pass exactly one of `after` or `every`."""
    timer = agent_timers.create_wake(
        messaging.agent_id(_agent_directory()),
        message=message,
        after=after,
        every=every,
        duration=duration,
    )
    return asdict(timer)


@mcp.tool()
def timer_list() -> list[dict[str, Any]]:
    """List this agent's wake timers in due order."""
    return [
        asdict(timer)
        for timer in agent_timers.list_wakes(messaging.agent_id(_agent_directory()))
    ]


@mcp.tool()
def timer_cancel(timer_id: str) -> str:
    """Cancel one of this agent's wake timers."""
    removed = agent_timers.cancel_wake(
        messaging.agent_id(_agent_directory()), timer_id
    )
    return "cancelled" if removed else "not found"


def _agent_directory() -> Path:
    if AGENT_DIRECTORY is None:
        raise MessagingError("the messaging server is not bound to an agent directory")
    return AGENT_DIRECTORY


def main() -> int:
    bound = os.environ.get("BEEBOT_AGENT_DIR", "")
    if not bound:
        raise MessagingError(
            "BEEBOT_AGENT_DIR is not set; this server is spawned bound to one "
            "agent directory and has no meaning without it"
        )
    directory = Path(bound)
    if not directory.is_absolute():
        raise MessagingError(f"BEEBOT_AGENT_DIR must be absolute, got {bound!r}")
    global AGENT_DIRECTORY
    AGENT_DIRECTORY = directory.resolve()
    # BEEBOT_ROOT remains the inherited deployment root. Re-deriving it from
    # the agent directory could silently make the SDK use a different tree.
    mcp.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
