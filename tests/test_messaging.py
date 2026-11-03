"""The bound MCP surface, authorization, routing, and detached delivery."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import beeloop.dispatch.dispatch as dsp
from beeloop import scripts
from beeloop import agents as ag
from beeloop.agents import records
from beeloop.agents.backends import fake
from beeloop.dispatch import messaging
from beeloop.dispatch.envelope import Envelope
from beeloop.dispatch.routes import Route
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
        "[allowed_receivers]\n"
        f"roles = {json.dumps(roles or [])}\n"
        f"ids = {json.dumps(ids or [])}\n"
    )
    (records.agent_path(agent.agent_id) / "config.toml").write_text(body, encoding="utf-8")


def sender_and_receiver() -> tuple[ag.Agent, ag.Agent]:
    sender = as_fake(orchestrator())
    receiver = as_fake(ag.create("worker", cwd="workspaces/receiver"))
    bind(sender)
    return sender, receiver


def accepted(receiver: ag.Agent) -> dict[str, str]:
    return {
        "status": "accepted",
        "receiver_agent_id": receiver.agent_id,
        "receiver_role": receiver.record["role"],
        "receiver_cwd": str(receiver.cwd),
    }


def routed_pair(source: str = "slack:thread") -> tuple[ag.Agent, ag.Agent, Route]:
    sender = orchestrator(instance="secondary", backend="fake")
    receiver = orchestrator(instance="primary", backend="fake")
    route = Route(
        source, sender.record["cwd"], sender.record["role"], "fake", "secondary"
    )
    route.new(sender.agent_id)
    bind(sender)
    return sender, receiver, route


def wait_for(predicate, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("background dispatch did not finish")
        time.sleep(0.01)


@asynccontextmanager
async def live_session(agent: ag.Agent | None = None):
    # Spawned the way an installed plugin is: the file by path, the
    # binding in the environment, nothing on the command line.
    env = {
        key: value for key, value in os.environ.items()
        if key != "BEELOOP_AGENT_DIR"
    }
    if agent is not None:
        env["BEELOOP_AGENT_DIR"] = str(records.agent_path(agent.agent_id).resolve())
    parameters = StdioServerParameters(
        command=sys.executable, args=[str(SERVER_PATH)], env=env,
    )
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            yield session


@pytest.mark.parametrize("bound", [True, False])
def test_server_introspection_with_and_without_agent_binding(bound, beeloop_root):
    agent = orchestrator()

    async def inspect_server():
        async with live_session(agent if bound else None) as session:
            return (
                await session.list_tools(),
                await session.call_tool("get_agent_id"),
                await session.call_tool("get_beeloop_root"),
            )

    listed, identity, resolved_root = asyncio.run(inspect_server())
    assert [tool.name for tool in listed.tools] == [
        "get_agent_id",
        "get_beeloop_root",
        "run_script",
        "create_agent",
        "message",
        "route_source",
        "list_agents",
        "timer_create",
        "timer_list",
        "timer_cancel",
    ]
    root_tool = listed.tools[1]
    assert root_tool.input_schema["properties"] == {}
    assert "required" not in root_tool.input_schema
    assert resolved_root.content[0].text == str(beeloop_root.resolve())
    create = next(tool for tool in listed.tools if tool.name == "create_agent")
    assert set(create.input_schema["properties"]) == {"role", "cwd", "config"}
    assert set(create.input_schema["required"]) == {"role"}
    assert any(shape.get("type") == "object" for shape in create.input_schema["properties"]["config"]["anyOf"])
    assert "without starting" in create.description
    by_name = {tool.name: tool for tool in listed.tools}
    runner = by_name["run_script"].input_schema
    assert set(runner["properties"]) == {"script", "args", "stdin"}
    assert set(runner["required"]) == {"script"}
    tool = by_name["message"]
    message = tool.input_schema
    assert set(message["properties"]) == {"receiver", "msg"}
    assert set(message["required"]) == {"receiver", "msg"}
    assert "fresh" in tool.description
    route = by_name["route_source"]
    assert set(route.input_schema["properties"]) == {"source", "receiver_agent_id"}
    assert set(route.input_schema["required"]) == {"source", "receiver_agent_id"}
    assert "existing agent" in route.description
    if bound:
        assert identity.content[0].text == agent.agent_id
    else:
        assert identity.content == []


def test_live_tool_errors_keep_the_actionable_reason():
    async def inspect_errors():
        async with live_session() as session:
            for name, arguments, reason in [
                ("message", {"receiver": "missing", "msg": ""}, "requires a bound BeeLoop agent"),
                ("create_agent", {"role": "missing"}, "requires a bound BeeLoop agent"),
                ("create_agent", {"role": "worker", "config": {}}, "requires a bound BeeLoop agent"),
                ("list_agents", {}, "requires a bound BeeLoop agent"),
                ("timer_list", {}, "requires a bound BeeLoop agent"),
            ]:
                result = await session.call_tool(name, arguments)
                assert result.is_error
                assert reason in result.content[0].text

    asyncio.run(inspect_errors())


def test_live_timer_tools_create_list_cancel_and_report_invalid_durations():
    agent = orchestrator()

    async def inspect_timers():
        async with live_session(agent) as session:
            original = await session.call_tool("timer_list")
            invalid = await session.call_tool(
                "timer_create", {"message": "check", "after": "bad"},
            )
            assert invalid.is_error
            assert "positive integer" in invalid.content[0].text
            created = await session.call_tool(
                "timer_create", {"message": "check", "after": "1h"},
            )
            assert not created.is_error
            timer = created.structured_content
            listed = await session.call_tool("timer_list")
            assert timer in listed.structured_content["result"]
            cancelled = await session.call_tool(
                "timer_cancel", {"timer_id": timer["timer_id"]},
            )
            assert cancelled.content[0].text == "cancelled"
            listed = await session.call_tool("timer_list")
            assert listed.structured_content == original.structured_content

    asyncio.run(inspect_timers())


def test_create_agent_is_authorized_and_does_not_dispatch(beeloop_root, monkeypatch):
    make_role(
        beeloop_root,
        "target",
        'backend = "fake"\ncwd = "workspaces/target"\n',
    )
    sender = orchestrator()
    bind(sender)
    rules(sender, roles=["target"])
    monkeypatch.setattr(messaging, "_submit", lambda *args: pytest.fail("dispatched"))

    result = server.create_agent("target")

    created = ag.restore(result["agent_id"])
    assert result == {
        "agent_id": created.agent_id,
        "role": "target",
        "cwd": str((beeloop_root / "workspaces" / "target").resolve()),
    }
    assert created.record["turns"] == 0
    assert fake.turns(created.agent_id) == []


def test_create_agent_requires_role_creation_permission(beeloop_root):
    make_role(
        beeloop_root,
        "target",
        'backend = "fake"\ncwd = "workspaces/target"\n',
    )
    sender = orchestrator()
    bind(sender)
    rules(sender, ids=["*"])

    with pytest.raises(server.MessagingError, match="not allowed to create"):
        server.create_agent("target")

    assert len(list(records.runtime("agents").glob("*/record.json"))) == 1


@pytest.mark.parametrize("role", ["worker", "router"])
def test_authorized_callers_can_choose_backend_at_server_and_core(role):
    sender = ag.create(role, cwd="project", backend="fake")
    bind(sender)
    config = {"backend": "fake", "backend_options": {"fake": {"marker": "chosen"}}}
    for call in (
        lambda: server.create_agent("orchestrator", "target", config),
        lambda: messaging.create_agent(records.agent_path(sender.agent_id), "orchestrator", "target", config),
    ):
        created = ag.restore(call()["agent_id"])
        assert created.record["backend"] == "fake"
        assert created.session().options == {"marker": "chosen"}
        assert created.session().permissions == ag.load_role("orchestrator").permissions
        assert created.record["turns"] == 0


@pytest.mark.parametrize("role", ["worker", "router", "orchestrator"])
def test_config_overrides_do_not_bypass_creation_allowlist_or_message_routes(role):
    sender = ag.create(role, cwd="project", backend="fake")
    bind(sender)
    rules(sender, ids=["*"])
    with pytest.raises(messaging.MessagingError, match="not allowed to create"):
        server.create_agent("worker", "project", {"backend": "fake"})
    rules(sender, roles=["worker"])
    with pytest.raises(messaging.MessagingError, match="unknown receiver route field"):
        server.message({"role": "worker", "cwd": "project", "config": {"backend": "fake"}}, "work")
    assert len(list(records.runtime("agents").glob("*/record.json"))) == 1


def test_router_can_still_create_orchestrator_with_defaults():
    sender = ag.create("router", backend="fake")
    bind(sender)
    result = server.create_agent("orchestrator")
    created = ag.restore(result["agent_id"])
    assert created.record["role"] == "orchestrator"
    assert created.record["backend"] == ag.load_role("orchestrator").backend
    assert "backend_options" not in created.record


def test_live_config_tool_validates_then_creates_without_dispatch():
    sender = orchestrator(backend="fake")

    async def inspect_config():
        async with live_session(sender) as session:
            for config, reason in (
                ({"status": "closed"}, "unknown config field"),
                ({"backend": "missing"}, "unknown backend"),
                ({"session_ttl": {"idle": "1h"}}, "unknown config field"),
                ({"permissions": "yolo"}, "unknown config field"),
                ({"backend": "codex", "backend_options": {"codex": {"config": []}}}, "config overrides must be a table"),
            ):
                invalid = await session.call_tool("create_agent", {"role": "worker", "cwd": "rejected", "config": config})
                assert invalid.is_error
                assert reason in invalid.content[0].text
            created = await session.call_tool("create_agent", {
                "role": "worker", "cwd": "project",
                "config": {"backend": "fake", "backend_options": {"fake": {"marker": "chosen"}}},
            })
            assert not created.is_error
            return created.structured_content["agent_id"]

    worker_id = asyncio.run(inspect_config())
    created = ag.restore(worker_id)
    assert created.session().options == {"marker": "chosen"}
    assert created.session().permissions == ag.load_role("worker").permissions
    assert created.record["turns"] == 0
    assert fake.turns(worker_id) == []
    assert not (records.root() / "rejected").exists()


@pytest.mark.parametrize("role", ["worker", "router"])
def test_live_authorized_callers_can_choose_backend(role):
    sender = ag.create(role, cwd="project", backend="fake")

    async def create():
        async with live_session(sender) as session:
            result = await session.call_tool("create_agent", {
                "role": "orchestrator", "cwd": "target", "config": {"backend": "fake"},
            })
            assert not result.is_error
            return result.structured_content["agent_id"]

    created = ag.restore(asyncio.run(create()))
    assert created.record["backend"] == "fake"
    assert created.record["role"] == "orchestrator"


def test_worker_role_allows_orchestrators_but_not_unconfigured_roles(monkeypatch):
    worker = ag.create("worker", cwd="project", backend="fake")
    bind(worker)
    submitted = []
    monkeypatch.setattr(messaging, "_submit", submitted.append)
    for _ in range(2):
        receiver = orchestrator(backend="fake")
        assert server.message(receiver.agent_id, "results") == accepted(receiver)
    assert all(item.source == f"agent:{worker.agent_id}" for item in submitted)

    planner = ag.create("planner", cwd="project", backend="fake")
    with pytest.raises(server.MessagingError, match="may not send"):
        server.message(planner.agent_id, "not allowed")
    rules(worker)
    with pytest.raises(server.MessagingError, match="may not send"):
        server.message(receiver.agent_id, "no implicit permission")


def test_live_generic_creation_and_messaging_dispatch_worker(beeloop_root):
    make_role(beeloop_root, "worker", 'backend = "fake"\n')
    sender = orchestrator(backend="fake")

    async def dispatch_worker():
        async with live_session(sender) as session:
            created = await session.call_tool("create_agent", {"role": "worker", "cwd": "project"})
            assert not created.is_error
            worker_id = created.structured_content["agent_id"]
            sent = await session.call_tool("message", {"receiver": worker_id, "msg": "work and verify"})
            assert not sent.is_error
            return worker_id

    worker_id = asyncio.run(dispatch_worker())
    wait_for(lambda: bool(fake.turns(worker_id)))
    assert fake.turns(worker_id) == [[[f"agent:{sender.agent_id}", "work and verify"]]]


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
    assert codex_server["env_vars"] == ["BEELOOP_AGENT_DIR"]


def test_the_optional_binding_comes_from_the_environment_and_must_be_absolute(
    monkeypatch,
):
    agent = orchestrator()
    monkeypatch.delenv("BEELOOP_AGENT_DIR", raising=False)
    monkeypatch.setattr(server.mcp, "run", lambda: None)
    assert server.main() == 0
    assert server.AGENT_DIRECTORY is None

    monkeypatch.setenv("BEELOOP_AGENT_DIR", "runtime/agents/whoever")
    with pytest.raises(server.MessagingError, match="must be absolute"):
        server.main()

    directory = records.agent_path(agent.agent_id).resolve()
    monkeypatch.setenv("BEELOOP_AGENT_DIR", str(directory))
    assert server.main() == 0
    assert server.AGENT_DIRECTORY == directory


def test_identity_without_binding_is_none():
    assert server.get_agent_id() is None


def test_agent_discovery_respects_receiver_permissions():
    sender, receiver = sender_and_receiver()
    rules(sender)
    assert server.list_agents() == []
    rules(sender, ids=[receiver.agent_id])
    assert [item["agent_id"] for item in server.list_agents()] == [receiver.agent_id]


def test_identity_is_read_from_the_bound_record_on_every_call():
    agent = orchestrator()
    bind(agent)
    assert server.get_agent_id() == agent.agent_id

    record = records.read(agent.agent_id)
    record["agent_id"] = "replacement-from-record"
    records.record_path(agent.agent_id).write_text(json.dumps(record), encoding="utf-8")

    assert server.get_agent_id() == "replacement-from-record"


@pytest.mark.parametrize(
    "call",
    [
        lambda: server.run_script("scripts/test.py", []),
        lambda: server.create_agent("worker", "project"),
        lambda: server.message("agent", "work"),
        server.list_agents,
        lambda: server.route_source("slack:thread", "agent"),
        lambda: server.timer_create("check", after="1h"),
        server.timer_list,
        lambda: server.timer_cancel("timer"),
    ],
)
def test_agent_operations_require_binding(call):
    with pytest.raises(server.MessagingError, match="requires a bound BeeLoop agent"):
        call()


def test_timer_tools_can_manage_only_the_bound_agents_record():
    first = orchestrator()
    second = ag.create("worker", cwd="workspaces/worker")
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
        {"roles": ["worker"]},
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
    else:
        (records.agent_path(sender.agent_id) / "config.toml").unlink()
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
        ({"role": "worker", "cwd": 1}, "cwd"),
        ({"role": "worker", "instance": []}, "instance"),
        (42, "agent ID or a route object"),
    ],
)
def test_malformed_receivers_are_refused(receiver, match, monkeypatch):
    sender = orchestrator()
    bind(sender)
    rules(sender, roles=["worker"])
    monkeypatch.setattr(messaging, "_submit", lambda *args: pytest.fail("dispatched"))

    with pytest.raises(server.MessagingError, match=match):
        server.message(receiver, "hello")


def test_live_sender_config_edits_take_effect(monkeypatch):
    sender, receiver = sender_and_receiver()
    rules(sender)
    monkeypatch.setattr(messaging, "_submit", lambda *args: None)

    with pytest.raises(server.MessagingError, match="may not send"):
        server.message(receiver.agent_id, "first")

    rules(sender, roles=["worker"])
    assert server.message(receiver.agent_id, "second") == accepted(receiver)

    rules(sender)
    with pytest.raises(server.MessagingError, match="may not send"):
        server.message(receiver.agent_id, "third")

    rules(sender, ids=[receiver.agent_id])
    assert server.message(receiver.agent_id, "fourth") == accepted(receiver)


def test_recipient_config_is_not_read(monkeypatch):
    sender, receiver = sender_and_receiver()
    rules(sender, roles=["worker"])
    recipient_config = records.agent_path(receiver.agent_id) / "config.toml"
    recipient_config.write_text(
        '[allowed_receivers]\nroles = "not a list"\n', encoding="utf-8"
    )
    monkeypatch.setattr(messaging, "_submit", lambda *args: None)

    assert server.message(receiver.agent_id, "hello") == accepted(receiver)


@pytest.mark.parametrize("field,value", [("roles", '"worker"'), ("ids", '{}')])
def test_malformed_allowed_recipient_lists_name_the_policy(field, value):
    sender, receiver = sender_and_receiver()
    config = records.agent_path(sender.agent_id) / "config.toml"
    config.write_text(
        f"[allowed_receivers]\n{field} = {value}\n", encoding="utf-8"
    )

    with pytest.raises(
        server.MessagingError,
        match=rf"allowed_receivers\.{field}",
    ):
        server.message(receiver.agent_id, "hello")


def test_the_old_messaging_policy_no_longer_authorizes_receivers(monkeypatch):
    sender, receiver = sender_and_receiver()
    config = records.agent_path(sender.agent_id) / "config.toml"
    config.write_text(
        '[messaging.allowed_recipients]\nroles = ["*"]\n', encoding="utf-8"
    )
    monkeypatch.setattr(messaging, "_submit", lambda *args: pytest.fail("dispatched"))

    with pytest.raises(server.MessagingError, match="may not send"):
        server.message(receiver.agent_id, "hello")


@pytest.mark.parametrize("grant", ["role", "id"])
def test_route_source_uses_the_shared_receiver_policy(grant):
    sender, receiver, route = routed_pair()
    rules(
        sender,
        roles=[receiver.record["role"]] if grant == "role" else None,
        ids=[receiver.agent_id] if grant == "id" else None,
    )

    assert server.route_source(route.source, receiver.agent_id) == {
        "source": route.source,
        "receiver_agent_id": receiver.agent_id,
        "receiver_role": receiver.record["role"],
    }
    assert route.resolve() == receiver.agent_id


def test_route_source_redirects_subsequent_delivery():
    sender, receiver, route = routed_pair()
    rules(sender, ids=[receiver.agent_id])

    server.route_source(route.source, receiver.agent_id)

    dsp.dispatch(
        Envelope(
            role=sender.record["role"],
            agent_id=None,
            cwd=sender.record["cwd"],
            instance=sender.record["instance"],
            backend=sender.record["backend"],
            source=route.source,
            msg="continued",
        )
    )
    assert fake.turns(receiver.agent_id)[-1][-1] == [route.source, "continued"]
    assert fake.turns(sender.agent_id) == []


def test_route_source_rejects_ephemeral_unknown_and_other_owned_sources():
    ephemeral = orchestrator()
    receiver = orchestrator(instance="primary")
    bind(ephemeral)
    rules(ephemeral, ids=[receiver.agent_id])
    with pytest.raises(server.MessagingError, match="no route owned"):
        server.route_source("slack:ephemeral", receiver.agent_id)

    sender, receiver, route = routed_pair("slack:unknown")
    rules(sender, ids=[receiver.agent_id])
    route.remove()
    with pytest.raises(server.MessagingError, match="no route owned"):
        server.route_source(route.source, receiver.agent_id)

    other = orchestrator(instance="other")
    route.new(other.agent_id)
    with pytest.raises(server.MessagingError, match="no route owned"):
        server.route_source(route.source, receiver.agent_id)


def test_route_source_requires_an_allowed_existing_receiver():
    sender, receiver, route = routed_pair()
    rules(sender)
    with pytest.raises(server.MessagingError, match="may not route"):
        server.route_source(route.source, receiver.agent_id)

    rules(sender, ids=["*"])
    receivers = [
        orchestrator(cwd="workspaces/elsewhere", instance="primary"),
        ag.create("worker", cwd=sender.record["cwd"], instance="primary"),
        orchestrator(instance="backend", backend="codex"),
    ]
    for receiver in receivers:
        route.new(sender.agent_id)
        server.route_source(route.source, receiver.agent_id)
        assert route.resolve() == receiver.agent_id

    route.new(sender.agent_id)

    with pytest.raises(ag.UnknownAgent):
        server.route_source(
            route.source, "missing-agent-02-11-26"
        )


def test_concurrent_route_transfers_have_one_winner():
    sender, receiver, route = routed_pair("slack:contended")
    rules(sender, ids=[receiver.agent_id])
    directory = records.agent_path(sender.agent_id)
    start = threading.Barrier(8)
    results = []

    def move():
        start.wait()
        try:
            messaging.route_source(directory, route.source, receiver.agent_id)
            results.append("routed")
        except messaging.MessagingError as exc:
            results.append(str(exc))

    threads = [threading.Thread(target=move) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert results.count("routed") == 1
    assert sum("no route owned" in result or "routed to another agent" in result for result in results) == 7
    assert len(records.runtime().joinpath("routes.jsonl").read_text().splitlines()) == 2


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
        server.message("missing-agent-02-11-26", "hello")


@pytest.mark.parametrize(
    "ids", [["*"], ["missing-agent-02-11-26"]]
)
def test_route_creation_requires_an_allowed_role(beeloop_root, monkeypatch, ids):
    make_role(
        beeloop_root,
        "target",
        'backend = "fake"\ncwd = "workspaces/target"\n',
    )
    sender = orchestrator()
    bind(sender)
    rules(sender, ids=ids)
    monkeypatch.setattr(messaging, "_submit", lambda *args: None)
    route = Route(
        source=f"agent:{sender.agent_id}",
        cwd=str((beeloop_root / "workspaces" / "target").resolve()),
        role="target",
        backend="fake",
        instance="one",
    )
    route.new("missing-agent-02-11-26")

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
    beeloop_root, monkeypatch, instance
):
    make_role(
        beeloop_root,
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
    beeloop_root, monkeypatch
):
    make_role(
        beeloop_root,
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


def test_named_message_routes_reuse_and_remain_distinct(beeloop_root, monkeypatch):
    make_role(
        beeloop_root,
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


def test_message_route_uses_the_shared_authorized_creator(beeloop_root, monkeypatch):
    make_role(
        beeloop_root,
        "target",
        'backend = "fake"\ncwd = "workspaces/target"\n',
    )
    sender = orchestrator()
    bind(sender)
    rules(sender, roles=["target"])
    monkeypatch.setattr(messaging, "_submit", lambda *args: None)
    original = messaging._create
    calls = []

    def create(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(messaging, "_create", create)

    server.message({"role": "target", "instance": "named"}, "hello")

    assert len(calls) == 1
    assert calls[0][0][1:] == ("target",)
    assert calls[0][1] == {
        "cwd": str((beeloop_root / "workspaces" / "target").resolve()),
        "backend": "fake",
        "instance": "named",
    }


def test_receiver_routes_reject_unknown_fields(monkeypatch):
    sender = orchestrator()
    bind(sender)
    rules(sender, roles=["worker"])
    monkeypatch.setattr(messaging, "_submit", lambda *args: None)

    with pytest.raises(server.MessagingError, match="instnace"):
        server.message({"role": "worker", "instnace": "one"}, "hello")


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
    rules(sender, roles=["worker"])
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


def test_cold_slack_reply_uses_mcp_python_before_source_transfer(beeloop_root, monkeypatch):
    source = "slack-private:D123:1712345678901234"
    router = as_fake(ag.create("router", instance="default"))
    recipient = as_fake(orchestrator())
    route = Route(source, str(router.cwd), "router", "fake", "default")
    route.new(router.agent_id)
    source_root = beeloop_root / "runtime" / "sources" / "slack-private"
    shutil.copytree(SERVER_PATH.parents[2] / "runtime" / "sources" / "slack-private", source_root)
    state = source_root / "state"
    state.mkdir(exist_ok=True)
    (state / "state.json").write_text(json.dumps({
        "sessions": {source: {"channel": "D123", "thread_ts": "1712345678.901234"}},
    }))
    (source_root / "secrets.env").write_text(
        "SLACK_APP_TOKEN=xapp-test\nSLACK_BOT_TOKEN=xoxb-test\nSLACK_ALLOWED_USER_ID=UOWNER\n"
    )
    sent = beeloop_root / "sent.json"
    (source_root / "slack_sdk.py").write_text(
        "import json\nfrom pathlib import Path\n"
        "class WebClient:\n"
        "    def __init__(self, token): assert token == 'xoxb-test'\n"
        "    def chat_postMessage(self, **kwargs):\n"
        f"        Path({str(sent)!r}).write_text(json.dumps(kwargs))\n"
        "        return {'channel': kwargs['channel'], 'ts': '1712345680.000001'}\n"
    )
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    text = "I’ll check this.\nLiteral shell text: $(exit 9) `exit 9`"

    async def acknowledge():
        async with live_session(recipient) as session:
            ok = await session.call_tool("run_script", {
                "script": "sources/slack-private/slack-private",
                "args": ["reply", "--source", source, "--text", "-"], "stdin": text,
            })
            refused = await session.call_tool("run_script", {
                "script": "sources/slack-private/slack-private",
                "args": ["reply", "--source", "slack-private:D999:1712345678901234", "--text", "-"],
                "stdin": "wrong destination",
            })
            return ok, refused

    ok, refused = asyncio.run(acknowledge())
    assert not ok.is_error
    reply = json.loads(ok.content[0].text)
    assert reply["exit_code"] == 0
    assert json.loads(reply["stdout"])["ok"] is True
    assert not refused.is_error
    refused_result = json.loads(refused.content[0].text)
    assert refused_result["exit_code"] == 1
    assert "has not been authenticated" in refused_result["stderr"]
    assert json.loads(sent.read_text()) == {
        "channel": "D123", "thread_ts": "1712345678.901234", "text": text,
    }
    # The first reply does not race source transfer; later messages bypass the router.
    assert route.resolve() == router.agent_id
    bind(router)
    server.route_source(source, recipient.agent_id)
    dsp.dispatch(Envelope(role="router", agent_id=None, cwd=str(router.cwd), backend="fake",
                          instance="default", source=source, msg="follow-up"))
    assert fake.turns(recipient.agent_id)[-1][-1] == [source, "follow-up"]
    assert fake.turns(router.agent_id) == []


@pytest.mark.parametrize("script", [
    "../outside.py", "/tmp/run.py", "python", "agents/record.py",
    "scripts/../agents/script.py", "sources/../../outside.py",
])
def test_run_script_rejects_unapproved_paths(script, monkeypatch):
    monkeypatch.setattr(scripts.subprocess, "run", lambda *a, **kw: pytest.fail("script ran"))
    with pytest.raises(scripts.ScriptError, match="script must"):
        scripts.run(script, [])


def test_run_script_refuses_symlinks_outside_approved_directories(beeloop_root):
    runtime = beeloop_root / "runtime"
    directory = runtime / "scripts"
    directory.mkdir()
    (directory / "outside").symlink_to("/bin/sh")
    (runtime / "other.py").write_text("pass")
    (directory / "disguised.py").symlink_to(runtime / "other.py")
    outside = beeloop_root / "outside"
    outside.mkdir()
    (outside / "script.py").write_text("pass")
    (runtime / "sources").symlink_to(outside)
    for script in ("scripts/outside", "scripts/disguised.py", "sources/script.py"):
        with pytest.raises(scripts.ScriptError, match="script must"):
            scripts.run(script, [])


def test_run_script_timeout_does_not_retry(beeloop_root, monkeypatch):
    directory = beeloop_root / "runtime" / "scripts"
    directory.mkdir()
    (directory / "test.py").touch()
    calls = []
    def timeout(*args, **kwargs):
        calls.append(args)
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
    monkeypatch.setattr(scripts.subprocess, "run", timeout)
    with pytest.raises(scripts.ScriptError, match="outcome is unknown"):
        scripts.run("scripts/test.py", [])
    assert len(calls) == 1


def test_run_script_executes_python_without_chmod_and_keeps_literal_arguments(beeloop_root):
    runtime = beeloop_root / "runtime"
    directory = runtime / "scripts"
    directory.mkdir()
    script = directory / "test.py"
    script.write_text(
        "import json, os, sys\n"
        "print(json.dumps({'argv': sys.argv[1:], 'stdin': sys.stdin.read(), "
        "'cwd': os.getcwd(), 'prefix': sys.prefix}))\n"
        "print('expected diagnostic', file=sys.stderr)\n"
        "sys.exit(7)\n"
    )
    script.chmod(0o644)
    args = ["hello world", "$(exit 9)", "`exit 9`", "--option=value"]
    text = "First line\nLiteral $HOME and `command`"
    agent = orchestrator()

    async def invoke():
        async with live_session(agent) as session:
            result = await session.call_tool("run_script", {
                "script": "scripts/test.py", "args": args, "stdin": text,
            })
            without_input = await session.call_tool("run_script", {"script": "scripts/test.py"})
            return result, without_input

    result, without_input = asyncio.run(invoke())
    assert not result.is_error
    output = json.loads(result.content[0].text)
    assert output["exit_code"] == 7
    assert output["stderr"] == "expected diagnostic\n"
    assert json.loads(output["stdout"]) == {
        "argv": args, "stdin": text, "cwd": str(beeloop_root), "prefix": sys.prefix,
    }
    assert not without_input.is_error
    output = json.loads(without_input.content[0].text)
    assert json.loads(output["stdout"])["stdin"] == ""
    assert json.loads(output["stdout"])["argv"] == []


@pytest.mark.parametrize("role", ["worker", "router", None])
def test_run_script_rejects_non_orchestrator_callers(role, beeloop_root):
    agent = ag.create(role, cwd="workspaces/test") if role else None
    async def invoke():
        async with live_session(agent) as session:
            return await session.call_tool("run_script", {"script": "scripts/test.py", "args": []})
    result = asyncio.run(invoke())
    assert result.is_error
    assert "bound" in result.content[0].text


def test_run_script_reads_current_role_and_refuses_external_agent_bindings(beeloop_root):
    agent = orchestrator()
    bind(agent)
    with pytest.raises(scripts.ScriptError, match="script must"):
        server.run_script("../no.py", [])
    records.update(agent.agent_id, agent.SCHEMA, {"role": "worker"})
    with pytest.raises(server.MessagingError, match="bound orchestrator"):
        server.run_script("scripts/test.py", [])
    outside = beeloop_root / "outside-agent"
    outside.mkdir()
    (outside / "record.json").write_text(json.dumps({
        "agent_id": agent.agent_id, "role": "orchestrator", "cwd": str(agent.cwd), "backend": "fake",
    }))
    server.AGENT_DIRECTORY = outside
    with pytest.raises(server.MessagingError, match="runtime/agents"):
        server.run_script("scripts/test.py", [])


def test_run_script_reports_a_non_executable_wrapper(beeloop_root):
    directory = beeloop_root / "runtime" / "scripts"
    directory.mkdir()
    helper = directory / "wrapper"
    helper.write_text("#!/bin/sh\nexit 0\n")
    helper.chmod(0o644)
    with pytest.raises(scripts.ScriptError, match="cannot run script"):
        scripts.run("scripts/wrapper")
