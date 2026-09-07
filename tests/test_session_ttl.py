"""Role-configured session expiry and continuation."""

from __future__ import annotations

import datetime as dt

import pytest

import beebot.agents.agent as agent_module
from beebot import agents as ag
from beebot import timers
from beebot.agents import records, session_ttl
from beebot.agents import timers as agent_timers
from beebot.agents.backends import InputItem, fake
from beebot.dispatch.dispatch import dispatch
from tests.conftest import as_fake, make_role, orchestrator, worker


def ttl_timer(agent):
    return next(
        timer
        for timer in agent_timers.list_wakes(agent.agent_id)
        if session_ttl.is_expiry_timer(timer)
    )


def test_orchestrator_starts_with_its_role_lifecycle_timers():
    agent = orchestrator()

    scheduled = agent_timers.list_wakes(agent.agent_id)
    ttl = ttl_timer(agent)
    heartbeat = next(
        timer for timer in scheduled if not session_ttl.is_expiry_timer(timer)
    )

    assert len(scheduled) == 2
    assert ttl.payload == {
        "message": agent.role.prompt("session_expire"),
        "_source": session_ttl.EXPIRY_SOURCE,
    }
    assert ttl.every_seconds is None
    assert ttl.due_at == timers.timestamp(
        timers.parse_timestamp(agent.record["session_since"])
        + dt.timedelta(hours=1)
    )
    assert heartbeat.payload == {"message": agent.role.prompt("heartbeat")}
    assert heartbeat.every_seconds == 14400
    assert heartbeat.until is None


def test_role_ttl_requires_a_supported_positive_duration(beebot_root):
    make_role(
        beebot_root,
        "broken-ttl",
        'backend = "fake"\ncwd = "workspaces/broken"\n'
        "[session_ttl]\n"
        'idle = "0h"\n',
    )

    with pytest.raises(ag.UnknownRole, match="positive integer"):
        ag.load_role("broken-ttl")


@pytest.mark.parametrize(
    "config, expected",
    [
        ({"idle": "2h"}, session_ttl.Policy(idle_seconds=7200)),
        ({"max_age": "1d"}, session_ttl.Policy(max_age_seconds=86400)),
        (
            {"idle": "2h", "max_age": "1d"},
            session_ttl.Policy(idle_seconds=7200, max_age_seconds=86400),
        ),
    ],
)
def test_ttl_policy_accepts_each_supported_limit(config, expected):
    assert session_ttl.parse_policy(config) == expected


def test_a_role_without_ttl_has_no_lifecycle_timer():
    agent = worker()

    assert agent.ttl_policy is None
    assert agent_timers.list_wakes(agent.agent_id) == []


def test_a_role_without_ttl_treats_the_expiry_words_as_ordinary_input():
    agent = as_fake(worker())

    agent.spin([InputItem("user", session_ttl.EXPIRY_MESSAGE)])

    assert fake.turns(agent.agent_id) == [
        [["user", session_ttl.EXPIRY_MESSAGE]]
    ]


def test_ttl_requires_the_typed_timer_source():
    agent = as_fake(orchestrator())

    agent.spin([InputItem("user", session_ttl.EXPIRY_MESSAGE)])

    assert (fake.turns(agent.agent_id), agent.status) == (
        [
            [
                [session_ttl.INIT_SOURCE, agent.role.prompt("session_init")],
                ["user", session_ttl.EXPIRY_MESSAGE],
            ]
        ],
        "active",
    )


def test_custom_expiry_and_resume_prompts_are_used(beebot_root):
    make_role(
        beebot_root,
        "custom-lifecycle",
        'backend = "fake"\ncwd = "workspaces/custom"\n'
        "[session_ttl]\n"
        'idle = "1h"\n'
        "[prompts]\n"
        'session_expire = "custom expiry"\n'
        'session_resume = "custom resume"\n',
    )
    agent = ag.create("custom-lifecycle")

    assert agent_timers.list_wakes(agent.agent_id)[0].payload == {
        "message": "custom expiry",
        "_source": session_ttl.EXPIRY_SOURCE,
    }
    agent.spin(
        [
            InputItem(f"{session_ttl.EXPIRY_SOURCE}:ttl", "custom expiry"),
            InputItem("user", "continue"),
        ]
    )

    turns = fake.turns(agent.agent_id)
    assert turns[0] == [[f"{session_ttl.EXPIRY_SOURCE}:ttl", "custom expiry"]]
    assert turns[1][0][0] == session_ttl.RESTORE_SOURCE
    assert turns[1][0][1].startswith(
        "custom resume\n\nPrevious session handoff:\n"
    )
    assert turns[1][1] == ["user", "continue"]


def test_blank_expiry_prompt_disables_ttl(beebot_root):
    make_role(
        beebot_root,
        "no-expiry",
        'backend = "fake"\ncwd = "workspaces/no-expiry"\n'
        "[session_ttl]\n"
        'idle = "1h"\n'
        "[prompts]\n"
        'session_expire = "   "\n',
    )
    agent = ag.create("no-expiry")

    assert agent_timers.list_wakes(agent.agent_id) == []
    agent.spin([InputItem("user", session_ttl.EXPIRY_MESSAGE)])
    assert fake.turns(agent.agent_id) == [
        [["user", session_ttl.EXPIRY_MESSAGE]]
    ]


def test_successful_activity_moves_idle_expiry_but_preserves_maximum(monkeypatch):
    agent = as_fake(orchestrator())
    since = timers.parse_timestamp(agent.record["session_since"])
    one_hour_later = since + dt.timedelta(hours=1)
    monkeypatch.setattr(agent_module, "now", lambda: timers.timestamp(one_hour_later))

    agent.spin([InputItem("user", "work")])

    scheduled = ttl_timer(agent)
    assert scheduled.due_at == timers.timestamp(since + dt.timedelta(hours=2))


def test_maximum_age_caps_idle_rescheduling(monkeypatch):
    agent = as_fake(orchestrator())
    since = timers.parse_timestamp(agent.record["session_since"])
    almost_max_age = since + dt.timedelta(hours=23)
    monkeypatch.setattr(agent_module, "now", lambda: timers.timestamp(almost_max_age))

    agent.spin([InputItem("user", "late activity")])

    scheduled = ttl_timer(agent)
    assert scheduled.due_at == timers.timestamp(since + dt.timedelta(hours=24))


def test_cancelling_ttl_prevents_an_ordinary_turn_from_recreating_it():
    agent = as_fake(orchestrator())
    scheduled = ttl_timer(agent)
    heartbeat = next(
        timer
        for timer in agent_timers.list_wakes(agent.agent_id)
        if timer.timer_id != scheduled.timer_id
    )
    assert agent_timers.cancel_wake(agent.agent_id, scheduled.timer_id)

    agent.spin([InputItem("user", "work")])

    assert agent_timers.list_wakes(agent.agent_id) == [heartbeat]


def test_ttl_saves_alone_then_restores_before_the_waiting_input():
    agent = as_fake(orchestrator())
    old_session = agent.session_id

    expired = agent.spin(
        [
            InputItem(
                f"{session_ttl.EXPIRY_SOURCE}:ttl", session_ttl.EXPIRY_MESSAGE
            ),
            InputItem("user", "new work"),
        ]
    )

    turns = fake.turns(agent.agent_id)
    assert turns[0] == [
        [f"{session_ttl.EXPIRY_SOURCE}:ttl", session_ttl.EXPIRY_MESSAGE]
    ]
    assert turns[1][0][0] == session_ttl.RESTORE_SOURCE
    assert session_ttl.EXPIRY_MESSAGE in turns[1][0][1]
    assert turns[1][1] == ["user", "new work"]
    assert expired.text.endswith("new work")
    assert agent.session_id != old_session
    assert agent.status == "active"
    assert agent.record["session_turns"] == 1
    assert not session_ttl.handoff_path(agent.agent_id).exists()


def test_ttl_without_waiting_input_leaves_the_agent_dormant_until_woken():
    agent = as_fake(orchestrator())
    agent_id = agent.agent_id

    expired = agent.spin(
        [
            InputItem(
                f"{session_ttl.EXPIRY_SOURCE}:ttl", session_ttl.EXPIRY_MESSAGE
            )
        ]
    )

    record = records.read(agent_id)
    assert expired.text == "session TTL expired; handoff saved"
    assert record["status"] == records.DORMANT
    assert record["session_id"] is None
    assert record["session_since"] is None
    assert session_ttl.handoff_path(agent_id).read_text("utf-8")

    restored = ag.restore(agent_id)
    restored.spin([InputItem("user", "continue")])

    turns = fake.turns(agent_id)
    assert turns[-1][0][0] == session_ttl.RESTORE_SOURCE
    assert turns[-1][1] == ["user", "continue"]
    assert not session_ttl.handoff_path(agent_id).exists()


def test_the_existing_timer_adapter_drives_session_expiry():
    agent = as_fake(orchestrator())
    scheduled = ttl_timer(agent)
    heartbeat = next(
        timer
        for timer in agent_timers.list_wakes(agent.agent_id)
        if timer.timer_id != scheduled.timer_id
    )
    envelope = agent_timers.poll(timers.parse_timestamp(scheduled.due_at))

    assert envelope is not None
    assert envelope.source == f"{session_ttl.EXPIRY_SOURCE}:{scheduled.timer_id}"
    dispatch(envelope)

    assert records.read(agent.agent_id)["status"] == records.DORMANT
    assert agent_timers.list_wakes(agent.agent_id) == [heartbeat]


def test_an_armed_ttl_keeps_its_identity_after_a_role_prompt_edit(beebot_root):
    agent = as_fake(orchestrator())
    scheduled = ttl_timer(agent)
    role_config = beebot_root / "configs" / "roles" / "orchestrator" / "role.toml"
    role_config.write_text(
        "[prompts]\nsession_expire = \"new expiry words\"\n",
        encoding="utf-8",
    )

    envelope = agent_timers.poll(timers.parse_timestamp(scheduled.due_at))
    assert envelope is not None
    dispatch(envelope)

    assert records.read(agent.agent_id)["status"] == records.DORMANT


def test_failed_ttl_save_still_detaches_with_a_fallback_handoff(monkeypatch):
    agent = as_fake(orchestrator())
    attempts = 0
    deliver = agent.backend.deliver

    def counted_deliver(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        return deliver(*args, **kwargs)

    monkeypatch.setattr(agent.backend, "deliver", counted_deliver)
    fake.fail_session(agent.session_id, "connection reset")

    expired = agent.spin(
        [
            InputItem(
                f"{session_ttl.EXPIRY_SOURCE}:ttl", session_ttl.EXPIRY_MESSAGE
            )
        ]
    )

    assert attempts == 1
    assert expired.text == "session TTL expired; handoff failed"
    assert agent.status == records.DORMANT
    assert "expired before it could save" in session_ttl.handoff_path(
        agent.agent_id
    ).read_text("utf-8")
