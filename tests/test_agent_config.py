"""Backend-only creation settings preserve role policies across sessions."""

from __future__ import annotations

from copy import deepcopy

import pytest

from beeloop import agents as ag
from beeloop.agents import agent as agent_module, backends, records, session_ttl
from beeloop.agents import timers as agent_timers
from beeloop.agents.backends import BackendError, InputItem, fake
from beeloop.agents.backends.codex import _argv
from beeloop.agents.roles import RoleConfigError
from beeloop.dispatch import messaging
from tests.conftest import make_role


@pytest.mark.parametrize("backend", ["fake", "codex", "claude_code"])
@pytest.mark.parametrize("permissions", ["read", "edit", "approve_for_me", "yolo"])
def test_shared_profiles_remain_role_owned(backend, permissions, beeloop_root):
    make_role(beeloop_root, "worker", f'permissions = "{permissions}"\n')
    agent = ag.create("worker", cwd="project", config={
        "backend": backend,
    })
    assert agent.session().permissions == permissions
    assert ag.restore(agent.agent_id).session().permissions == permissions


@pytest.mark.parametrize("backend", ["fake", "codex", "claude_code"])
@pytest.mark.parametrize("permissions", ["read_only", "full_access", "root"])
def test_invalid_role_profiles_fail_before_allocation(
    backend, permissions, beeloop_root, monkeypatch,
):
    make_role(beeloop_root, "worker", f'backend = "{backend}"\npermissions = "{permissions}"\n')
    monkeypatch.setattr(agent_module, "new_agent_id", lambda *_: pytest.fail("allocated an agent"))
    with pytest.raises(BackendError, match="invalid permission profile"):
        ag.create("worker", cwd="must-not-exist")
    assert not (records.root() / "must-not-exist").exists()


def test_backend_options_overlay_without_mutating_role_or_other_agents(beeloop_root):
    directory = make_role(
        beeloop_root, "configured",
        'backend = "fake"\ncwd = "default-work"\npermissions = "read"\n'
        '[backend_options.fake]\nmarker = "default"\n'
        '[backend_options.fake.config]\nkeep = true\nvalues = ["old"]\n'
        '[prompts]\nsession_init = "default init"\ncustom = "keep"\n'
        '[allowed_receivers]\nroles = ["orchestrator"]\nids = ["original"]\n',
    )
    original = (directory / "role.toml").read_bytes()
    config = {"backend_options": {"fake": {"config": {"values": ["new"]}}}}
    requested = deepcopy(config)
    agent = ag.create("configured", cwd="caller-work", config=config)
    config["backend_options"]["fake"]["config"]["values"].append("mutation")
    other = ag.create("configured")

    assert agent.cwd == beeloop_root / "caller-work"
    assert agent.session().permissions == "read"
    assert agent.session().options == {"marker": "default", "config": {"values": ["new"]}}
    assert agent.role.prompts == {"session_init": "default init", "custom": "keep"}
    assert agent.record["backend_options"] == requested["backend_options"]
    allowed = messaging._allowed_receivers(records.agent_path(agent.agent_id) / "config.toml")
    assert allowed.roles == {"orchestrator"}
    assert allowed.ids == {"original"}
    assert other.session().options["config"]["values"] == ["old"]
    assert other.session().permissions == "read"
    assert (directory / "role.toml").read_bytes() == original

    (directory / "role.toml").write_text(
        'backend = "codex"\ncwd = "new-default"\npermissions = "edit"\n'
        '[backend_options.fake]\nmarker = "refreshed"\n'
        '[backend_options.fake.config]\nkeep = false\nvalues = ["later"]\n'
        '[prompts]\ncustom = "refreshed hook"\n',
    )
    restored = ag.restore(agent.agent_id)
    assert restored.record["backend"] == "fake"
    assert restored.cwd == agent.cwd
    assert restored.session().permissions == "edit"
    assert restored.session().options == {"marker": "refreshed", "config": {"values": ["new"]}}
    assert restored.role.prompts == {"custom": "refreshed hook"}
    assert messaging._allowed_receivers(records.agent_path(agent.agent_id) / "config.toml") == allowed


def test_backend_and_native_options_reach_fresh_and_resumed_codex_argv(monkeypatch, beeloop_root):
    make_role(beeloop_root, "worker", 'permissions = "read"\n')
    monkeypatch.setenv("BEELOOP_CODEX_BIN", "/unused/codex")
    config = {
        "backend": "codex",
        "backend_options": {"codex": {"model": "gpt-6-astra", "config": {"feature": ["a", "b"]}}},
    }
    agent = ag.create("worker", cwd="project", config=config)
    for active in (False, True):
        if active:
            records.update(agent.agent_id, agent.SCHEMA, {"status": "active"})
        restored = ag.restore(agent.agent_id)
        argv = _argv(restored.session(), "work")
        assert argv[argv.index("--model") + 1] == "gpt-6-astra"
        assert argv[argv.index("--sandbox") + 1] == "read-only"
        assert argv[argv.index("-c") + 1] == 'feature=["a", "b"]'
        assert ("resume" in argv) == active


def test_backend_options_survive_role_owned_lifecycle(monkeypatch, beeloop_root):
    make_role(beeloop_root, "worker",
        'permissions = "edit"\n[session_ttl]\nidle = "1h"\n'
        '[heartbeat]\nevery = "2h"\n'
        '[prompts]\nsession_init = "initialize"\nsession_expire = "save"\n'
        'session_resume = "restore"\nheartbeat = "check"\n',
    )
    config = {"backend": "fake", "backend_options": {"fake": {"marker": "retained"}}}
    agent = ag.create("worker", cwd="project", config=config)
    scheduled = agent_timers.list_wakes(agent.agent_id)
    heartbeat = next(timer for timer in scheduled if not session_ttl.is_expiry_timer(timer))
    assert heartbeat.every_seconds == 7200
    assert heartbeat.payload == {"message": "check"}
    agent.spin([InputItem("user", "work")])
    assert fake.turns(agent.agent_id)[0] == [["session-init", "initialize"], ["user", "work"]]
    old_session = agent.session_id
    ttl = next(timer for timer in agent_timers.list_wakes(agent.agent_id) if session_ttl.is_expiry_timer(timer))
    monkeypatch.setattr(agent_module, "now", lambda: ttl.due_at)
    agent = ag.restore(agent.agent_id)
    agent.spin([InputItem("session-ttl:expiry", "save")])
    assert agent.status == records.DORMANT
    agent = ag.restore(agent.agent_id)
    assert agent.ttl_policy == session_ttl.Policy(idle_seconds=3600)
    agent.spin([InputItem("user", "continue")])
    assert agent.session_id != old_session
    assert agent.session().options == {"marker": "retained"}
    assert agent.session().permissions == "edit"
    assert fake.turns(agent.agent_id)[-1][0][1].startswith("restore\n\nPrevious session handoff:")
    assert any(session_ttl.is_expiry_timer(timer) for timer in agent_timers.list_wakes(agent.agent_id))


def test_backend_override_uses_role_type(monkeypatch, beeloop_root):
    class Specialist(ag.Agent):
        pass

    monkeypatch.setitem(agent_module.REGISTRY, "Specialist", Specialist)
    make_role(beeloop_root, "worker", 'type = "Specialist"\n')
    agent = ag.create("worker", cwd="project", config={"backend": "fake"})
    assert type(agent) is Specialist
    assert type(ag.restore(agent.agent_id)) is Specialist


def test_backend_validation_uses_the_registry(monkeypatch):
    monkeypatch.setitem(backends._BACKENDS, "test-backend", ".fake:FakeBackend")
    agent = ag.create("worker", cwd="project", config={
        "backend": "test-backend", "backend_options": {"test-backend": {"native": [1, True]}},
    })
    assert agent.record["backend"] == "test-backend"
    assert agent.session().options == {"native": [1, True]}


@pytest.mark.parametrize("config", [
    [], {"backend": None}, {"backend": 1}, {"backend": "missing"},
    {"backend_options": []}, {"backend_options": {"fake": []}},
    {"backend_options": {"missing": {}}},
    {"backend": "codex", "backend_options": {"codex": {"model": 42}}},
    {"backend": "codex", "backend_options": {"codex": {"config": []}}},
    {"backend": "codex", "backend_options": {"codex": {"config": {"bad": {}}}}},
    {"backend_options": {"fake": {"bad": float("nan")}}},
    {"backend_options": {"fake": {1: "not a JSON field"}}},
    {"backend_options": {"fake": {"not_an_array": (1, 2)}}},
    {"type": "Agent"}, {"cwd": "elsewhere"}, {"permissions": "yolo"},
    {"prompts": {}}, {"session_ttl": None}, {"heartbeat": None},
    {"allowed_receivers": {"roles": ["*"]}}, {"instance": "experiment"},
    {"agent_id": "injected"}, {"session_id": "injected"}, {"status": "closed"},
    {"timers": []}, {"turns": 10}, {"unknown": True},
])
def test_invalid_config_fails_before_allocation_or_workspace_creation(config, monkeypatch):
    monkeypatch.setattr(agent_module, "new_agent_id", lambda *_: pytest.fail("allocated an agent"))
    monkeypatch.setattr(fake.FakeBackend, "open", lambda *_: pytest.fail("opened a session"))
    with pytest.raises((RoleConfigError, BackendError)):
        ag.create("worker", cwd="must-not-exist", config=config)
    assert not (records.root() / "must-not-exist").exists()
    assert not list((records.root() / "runtime/agents").glob("*"))


def test_backend_override_keeps_role_receiver_policy(monkeypatch):
    agent = ag.create("worker", cwd="project", config={"backend": "fake"})
    receiver = ag.create("orchestrator", backend="fake")
    other_role = ag.create("worker", cwd="other", backend="fake")
    submitted = []
    monkeypatch.setattr(messaging, "_submit", submitted.append)
    messaging.send(records.agent_path(agent.agent_id), receiver.agent_id, "result")
    assert submitted[0].agent_id == receiver.agent_id
    with pytest.raises(messaging.MessagingError, match="may not send"):
        messaging.send(records.agent_path(agent.agent_id), other_role.agent_id, "denied")
