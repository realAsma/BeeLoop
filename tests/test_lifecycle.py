"""Who owns the life of a backend session.

The agent does, not the dispatcher: a caller hands an agent a batch and should
never have to know that the conversation underneath it can die.

Only the death case is here, because only one type exists. The type that
SURVIVES a lost session -- by refreshing under the same agent_id -- arrives with
its own class and its own schema, and will bring the mirror of these tests.
"""

from __future__ import annotations

import pytest

from beebot import agents as ag
from beebot.agents import records
from beebot.agents.backends import InputItem, fake
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
