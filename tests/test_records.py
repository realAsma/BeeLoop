"""The one door into an agent, and the schema that guards what goes through it."""

from __future__ import annotations

import pytest

from beebot import agents as ag
from beebot.agents import agent as agent_impl
from beebot.agents import records, roles
from tests.conftest import as_fake, orchestrator, poke, worker


# ------------------------------------------------------------------- the door


def test_an_agent_id_continues_and_a_role_creates():
    made = orchestrator()
    again = ag.Agent(made.agent_id)
    assert again.record == made.record


def test_naming_an_agent_that_does_not_exist_raises_rather_than_creating_one():
    """A silent cold start would strand whatever was pointing at the old id."""
    missing = "0198ff2a-0000-7000-8000-000000000000"
    with pytest.raises(ag.UnknownAgent, match="runtime/agents"):
        ag.Agent(missing)
    assert not records.record_path(missing).exists()


def test_an_agent_id_and_a_role_together_are_refused():
    """They mean opposite things, so precedence would just hide a caller bug."""
    made = orchestrator()
    with pytest.raises(ag.AgentError, match="never both"):
        ag.Agent(made.agent_id, role="orchestrator")


def test_an_agent_needs_one_of_them():
    with pytest.raises(ag.AgentError, match="either an agent_id"):
        ag.Agent()


# ------------------------------------------------------------------ the types


def test_the_default_type_is_the_ordinary_class_by_name():
    """The constant is spelled out because `Role`'s field default is evaluated
    before `Agent` exists. This is what stops the literal rotting."""
    assert roles.DEFAULT_TYPE == ag.Agent.__name__
    assert agent_impl.REGISTRY[roles.DEFAULT_TYPE] is ag.Agent


def test_a_role_that_names_no_type_is_an_ordinary_agent():
    """Adding a role is still just a directory: `worker` names no class and has
    an empty config file, and it resolves anyway -- to the default type, with
    the directory name kept in `role`."""
    made = worker()
    assert type(made) is ag.Agent
    assert made.record["type"] == roles.DEFAULT_TYPE
    assert made.record["role"] == "worker"


def test_a_role_naming_a_registered_type_builds_that_class(beebot_root):
    """The escape hatch for behaviour that config cannot express: an envelope
    names a role, and the role says which class runs it."""

    class Specialist(ag.Agent):
        pass

    ag.register(Specialist)
    (beebot_root / "configs" / "roles" / "worker" / "role.toml").write_text(
        'type = "Specialist"\n', encoding="utf-8"
    )
    try:
        assert type(worker()) is Specialist
        assert agent_impl.agent_class(roles.DEFAULT_TYPE) is ag.Agent
    finally:
        del agent_impl.REGISTRY["Specialist"]


def test_a_role_naming_a_type_nothing_registers_is_refused_and_named(beebot_root):
    """A misspelled `type = "Persistant"` must fail loudly rather than hand back
    an ordinary agent that then quietly never refreshes."""
    (beebot_root / "configs" / "roles" / "worker" / "role.toml").write_text(
        'type = "Persistant"\n', encoding="utf-8"
    )
    with pytest.raises(ag.AgentError, match="Persistant") as raised:
        worker()
    assert "'worker'" in str(raised.value), "the message must name the role"


def test_restore_rebuilds_whatever_type_the_record_says():
    made = worker()
    again = ag.restore(made.agent_id)
    assert type(again) is ag.Agent
    assert again.record["type"] == roles.DEFAULT_TYPE


# ----------------------------------------------------------------- the schema


def test_a_misspelled_field_is_refused_instead_of_silently_written():
    """The bug this schema exists for.

    Before it, `{"statuss": CLOSED}` wrote a junk field, left `status` alone,
    and left the agent open forever -- with nothing anywhere reporting a
    problem.
    """
    agent = as_fake(worker())
    with pytest.raises(ag.AgentError, match="statuss"):
        poke(agent, {"statuss": records.CLOSED})

    assert records.read(agent.agent_id)["status"] != records.CLOSED


def test_a_status_outside_the_enum_is_refused():
    agent = as_fake(worker())
    with pytest.raises(ag.AgentError, match="not one of"):
        poke(agent, {"status": "finished"})


def test_a_negative_turn_count_is_refused():
    agent = as_fake(worker())
    with pytest.raises(ag.AgentError, match="minimum"):
        poke(agent, {"turns": -1})


def test_an_agent_may_not_carry_a_task():
    """spec 9 says an ordinary agent has no task, and nothing said so until now.

    The class that DOES own one brings its own schema file alongside this one,
    which is why the split is per file: adding it edits nothing here.
    """
    agent = as_fake(worker())
    with pytest.raises(ag.AgentError, match="task_name"):
        poke(agent, {"task_name": "build-state-store"})


def test_an_unknown_schema_name_says_what_there_is():
    with pytest.raises(ag.AgentError, match="assets/ has Agent"):
        records.validate({}, "NoSuchSchema")
