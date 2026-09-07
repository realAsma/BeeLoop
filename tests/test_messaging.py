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
from beebot.agents import records
from beebot.agents.backends import fake
from beebot.dispatch import messaging
from beebot.dispatch.envelope import Envelope
from beebot.dispatch.routes import Route
from tests.conftest import as_fake, make_role, orchestrator

# Loaded by path, because that is how the tool loads it. `plugins/` is not a
# package and is not installed: the plugin is installed into Claude Code, which
# runs this exact file from wherever it put it. An import would need a
# `plugins/` on sys.path that no deployment has.
PLUGIN_PATH = Path(__file__).resolve().parents[1] / "plugins" / "beeloop-tools"
SERVER_PATH = PLUGIN_PATH / "server.py"
_spec = importlib.util.spec_from_file_location("beeloop_tools_server", SERVER_PATH)
server = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = server
_spec.loader.exec_module(server)


@pytest.fixture(autouse=True)
def unbind_server():
    server.AGENT_DIRECTORY = None
    yield
    server.AGENT_DIRECTORY = None


def bind(agent: ag.Agent) -> None:
    server.AGENT_DIRECTORY = records.agent_path(agent.agent_id).resolve()


def rules(
    agent: ag.Agent,
    *,
    roles: list[str] | None = None,
    ids: list[str] | None = None,
) -> None:
    body = (
        "[messaging.allowed_recipients]\n"
        f"roles = {json.dumps(roles or [])}\n"
        f"ids = {json.dumps(ids or [])}\n"
    )
    (records.agent_path(agent.agent_id) / "config.toml").write_text(body, encoding="utf-8")


def sender_and_receiver() -> tuple[ag.Agent, ag.Agent]:
    sender = as_fake(orchestrator())
    receiver = as_fake(ag.create("logger"))
    bind(sender)
    return sender, receiver


def accepted(receiver: ag.Agent) -> dict[str, str]:
    return {
        "status": "accepted",
        "receiver_agent_id": receiver.agent_id,
        "receiver_role": receiver.record["role"],
    }


def wait_for(predicate, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("background dispatch did not finish")
        time.sleep(0.01)


def test_the_live_server_exposes_the_bound_messaging_and_timer_tools():
    agent = orchestrator()

    async def inspect_server():
        # Spawned the way an installed plugin is: the file by path, the
        # binding in the environment, nothing on the command line.
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(SERVER_PATH)],
            env={
                **os.environ,
                "BEEBOT_AGENT_DIR": str(records.agent_path(agent.agent_id).resolve()),
            },
        )
        async with stdio_client(parameters) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                return await session.list_tools(), await session.call_tool(
                    "get_agent_id"
                )

    listed, identity = asyncio.run(inspect_server())
    assert [tool.name for tool in listed.tools] == [
        "get_agent_id",
        "message",
        "timer_create",
        "timer_list",
        "timer_cancel",
    ]
    tool = listed.tools[1]
    message = tool.inputSchema
    assert set(message["properties"]) == {"receiver", "msg"}
    assert set(message["required"]) == {"receiver", "msg"}
    assert "fresh" in tool.description
    assert identity.content[0].text == agent.agent_id


def test_the_plugin_definitions_bind_each_tools_stdio_environment():
    claude = json.loads((PLUGIN_PATH / ".mcp.json").read_text("utf-8"))
    claude_server = claude["mcpServers"]["beeloop-tools"]
    assert claude_server["args"] == ["${CLAUDE_PLUGIN_ROOT}/server.py"]

    codex = json.loads(
        (PLUGIN_PATH / ".codex-plugin" / "plugin.json").read_text("utf-8")
    )
    codex_server = codex["mcpServers"]["beeloop-tools"]
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

    directory = records.agent_path(agent.agent_id).resolve()
    monkeypatch.setenv("BEEBOT_AGENT_DIR", str(directory))
    monkeypatch.setattr(server.mcp, "run", lambda: None)
    assert server.main() == 0
    assert server.AGENT_DIRECTORY == directory


def test_identity_is_read_from_the_bound_record_on_every_call():
    agent = orchestrator()
    bind(agent)
    assert server.get_agent_id() == agent.agent_id

    record = records.read(agent.agent_id)
    record["agent_id"] = "replacement-from-record"
    records.record_path(agent.agent_id).write_text(json.dumps(record), encoding="utf-8")

    assert server.get_agent_id() == "replacement-from-record"


def test_timer_tools_can_manage_only_the_bound_agents_record():
    first = orchestrator()
    second = ag.create("logger")
    bind(first)

    created = server.timer_create("check", after="1h")

    first_timers = server.timer_list()
    assert created in first_timers
    role_timers = [timer for timer in first_timers if timer != created]
    assert records.read(second.agent_id).get("timers", []) == []
    bind(second)
    assert server.timer_list() == []
    assert server.timer_cancel(created["timer_id"]) == "not found"
    assert records.read(first.agent_id)["timers"] == first_timers
    bind(first)
    assert server.timer_cancel(created["timer_id"]) == "cancelled"
    assert server.timer_list() == role_timers


@pytest.mark.parametrize(
    "grant",
    [
        {"roles": ["logger"]},
        {"ids": ["receiver"]},
        {"roles": ["*"]},
        {"ids": ["*"]},
    ],
)
def test_role_id_and_wildcard_allowances_union(monkeypatch, grant):
    sender, receiver = sender_and_receiver()
    grant = {
        key: [receiver.agent_id if value == "receiver" else value for value in values]
        for key, values in grant.items()
    }
    rules(sender, **grant)
    sent = []
    monkeypatch.setattr(messaging, "_submit", sent.append)

    assert server.message(receiver.agent_id, "hello") == accepted(receiver)
    assert sent == [
        Envelope(
            role=None,
            agent_id=receiver.agent_id,
            source=f"agent:{sender.agent_id}",
            msg="hello",
        )
    ]


@pytest.mark.parametrize("empty", [False, True])
def test_missing_and_empty_rules_deny_without_starting_dispatch(monkeypatch, empty):
    sender, receiver = sender_and_receiver()
    if empty:
        rules(sender)
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
    rules(sender, roles=["logger"])
    monkeypatch.setattr(messaging, "_submit", lambda *args: pytest.fail("dispatched"))

    with pytest.raises(server.MessagingError, match=match):
        server.message(receiver, "hello")


def test_live_sender_config_edits_take_effect(monkeypatch):
    sender, receiver = sender_and_receiver()
    rules(sender)
    monkeypatch.setattr(messaging, "_submit", lambda *args: None)

    with pytest.raises(server.MessagingError, match="may not send"):
        server.message(receiver.agent_id, "first")

    rules(sender, roles=["logger"])
    assert server.message(receiver.agent_id, "second") == accepted(receiver)

    rules(sender)
    with pytest.raises(server.MessagingError, match="may not send"):
        server.message(receiver.agent_id, "third")

    rules(sender, ids=[receiver.agent_id])
    assert server.message(receiver.agent_id, "fourth") == accepted(receiver)


def test_recipient_config_is_not_read(monkeypatch):
    sender, receiver = sender_and_receiver()
    rules(sender, roles=["logger"])
    recipient_config = records.agent_path(receiver.agent_id) / "config.toml"
    recipient_config.write_text(
        '[messaging.allowed_recipients]\nroles = "not a list"\n', encoding="utf-8"
    )
    monkeypatch.setattr(messaging, "_submit", lambda *args: None)

    assert server.message(receiver.agent_id, "hello") == accepted(receiver)


@pytest.mark.parametrize("field,value", [("roles", '"logger"'), ("ids", '{}')])
def test_malformed_allowed_recipient_lists_name_the_policy(field, value):
    sender, receiver = sender_and_receiver()
    config = records.agent_path(sender.agent_id) / "config.toml"
    config.write_text(
        f"[messaging.allowed_recipients]\n{field} = {value}\n", encoding="utf-8"
    )

    with pytest.raises(
        server.MessagingError,
        match=rf"messaging\.allowed_recipients\.{field}",
    ):
        server.message(receiver.agent_id, "hello")


def test_accepted_does_not_wait_for_the_receiver_process(monkeypatch):
    sender, receiver = sender_and_receiver()
    rules(sender, ids=[receiver.agent_id])

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

    assert server.message(receiver.agent_id, "hello") == accepted(receiver)
    assert process.stdin.closed is True
    assert f"agent_id={receiver.agent_id}".encode() in process.stdin.body


def test_an_exact_unknown_id_is_refused():
    sender = orchestrator()
    bind(sender)
    rules(sender, ids=["*"])

    with pytest.raises(ag.UnknownAgent):
        server.message("0198ff2a-0000-7000-8000-000000000000", "hello")


@pytest.mark.parametrize(
    "ids", [["*"], ["0198ff2a-0000-7000-8000-000000000000"]]
)
def test_route_creation_requires_an_allowed_role(beebot_root, monkeypatch, ids):
    make_role(
        beebot_root,
        "target",
        'backend = "fake"\ncwd = "workspaces/target"\n',
    )
    sender = orchestrator()
    bind(sender)
    rules(sender, ids=ids)
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
    assert len(list(records.runtime("agents").glob("*/record.json"))) == 1

    rules(sender, roles=["target"])
    receiver = {"role": "target", "instance": "one"}
    result = server.message(receiver, "hello")
    created = [
        ag.restore(path.parent.name)
        for path in records.runtime("agents").glob("*/record.json")
        if path.parent.name != sender.agent_id
    ]
    assert len(created) == 1
    assert created[0].record["role"] == "target"
    assert result == accepted(created[0])
    assert route.resolve() == created[0].agent_id


@pytest.mark.parametrize("instance", [None, "", "fresh"])
def test_ephemeral_message_routes_create_a_new_agent_each_time(
    beebot_root, monkeypatch, instance
):
    make_role(
        beebot_root,
        "target",
        'backend = "fake"\ncwd = "workspaces/target"\n',
    )
    sender = orchestrator()
    bind(sender)
    rules(sender, roles=["target"])
    submitted = []
    monkeypatch.setattr(messaging, "_submit", submitted.append)
    receiver = {"role": "target"}
    if instance is not None:
        receiver["instance"] = instance

    server.message(receiver, "first")
    server.message(receiver, "second")
    recipient_ids = [item.agent_id for item in submitted]

    assert len(set(recipient_ids)) == 2
    assert all("instance" not in records.read(agent_id) for agent_id in recipient_ids)
    assert not (records.runtime() / "routes.jsonl").exists()


def test_fresh_message_routes_require_role_creation_permission(
    beebot_root, monkeypatch
):
    make_role(
        beebot_root,
        "target",
        'backend = "fake"\ncwd = "workspaces/target"\n',
    )
    sender = orchestrator()
    bind(sender)
    rules(sender, ids=["*"])
    monkeypatch.setattr(messaging, "_submit", lambda *args: None)

    with pytest.raises(server.MessagingError, match="not allowed to create"):
        server.message({"role": "target", "instance": "fresh"}, "hello")

    assert len(list(records.runtime("agents").glob("*/record.json"))) == 1


def test_named_message_routes_reuse_and_remain_distinct(beebot_root, monkeypatch):
    make_role(
        beebot_root,
        "target",
        'backend = "fake"\ncwd = "workspaces/target"\n',
    )
    sender = orchestrator()
    bind(sender)
    rules(sender, roles=["target"])
    submitted = []
    monkeypatch.setattr(messaging, "_submit", submitted.append)

    for instance in ("default", "other", "default", "other"):
        server.message({"role": "target", "instance": instance}, "hello")

    recipient_ids = [item.agent_id for item in submitted]
    assert recipient_ids[0] == recipient_ids[2]
    assert recipient_ids[1] == recipient_ids[3]
    assert recipient_ids[0] != recipient_ids[1]


def test_receiver_routes_reject_unknown_fields(monkeypatch):
    sender = orchestrator()
    bind(sender)
    rules(sender, roles=["logger"])
    monkeypatch.setattr(messaging, "_submit", lambda *args: None)

    with pytest.raises(server.MessagingError, match="instnace"):
        server.message({"role": "logger", "instnace": "one"}, "hello")


def test_idle_delivery_is_detached_framed_and_logged():
    sender, receiver = sender_and_receiver()
    rules(sender, ids=[receiver.agent_id])

    assert server.message(receiver.agent_id, "background hello") == accepted(receiver)
    wait_for(lambda: bool(fake.turns(receiver.agent_id)))

    assert fake.turns(receiver.agent_id) == [
        [[f"agent:{sender.agent_id}", "background hello"]]
    ]
    wait_for(lambda: "delivered" in (records.root() / "logs" / "dispatch.log").read_text())


def test_busy_delivery_parks_then_drains_on_the_next_message():
    sender, receiver = sender_and_receiver()
    rules(sender, roles=["logger"])
    held = records.claim(receiver.agent_id, [])
    assert held is not None
    try:
        assert server.message(receiver.agent_id, "parked") == accepted(receiver)
        wait_for(lambda: records.queue_path(receiver.agent_id).exists())
        wait_for(lambda: bool(records.queue_path(receiver.agent_id).read_text()))
    finally:
        held.release()

    assert server.message(receiver.agent_id, "drain") == accepted(receiver)
    wait_for(lambda: records.read(receiver.agent_id)["turns"] == 2)
    delivered = [item for turn in fake.turns(receiver.agent_id) for item in turn]
    assert delivered == [
        [f"agent:{sender.agent_id}", "drain"],
        [f"agent:{sender.agent_id}", "parked"],
    ]
    log = (records.root() / "logs" / "dispatch.log").read_text("utf-8")
    assert "parked" in log and "delivered" in log
