"""MCP tools for asynchronous BeeLoop messaging.

`BEELOOP_AGENT_DIR` binds operations to a harness-managed agent.
"""

from __future__ import annotations

import os
from dataclasses import asdict
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from beeloop import scripts, timers
from beeloop.agents import records, timers as agent_timers
from beeloop.agents.records import AgentError
from beeloop.config import ConfigError, root as configured_root
from beeloop.dispatch import messaging
from beeloop.dispatch.routes import RouteError

MessagingError = messaging.MessagingError

AGENT_DIRECTORY: Path | None = None
mcp = MCPServer(
    "beeloop-tools",
    instructions="Messaging for harness-bound BeeLoop agents.",
)


def _tool(function: Callable[..., Any]) -> Callable[..., Any]:
    """Expose expected BeeLoop failures as actionable MCP tool errors."""
    @wraps(function)
    def call(*args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except (
            MessagingError, AgentError, scripts.ScriptError, timers.TimerError, ConfigError, RouteError
        ) as exc:
            raise ToolError(str(exc)) from exc

    mcp.tool()(call)
    return function


@_tool
def get_agent_id() -> str | None:
    """Return this agent's ID, or None outside the BeeLoop harness."""
    return messaging.agent_id(AGENT_DIRECTORY) if AGENT_DIRECTORY else None


@_tool
def get_beeloop_root() -> str:
    """Return the configured absolute BeeLoop root."""
    return str(configured_root())


@_tool
def run_script(script: str, args: list[str] | None = None, stdin: str | None = None) -> dict[str, str | int]:
    """Run a prepared runtime/scripts or runtime/sources script as the orchestrator.

    Pass its path relative to runtime and literal arguments. Python scripts use
    this MCP server’s interpreter. Returns exit_code, stdout, and stderr.
    """
    directory = _agent_directory().resolve()
    agents = (configured_root() / "runtime" / "agents").resolve()
    if directory.parent != agents:
        raise MessagingError("run_script requires a bound orchestrator in runtime/agents")
    record = records.read(directory.name)
    if record.get("agent_id") != directory.name or record.get("role") != "orchestrator":
        raise MessagingError("run_script requires a bound orchestrator")
    return scripts.run(script, args, stdin)


@_tool
def create_agent(role: str, cwd: str | None = None) -> dict[str, str]:
    """Create an authorized agent without starting its first model turn."""
    return messaging.create_agent(_agent_directory(), role, cwd)


@_tool
def message(receiver: str | dict[str, Any], msg: str) -> dict[str, str]:
    """Asynchronously send a message to an agent ID or role route.

    A route is an object containing `role` and optional `cwd` and `instance`.
    Missing, empty, or `fresh` instances create a new agent; any other instance
    reuses the latest restorable agent created for that route.
    An accepted result identifies the resolved receiver and confirms background
    dispatch, not model completion.
    """
    return messaging.send(_agent_directory(), receiver, msg)


@_tool
def route_source(source: str, receiver_agent_id: str) -> dict[str, str]:
    """Route one persistent input source owned by this agent to an existing agent."""
    return messaging.route_source(_agent_directory(), source, receiver_agent_id)


@_tool
def list_agents() -> list[dict[str, Any]]:
    """List permitted BeeLoop agents for routing and conversation discovery."""
    return messaging.list_agents(_agent_directory())


@_tool
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


@_tool
def timer_list() -> list[dict[str, Any]]:
    """List this agent's wake timers in due order."""
    return [
        asdict(timer)
        for timer in agent_timers.list_wakes(messaging.agent_id(_agent_directory()))
    ]


@_tool
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
