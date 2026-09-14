"""MCP tools for asynchronous BeeLoop messaging.

When present, `BEELOOP_AGENT_DIR` binds calls to a harness-managed agent.
Without it, creation and messaging are trusted direct-user operations.
"""

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from beeloop.agents import timers as agent_timers
from beeloop.config import root as configured_root
from beeloop.dispatch import messaging
from mcp.server.fastmcp import FastMCP

MessagingError = messaging.MessagingError

AGENT_DIRECTORY: Path | None = None
mcp = FastMCP(
    "beeloop-tools",
    instructions="Messaging for BeeLoop agents and trusted direct-user sessions.",
)


@mcp.tool()
def get_agent_id() -> str | None:
    """Return this agent's ID, or None outside the BeeLoop harness."""
    return messaging.agent_id(AGENT_DIRECTORY) if AGENT_DIRECTORY else None


@mcp.tool()
def get_beeloop_root() -> str:
    """Return the configured absolute BeeLoop root."""
    return str(configured_root())


@mcp.tool()
def create_agent(role: str, cwd: str | None = None) -> dict[str, str]:
    """Create an authorized agent without starting its first model turn."""
    return messaging.create_agent(AGENT_DIRECTORY, role, cwd)


@mcp.tool()
def message(receiver: str | dict[str, Any], msg: str) -> dict[str, str]:
    """Asynchronously send a message to an agent ID or role route.

    A route is an object containing `role` and optional `cwd` and `instance`.
    Missing, empty, or `fresh` instances create a new agent; any other instance
    reuses the latest restorable agent created for that route.
    An accepted result identifies the resolved receiver and confirms background
    dispatch, not model completion.
    """
    return messaging.send(AGENT_DIRECTORY, receiver, msg)


@mcp.tool()
def route_source(source: str, receiver_agent_id: str) -> dict[str, str]:
    """Route one persistent input source owned by this agent to an existing agent."""
    return messaging.route_source(_agent_directory(), source, receiver_agent_id)


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
        raise MessagingError("requires a bound BeeLoop agent")
    return AGENT_DIRECTORY


def main() -> int:
    bound = os.environ.get("BEELOOP_AGENT_DIR", "")
    global AGENT_DIRECTORY
    AGENT_DIRECTORY = None
    if bound:
        directory = Path(bound)
        if not directory.is_absolute():
            raise MessagingError(f"BEELOOP_AGENT_DIR must be absolute, got {bound!r}")
        AGENT_DIRECTORY = directory.resolve()
    mcp.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
