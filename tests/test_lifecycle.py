"""Who owns the life of a backend session.

The agent does, not the dispatcher: a caller hands an agent a batch and should
never have to know that the conversation underneath it can die.

Workers close after a terminal session loss. Persistent router and orchestrator
agents recover under the same identity, with at most one fresh-session retry.
"""

from __future__ import annotations

import pytest

from beeloop import agents as ag
from beeloop.agents import records, session_ttl
from beeloop.agents.backends import BackendError, InputItem, fake
from tests.conftest import as_fake, worker


def test_a_plain_agent_that_loses_its_session_dies():
    """A worker has nothing to brief a replacement from, so terminal is death."""
    agent = as_fake(worker())
    fake.fail_session(agent.session_id, "terminal: no conversation found")

    with pytest.raises(ag.NotResumable, match="lost its session"):
        agent.spin([InputItem("slack:D0B8:1", "hello")])

    assert records.read(agent.agent_id)["status"] == records.CLOSED


def test_a_failure_that_is_not_terminal_is_not_a_death():
    """Calling a blip terminal would throw away a live conversation."""
    agent = as_fake(worker())
    fake.fail_session(agent.session_id, "connection reset by peer")

    with pytest.raises(Exception) as caught:
        agent.spin([InputItem("slack:D0B8:1", "hello")])

    assert not isinstance(caught.value, ag.NotResumable)
    assert records.read(agent.agent_id)["status"] != records.CLOSED


@pytest.mark.parametrize("role", ["router", "orchestrator"])
def test_persistent_agent_recovers_lost_session_under_same_identity(role):
    agent = ag.create(role, backend="fake")
    agent.spin([InputItem("source", "earlier work")])
    previous_session = agent.session_id
    fake.fail_session(previous_session, "terminal: session not found")

    result = agent.spin([InputItem("source", "pending intake")])

    restored = ag.restore(agent.agent_id)
    assert restored.session_id != previous_session
    assert restored.status == "active"
    assert restored.record["turns"] == 2
    assert restored.record["session_turns"] == 1
    assert result.text.endswith("pending intake")
    recovery = fake.turns(agent.agent_id)[-1]
    assert recovery[0][0] == session_ttl.RESTORE_SOURCE
    assert agent.agent_id in recovery[0][1]
    assert str(agent.cwd) in recovery[0][1]
    assert agent.role.prompt("session_recover") in recovery[0][1]
    assert recovery[-1] == ["source", "pending intake"]


def test_persistent_recovery_retains_existing_handoff_and_bounds_retry(monkeypatch):
    agent = ag.create("orchestrator", backend="fake")
    session_ttl.write_handoff(agent.agent_id, "saved-work record and useful context")
    attempts = []

    def fail(session, inputs):
        attempts.append(session.session_id)
        raise BackendError("terminal: session not found")

    monkeypatch.setattr(agent.backend, "deliver", fail)
    with pytest.raises(BackendError, match="session not found"):
        agent.spin([InputItem("source", "pending intake")])

    assert len(attempts) == 2
    assert attempts[0] != attempts[1]
    assert records.read(agent.agent_id)["status"] == records.DORMANT
    assert session_ttl.read_handoff(agent.agent_id) == "saved-work record and useful context"


def test_transient_replacement_failure_keeps_recovery_context_for_next_attempt(monkeypatch):
    agent = ag.create("orchestrator", backend="fake")
    lost_session = agent.session_id
    fake.fail_session(lost_session, "terminal: session not found")
    deliver = agent.backend.deliver

    def fail_replacement(session, inputs):
        if session.session_id != lost_session:
            raise BackendError("connection reset")
        return deliver(session, inputs)

    monkeypatch.setattr(agent.backend, "deliver", fail_replacement)
    with pytest.raises(BackendError, match="connection reset"):
        agent.spin([InputItem("source", "pending intake")])

    assert agent.status == records.DORMANT
    handoff = session_ttl.read_handoff(agent.agent_id)
    assert agent.agent_id in handoff
    monkeypatch.setattr(agent.backend, "deliver", deliver)
    agent.spin([InputItem("source", "retry intake")])
    assert handoff in fake.turns(agent.agent_id)[-1][0][1]
    assert agent.status == "active"
