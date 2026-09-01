"""The bound MCP surface, authorization, routing, and detached delivery."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from beebot import agents as ag
from beebot.agents.backends import fake
from beebot.dispatch import messaging
from beebot.dispatch.envelope import Envelope
from beebot.dispatch.routes import Route
from tests.test_agents import as_fake, make_role, orchestrator

# Loaded by path, because that is how the tool loads it. `plugins/` is not a
# package and is not installed: the plugin is installed into Claude Code, which
# runs this exact file from wherever it put it. An import would need a
# `plugins/` on sys.path that no deployment has.
PLUGIN_PATH = Path(__file__).resolve().parents[1] / "plugins" / "beebot-loop"
SERVER_PATH = PLUGIN_PATH / "server.py"
_spec = importlib.util.spec_from_file_location("beebot_loop_server", SERVER_PATH)
server = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = server
_spec.loader.exec_module(server)


@pytest.fixture(autouse=True)
def unbind_server():
    server.AGENT_DIRECTORY = None
    yield
    server.AGENT_DIRECTORY = None


def bind(agent: ag.Agent) -> None:
    server.AGENT_DIRECTORY = ag.agent_path(agent.agent_id).resolve()


def rules(
    agent: ag.Agent,
    *,
    send_roles: list[str] | None = None,
    send_ids: list[str] | None = None,
    receive_roles: list[str] | None = None,
    receive_ids: list[str] | None = None,
) -> None:
    body = (
        "[messaging.send]\n"
        f"roles = {json.dumps(send_roles or [])}\n"
        f"ids = {json.dumps(send_ids or [])}\n\n"
        "[messaging.receive]\n"
        f"roles = {json.dumps(receive_roles or [])}\n"
        f"ids = {json.dumps(receive_ids or [])}\n"
    )
    (ag.agent_path(agent.agent_id) / "config.toml").write_text(body, encoding="utf-8")


def sender_and_receiver() -> tuple[ag.Agent, ag.Agent]:
    sender = as_fake(orchestrator())
    receiver = as_fake(ag.create("logger"))
    bind(sender)
    return sender, receiver


def wait_for(predicate, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("background dispatch did not finish")
        time.sleep(0.01)


def test_the_live_server_exposes_exactly_the_two_public_tools():
    agent = orchestrator()

    async def inspect_server():
        # Spawned the way an installed plugin is: the file by path, the
        # binding in the environment, nothing on the command line.
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(SERVER_PATH)],
            env={
                **os.environ,
                "BEEBOT_AGENT_DIR": str(ag.agent_path(agent.agent_id).resolve()),
            },
        )
        async with stdio_client(parameters) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                return await session.list_tools(), await session.call_tool(
                    "get_agent_id"
                )

    listed, identity = asyncio.run(inspect_server())
    assert [tool.name for tool in listed.tools] == ["get_agent_id", "message"]
    message = listed.tools[1].inputSchema
    assert set(message["properties"]) == {"receiver", "msg"}
    assert set(message["required"]) == {"receiver", "msg"}
    assert identity.content[0].text == agent.agent_id


def test_the_plugin_definitions_bind_each_tools_stdio_environment():
    claude = json.loads((PLUGIN_PATH / ".mcp.json").read_text("utf-8"))
    claude_server = claude["mcpServers"]["beebot-loop"]
    assert claude_server["args"] == ["${CLAUDE_PLUGIN_ROOT}/server.py"]

    codex = json.loads(
        (PLUGIN_PATH / ".codex-plugin" / "plugin.json").read_text("utf-8")
    )
    codex_server = codex["mcpServers"]["beebot-loop"]
    assert codex_server["args"] == ["server.py"]
    assert codex_server["cwd"] == "."
    assert codex_server["env_vars"] == ["BEEBOT_AGENT_DIR", "BEEBOT_ROOT"]


def test_the_binding_comes_from_the_environment_and_must_be_absolute(monkeypatch):
    """The binding is the identity, so a missing or relative one is refused
    before the server can answer a single call under nobody's name."""
    agent = orchestrator()
    monkeypatch.delenv("BEEBOT_AGENT_DIR", raising=False)
    with pytest.raises(server.MessagingError, match="BEEBOT_AGENT_DIR is not set"):
        server.main()

    monkeypatch.setenv("BEEBOT_AGENT_DIR", "runtime/agents/whoever")
    with pytest.raises(server.MessagingError, match="must be absolute"):
        server.main()

    directory = ag.agent_path(agent.agent_id).resolve()
    monkeypatch.setenv("BEEBOT_AGENT_DIR", str(directory))
    monkeypatch.setattr(server.mcp, "run", lambda: None)
    assert server.main() == 0
    assert server.AGENT_DIRECTORY == directory


def test_identity_is_read_from_the_bound_record_on_every_call():
    agent = orchestrator()
    bind(agent)
    assert server.get_agent_id() == agent.agent_id

    record = ag.read(agent.agent_id)
    record["agent_id"] = "replacement-from-record"
    ag.record_path(agent.agent_id).write_text(json.dumps(record), encoding="utf-8")

    assert server.get_agent_id() == "replacement-from-record"


@pytest.mark.parametrize(
    "sender_grant,receiver_grant",
    [
        ({"send_roles": ["logger"]}, {"receive_roles": ["orchestrator"]}),
        ({"send_ids": ["receiver"]}, {"receive_ids": ["sender"]}),
        ({"send_roles": ["*"]}, {"receive_ids": ["*"]}),
    ],
)
def test_role_id_and_wildcard_grants_union(
    monkeypatch, sender_grant, receiver_grant
):
    sender, receiver = sender_and_receiver()
    sender_grant = {
        key: [receiver.agent_id if value == "receiver" else value for value in values]
        for key, values in sender_grant.items()
    }
    receiver_grant = {
        key: [sender.agent_id if value == "sender" else value for value in values]
        for key, values in receiver_grant.items()
    }
    rules(sender, **sender_grant)
    rules(receiver, **receiver_grant)
    sent = []
    monkeypatch.setattr(messaging, "_submit", sent.append)

    assert server.message(receiver.agent_id, "hello") == "accepted"
    assert sent == [
        Envelope(
            role=None,
            agent_id=receiver.agent_id,
            source=f"agent:{sender.agent_id}",
            msg="hello",
        )
    ]


def test_missing_and_empty_rules_deny_without_starting_dispatch(monkeypatch):
    sender, receiver = sender_and_receiver()
    called = False

    def dispatch(*args):
        nonlocal called
        called = True

    monkeypatch.setattr(messaging, "_submit", dispatch)
    with pytest.raises(server.MessagingError, match="may not send"):
        server.message(receiver.agent_id, "hello")
    assert called is False


def test_empty_messages_are_refused_before_dispatch(monkeypatch):
    sender = orchestrator()
    bind(sender)
    monkeypatch.setattr(messaging, "_submit", lambda *args: pytest.fail("dispatched"))

    with pytest.raises(server.MessagingError, match="must not be empty"):
        server.message("anyone", "  \n")


@pytest.mark.parametrize(
    "receiver,match",
    [
        ({}, "non-empty role"),
        ({"role": ""}, "non-empty role"),
        ({"role": "logger", "cwd": 1}, "cwd"),
        ({"role": "logger", "instance": []}, "instance"),
        (42, "agent ID or a route object"),
    ],
)
def test_malformed_receivers_are_refused(receiver, match, monkeypatch):
    sender = orchestrator()
    bind(sender)
    rules(sender, send_roles=["logger"])
    monkeypatch.setattr(messaging, "_submit", lambda *args: pytest.fail("dispatched"))

    with pytest.raises(server.MessagingError, match=match):
        server.message(receiver, "hello")


def test_both_sides_must_allow_and_live_config_edits_take_effect(monkeypatch):
    sender, receiver = sender_and_receiver()
    rules(sender)
    rules(receiver, receive_ids=[sender.agent_id])
    monkeypatch.setattr(messaging, "_submit", lambda *args: None)

    with pytest.raises(server.MessagingError, match="may not send"):
        server.message(receiver.agent_id, "first")

    rules(sender, send_roles=["logger"])
    assert server.message(receiver.agent_id, "second") == "accepted"

    rules(receiver)
    with pytest.raises(server.MessagingError, match="does not allow"):
        server.message(receiver.agent_id, "third")

    rules(receiver, receive_ids=[sender.agent_id])
    assert server.message(receiver.agent_id, "fourth") == "accepted"


def test_accepted_does_not_wait_for_the_receiver_process(monkeypatch):
    sender, receiver = sender_and_receiver()
    rules(sender, send_ids=[receiver.agent_id])
    rules(receiver, receive_ids=[sender.agent_id])

    class Input:
        def __init__(self):
            self.body = b""
            self.closed = False

        def write(self, body):
            self.body += body

        def close(self):
            self.closed = True

    class Process:
        def __init__(self):
            self.stdin = Input()

        def wait(self):
            raise AssertionError("message() waited for delivery")

        def kill(self):
            pass

    process = Process()
    monkeypatch.setattr(messaging.subprocess, "Popen", lambda *args, **kwargs: process)

    assert server.message(receiver.agent_id, "hello") == "accepted"
    assert process.stdin.closed is True
    assert f"agent_id={receiver.agent_id}".encode() in process.stdin.body


def test_an_exact_unknown_id_is_refused():
    sender = orchestrator()
    bind(sender)
    rules(sender, send_ids=["*"])

    with pytest.raises(ag.UnknownAgent):
        server.message("0198ff2a-0000-7000-8000-000000000000", "hello")


def test_route_creation_requires_a_send_role_grant(beebot_root, monkeypatch):
    make_role(
        beebot_root,
        "target",
        'backend = "fake"\ncwd = "workspaces/target"\n'
        '[messaging.receive]\nroles = ["orchestrator"]\nids = []\n',
    )
    sender = orchestrator()
    bind(sender)
    rules(sender, send_ids=["*"])
    monkeypatch.setattr(messaging, "_submit", lambda *args: None)
    route = Route(
        source=f"agent:{sender.agent_id}",
        cwd=str((beebot_root / "workspaces" / "target").resolve()),
        role="target",
        instance="one",
    )
    route.new("0198ff2a-0000-7000-8000-000000000000")

    with pytest.raises(server.MessagingError, match="not allowed to create"):
        server.message({"role": "target", "instance": "one"}, "hello")
    assert len(list(ag.runtime("agents").glob("*/record.json"))) == 1

    rules(sender, send_roles=["target"])
    receiver = {"role": "target", "instance": "one"}
    assert server.message(receiver, "hello") == "accepted"
    created = [
        ag.restore(path.parent.name)
        for path in ag.runtime("agents").glob("*/record.json")
        if path.parent.name != sender.agent_id
    ]
    assert len(created) == 1
    assert created[0].record["role"] == "target"
    assert route.resolve() == created[0].agent_id


def test_receiver_routes_reject_unknown_fields(monkeypatch):
    sender = orchestrator()
    bind(sender)
    rules(sender, send_roles=["logger"])
    monkeypatch.setattr(messaging, "_submit", lambda *args: None)

    with pytest.raises(server.MessagingError, match="instnace"):
        server.message({"role": "logger", "instnace": "one"}, "hello")


def test_idle_delivery_is_detached_framed_and_logged():
    sender, receiver = sender_and_receiver()
    rules(sender, send_ids=[receiver.agent_id])
    rules(receiver, receive_ids=[sender.agent_id])

    assert server.message(receiver.agent_id, "background hello") == "accepted"
    wait_for(lambda: bool(fake.turns(receiver.agent_id)))

    assert fake.turns(receiver.agent_id) == [
        [[f"agent:{sender.agent_id}", "background hello"]]
    ]
    wait_for(lambda: "delivered" in (ag.root() / "logs" / "dispatch.log").read_text())


def test_busy_delivery_parks_then_drains_on_the_next_message():
    sender, receiver = sender_and_receiver()
    rules(sender, send_roles=["logger"])
    rules(receiver, receive_roles=["orchestrator"])
    held = ag.claim(receiver.agent_id, [])
    assert held is not None
    try:
        assert server.message(receiver.agent_id, "parked") == "accepted"
        wait_for(lambda: ag.queue_path(receiver.agent_id).exists())
        wait_for(lambda: bool(ag.queue_path(receiver.agent_id).read_text()))
    finally:
        held.release()

    assert server.message(receiver.agent_id, "drain") == "accepted"
    wait_for(lambda: ag.read(receiver.agent_id)["turns"] == 2)
    delivered = [item for turn in fake.turns(receiver.agent_id) for item in turn]
    assert delivered == [
        [f"agent:{sender.agent_id}", "drain"],
        [f"agent:{sender.agent_id}", "parked"],
    ]
    log = (ag.root() / "logs" / "dispatch.log").read_text("utf-8")
    assert "parked" in log and "delivered" in log
