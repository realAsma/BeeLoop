"""Per-agent role overrides share validation and survive session changes."""

from __future__ import annotations

from copy import deepcopy

import pytest

from beeloop import agents as ag
from beeloop.agents import agent as agent_module, backends, records, session_ttl
from beeloop.agents import timers as agent_timers
from beeloop.agents.backends import BackendError, InputItem, fake
from beeloop.agents.backends.codex import _argv
from beeloop.dispatch import messaging
from tests.conftest import make_role


def test_overrides_merge_without_mutating_defaults_or_other_agents(beeloop_root):
    directory = make_role(
        beeloop_root, "configured",
        'backend = "fake"\ncwd = "default-work"\npermissions = "read"\n'
        '[backend_options.fake]\nmarker = "default"\n'
        '[backend_options.fake.config]\nkeep = true\nvalues = ["old"]\n'
        '[prompts]\nsession_init = "default init"\ncustom = "keep"\n'
        '[allowed_receivers]\nroles = ["orchestrator"]\nids = ["original"]\n',
    )
    original = (directory / "role.toml").read_bytes()
    config = {
        "cwd": "config-work", "permissions": "edit", "instance": "experiment",
        "backend_options": {"fake": {"config": {"values": ["new"]}}},
        "prompts": {"session_init": "custom init"},
        "allowed_receivers": {"roles": ["worker"]},
    }
    requested = deepcopy(config)
    agent = ag.create("configured", cwd="caller-work", config=config)
    config["backend_options"]["fake"]["config"]["values"].append("mutation")
    other = ag.create("configured")

    assert agent.cwd == beeloop_root / "caller-work"
    assert agent.session().permissions == "edit"
    assert agent.session().options == {"marker": "default", "config": {"keep": True, "values": ["new"]}}
    assert agent.role.prompts == {"session_init": "custom init", "custom": "keep"}
    assert agent.record["config"] == requested
    assert agent.record["instance"] == "experiment"
    assert not (beeloop_root / "runtime/routes.jsonl").exists()
    allowed = messaging._allowed_receivers(records.agent_path(agent.agent_id) / "config.toml")
    assert allowed.roles == {"worker"}
    assert allowed.ids == {"original"}
    assert other.session().options["config"]["values"] == ["old"]
    assert other.session().permissions == "read"
    assert (directory / "role.toml").read_bytes() == original

    (directory / "role.toml").write_text(
        'backend = "codex"\ncwd = "new-default"\npermissions = "read"\n'
        '[backend_options.fake]\nmarker = "refreshed"\n'
        '[backend_options.fake.config]\nkeep = false\nvalues = ["later"]\n'
        '[prompts]\ncustom = "refreshed hook"\n',
    )
    restored = ag.restore(agent.agent_id)
    assert restored.record["backend"] == "fake"
    assert restored.cwd == agent.cwd
    assert restored.session().permissions == "edit"
    assert restored.session().options == {"marker": "refreshed", "config": {"keep": False, "values": ["new"]}}
    assert restored.role.prompts == {"session_init": "custom init", "custom": "refreshed hook"}
    assert messaging._allowed_receivers(records.agent_path(agent.agent_id) / "config.toml") == allowed


def test_backend_and_native_options_reach_fresh_and_resumed_codex_argv(monkeypatch):
    monkeypatch.setenv("BEELOOP_CODEX_BIN", "/unused/codex")
    config = {
        "backend": "codex", "permissions": "read",
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


def test_ttl_heartbeat_and_prompts_survive_dormancy_and_fresh_session(monkeypatch):
    config = {
        "backend": "fake", "permissions": "edit",
        "backend_options": {"fake": {"marker": "retained"}},
        "session_ttl": {"idle": "1h"}, "heartbeat": {"every": "2h"},
        "prompts": {"session_init": "initialize", "session_expire": "save",
                    "session_resume": "restore", "heartbeat": "check"},
    }
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


def test_null_disables_supported_defaults_and_config_cwd_is_a_default():
    agent = ag.create("orchestrator", config={
        "backend": "fake", "session_ttl": None, "heartbeat": None,
        "prompts": None, "cwd": "configured-work",
    })
    assert agent.cwd == records.root() / "configured-work"
    assert ag.restore(agent.agent_id).ttl_policy is None
    assert agent.role.prompts == {}
    assert agent_timers.list_wakes(agent.agent_id) == []
    with pytest.raises(ag.AgentError, match="neither role"):
        ag.create("orchestrator", config={"cwd": None})


def test_type_override_uses_registered_class_and_stays_pinned(monkeypatch):
    class Specialist(ag.Agent):
        pass

    monkeypatch.setitem(agent_module.REGISTRY, "Specialist", Specialist)
    agent = ag.create("worker", cwd="project", config={"type": "Specialist", "backend": "fake"})
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
    [], {"agent_id": "injected"}, {"session_id": "injected"}, {"status": "closed"},
    {"timers": []}, {"turns": 10}, {"unknown": True},
    {"type": []}, {"type": "MissingClass"}, {"backend": 1}, {"backend": "missing"},
    {"permissions": False}, {"backend": "codex", "permissions": "missing"},
    {"cwd": []}, {"cwd": ""}, {"backend_options": []},
    {"backend_options": {"fake": []}}, {"backend_options": {"missing": {}}},
    {"backend": "codex", "backend_options": {"codex": {"model": 42}}},
    {"backend": "codex", "backend_options": {"codex": {"config": []}}},
    {"backend": "codex", "backend_options": {"codex": {"config": {"bad": {}}}}},
    {"session_ttl": []}, {"session_ttl": {}}, {"session_ttl": {"idle": "bad"}},
    {"session_ttl": {"other": "1h"}}, {"heartbeat": "1h"}, {"heartbeat": {"every": 0}},
    {"heartbeat": {"every": "1h", "other": True}}, {"prompts": []}, {"prompts": {"hook": 1}},
    {"allowed_receivers": []}, {"allowed_receivers": {"roles": "worker"}},
    {"allowed_receivers": {"ids": [1]}}, {"allowed_receivers": {"other": []}},
    {"instance": "fresh"}, {"instance": []}, {"backend_options": {"fake": {"bad": float("nan")}}},
    {"backend_options": {"fake": {1: "not a JSON field"}}},
    {"backend_options": {"fake": {"not_an_array": (1, 2)}}},
])
def test_invalid_config_fails_before_allocation_or_workspace_creation(config, monkeypatch):
    monkeypatch.setattr(agent_module, "new_agent_id", lambda *_: pytest.fail("allocated an agent"))
    monkeypatch.setattr(fake.FakeBackend, "open", lambda *_: pytest.fail("opened a session"))
    with pytest.raises((ag.AgentError, BackendError)):
        ag.create("worker", cwd="must-not-exist", config=config)
    assert not (records.root() / "must-not-exist").exists()
    assert not list((records.root() / "runtime/agents").glob("*"))


def test_receiver_override_is_the_workers_live_messaging_policy(monkeypatch):
    agent = ag.create("worker", cwd="project", config={
        "backend": "fake", "allowed_receivers": {"roles": ["worker"]},
    })
    receiver = ag.create("worker", cwd="other", backend="fake")
    other_role = ag.create("orchestrator", backend="fake")
    submitted = []
    monkeypatch.setattr(messaging, "_submit", submitted.append)
    messaging.send(records.agent_path(agent.agent_id), receiver.agent_id, "result")
    assert submitted[0].agent_id == receiver.agent_id
    with pytest.raises(messaging.MessagingError, match="may not send"):
        messaging.send(records.agent_path(agent.agent_id), other_role.agent_id, "denied")
