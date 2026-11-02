"""Generic scheduling and agent-owned wake timer integration."""

from __future__ import annotations

import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import pytest

import beeloop.agents.agent as agent_module
from beeloop import agents as ag
from beeloop import timers
from beeloop.agents import records, session_ttl
from beeloop.agents import timers as agent_timers
from beeloop.agents.backends import InputItem, fake
from beeloop.dispatch.dispatch import dispatch
from tests.conftest import as_fake, make_role, orchestrator, worker


def at(hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(2026, 9, 4, hour, minute, tzinfo=timers.UTC)


@pytest.mark.parametrize(
    "value, seconds",
    [("30s", 30), ("5m", 300), ("2h", 7200), ("1d", 86400)],
)
def test_duration_units(value, seconds):
    assert timers.parse_duration(value) == seconds


@pytest.mark.parametrize(
    "value", ["", "0s", "-1s", "1.5h", "1w", " 1h", "1h ", 1]
)
def test_duration_rejects_everything_but_a_positive_integer_and_known_unit(value):
    with pytest.raises(timers.TimerError, match="positive integer"):
        timers.parse_duration(value)


def test_timer_arguments_are_validated():
    with pytest.raises(timers.TimerError, match="exactly one"):
        timers.create({}, current=at(12))
    with pytest.raises(timers.TimerError, match="exactly one"):
        timers.create({}, after="1h", every="1h", current=at(12))
    with pytest.raises(timers.TimerError, match="only meaningful"):
        timers.create({}, after="1h", duration="2h", current=at(12))
    with pytest.raises(timers.TimerError, match="first occurrence"):
        timers.create({}, every="2h", duration="1h", current=at(12))


def test_schedule_keys_encode_the_first_due_cadence_lifetime_and_source():
    first = timers.create({"message": "first"}, every="1h", current=at(10))
    same = timers.create({"message": "second"}, every="60m", current=at(10))
    cadence = timers.create({"message": "other"}, every="30m", current=at(10, 30))
    bounded = timers.create({}, every="1h", duration="2h", current=at(10))
    expiry = timers.create({"_source": "session-ttl"}, every="1h", current=at(10))

    assert first.timer_id == same.timer_id
    assert len({first.timer_id, cadence.timer_id, bounded.timer_id, expiry.timer_id}) == 4
    _, advanced = timers.take_due([first], at(12))
    assert advanced[0].timer_id == first.timer_id
    assert advanced[0].due_at != first.due_at


def test_matching_wake_schedules_append_prompts_and_cancel_as_one():
    agent = worker()
    first = agent_timers.create_wake(
        agent.agent_id, message="first task", after="1h", current=at(10)
    )
    merged = agent_timers.create_wake(
        agent.agent_id, message="second task", after="60m", current=at(10)
    )

    assert merged.timer_id == first.timer_id
    assert merged.payload["message"] == "first task\n\nsecond task"
    assert agent_timers.list_wakes(agent.agent_id) == [merged]
    envelope = agent_timers.poll(at(11))
    assert envelope.msg == "first task\n\nsecond task"
    assert agent_timers.list_wakes(agent.agent_id) == []

    repeated = agent_timers.create_wake(
        agent.agent_id, message="repeat", every="1h", current=at(10)
    )
    assert agent_timers.cancel_wake(agent.agent_id, repeated.timer_id)
    assert agent_timers.list_wakes(agent.agent_id) == []


def test_concurrent_matching_schedules_keep_every_prompt():
    agent = worker()

    def schedule(index):
        return agent_timers.create_wake(
            agent.agent_id, message=f"task {index}", after="1h", current=at(10)
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        scheduled = list(executor.map(schedule, range(8)))

    [combined] = agent_timers.list_wakes(agent.agent_id)
    assert {timer.timer_id for timer in scheduled} == {combined.timer_id}
    assert set(combined.payload["message"].split("\n\n")) == {f"task {index}" for index in range(8)}


def test_matching_due_times_keep_distinct_cadence_and_lifetimes():
    agent = worker()
    recurring = agent_timers.create_wake(
        agent.agent_id, message="hourly", every="1h", current=at(10)
    )
    one_shot = agent_timers.create_wake(
        agent.agent_id, message="once", after="1h", current=at(10)
    )
    bounded = agent_timers.create_wake(
        agent.agent_id, message="bounded", every="1h", duration="2h", current=at(10)
    )

    assert len(agent_timers.list_wakes(agent.agent_id)) == 3
    assert len({recurring.timer_id, one_shot.timer_id, bounded.timer_id}) == 3


def test_matching_ordinary_wake_does_not_merge_with_session_expiry():
    agent = orchestrator(backend="fake")
    expiry = next(t for t in agent_timers.list_wakes(agent.agent_id) if session_ttl.is_expiry_timer(t))
    due = timers.parse_timestamp(expiry.due_at)
    ordinary = agent_timers.create_wake(
        agent.agent_id, message="ordinary", after="1h", current=due - dt.timedelta(hours=1)
    )

    assert ordinary.due_at == expiry.due_at
    assert ordinary.timer_id != expiry.timer_id
    assert session_ttl.is_expiry_timer(expiry)
    assert ordinary.payload == {"message": "ordinary"}
    assert agent_timers.cancel_wake(agent.agent_id, ordinary.timer_id)
    assert any(t.timer_id == expiry.timer_id for t in agent_timers.list_wakes(agent.agent_id))


def test_take_due_is_deterministic_and_removes_a_one_shot():
    later = timers.Timer("later", timers.timestamp(at(13)), {})
    second = timers.Timer("b", timers.timestamp(at(12)), {})
    first = timers.Timer("a", timers.timestamp(at(12)), {})

    due, remaining = timers.take_due([later, second, first], at(12))

    assert due == first
    assert remaining == [second, later]


def test_recurrence_keeps_cadence_and_skips_missed_occurrences():
    recurring = timers.Timer(
        "hourly", timers.timestamp(at(12)), {}, every_seconds=3600
    )

    due, remaining = timers.take_due([recurring], at(14, 5))

    assert due == recurring
    assert remaining[0].due_at == "2026-09-04T15:00:00Z"


def test_recurrence_end_is_inclusive():
    recurring = timers.Timer(
        "bounded",
        timers.timestamp(at(12)),
        {},
        every_seconds=3600,
        until=timers.timestamp(at(13)),
    )

    _, at_boundary = timers.take_due([recurring], at(12))
    assert at_boundary[0].due_at == "2026-09-04T13:00:00Z"

    due, finished = timers.take_due(at_boundary, at(13))
    assert due == at_boundary[0]
    assert finished == []


def test_legacy_record_has_no_timer_field_until_its_first_timer():
    agent = worker()
    assert "timers" not in records.read(agent.agent_id)
    assert agent_timers.list_wakes(agent.agent_id) == []

    timer = agent_timers.create_wake(
        agent.agent_id, message="check", after="1h", current=at(12)
    )

    assert records.read(agent.agent_id)["timers"] == [asdict(timer)]


def test_role_heartbeat_arms_an_indefinite_recurring_timer(beeloop_root):
    make_role(
        beeloop_root,
        "heartbeat",
        'backend = "fake"\ncwd = "workspaces/heartbeat"\n'
        "[heartbeat]\n"
        'every = "2h"\n'
        "[prompts]\n"
        'heartbeat = "check for work"\n',
    )

    agent = ag.create("heartbeat")

    [scheduled] = agent_timers.list_wakes(agent.agent_id)
    assert scheduled.payload == {"message": "check for work"}
    assert scheduled.every_seconds == 7200
    assert scheduled.until is None

    assert agent_timers.cancel_wake(agent.agent_id, scheduled.timer_id)
    agent.spin([InputItem("user", "ordinary work")])
    assert agent_timers.list_wakes(agent.agent_id) == []


@pytest.mark.parametrize(
    "config",
    [
        "[heartbeat]\nevery = \"1h\"\n",
        "[prompts]\nheartbeat = \"check\"\n",
        "[heartbeat]\nevery = \"1h\"\n[prompts]\nheartbeat = \"  \"\n",
    ],
)
def test_heartbeat_needs_both_schedule_and_prompt(beeloop_root, config):
    make_role(
        beeloop_root,
        "inactive-heartbeat",
        'backend = "fake"\ncwd = "workspaces/inactive"\n' + config,
    )

    agent = ag.create("inactive-heartbeat")

    assert agent_timers.list_wakes(agent.agent_id) == []


@pytest.mark.parametrize(
    "heartbeat",
    [
        "[heartbeat]\n",
        "[heartbeat]\nevery = \"0h\"\n",
        "[heartbeat]\nevery = \"1h\"\nextra = \"no\"\n",
    ],
)
def test_heartbeat_requires_exactly_one_positive_every(beeloop_root, heartbeat):
    make_role(beeloop_root, "broken-heartbeat", heartbeat)

    with pytest.raises(ag.UnknownRole, match="heartbeat|positive integer"):
        ag.load_role("broken-heartbeat")


def test_identical_heartbeat_and_expiry_prompts_keep_their_distinct_behaviors(
    beeloop_root,
    monkeypatch,
):
    make_role(
        beeloop_root,
        "heartbeat-ttl",
        'backend = "fake"\ncwd = "workspaces/heartbeat-ttl"\n'
        "[session_ttl]\n"
        'idle = "4h"\n'
        "[heartbeat]\n"
        'every = "1h"\n'
        "[prompts]\n"
        'heartbeat = "check for work"\n'
        'session_expire = "check for work"\n',
    )
    agent = ag.create("heartbeat-ttl")
    scheduled = agent_timers.list_wakes(agent.agent_id)
    heartbeat = next(timer for timer in scheduled if timer.every_seconds is not None)
    expiry = next(timer for timer in scheduled if timer.every_seconds is None)
    monkeypatch.setattr(agent_module, "now", lambda: expiry.due_at)

    agent.spin(
        [
            InputItem(
                f"{session_ttl.EXPIRY_SOURCE}:ttl", "check for work"
            )
        ]
    )
    assert agent.status == records.DORMANT
    assert [timer.timer_id for timer in agent_timers.list_wakes(agent.agent_id)] == [
        heartbeat.timer_id
    ]

    envelope = agent_timers.poll(timers.parse_timestamp(heartbeat.due_at))
    assert envelope is not None
    dispatch(envelope)

    turns = fake.turns(agent.agent_id)
    assert turns[-1][0][0] == session_ttl.RESTORE_SOURCE
    assert turns[-1][1] == [f"timer:{heartbeat.timer_id}", "check for work"]
    assert records.read(agent.agent_id)["status"] == "active"
    assert any(
        timer.timer_id == heartbeat.timer_id
        for timer in agent_timers.list_wakes(agent.agent_id)
    )


def test_agent_timer_crud_is_bound_to_its_owner():
    first = worker(cwd="workspaces/first")
    second = worker(cwd="workspaces/second")
    timer = agent_timers.create_wake(
        first.agent_id, message="owned", after="1h", current=at(12)
    )

    assert agent_timers.list_wakes(first.agent_id) == [timer]
    assert agent_timers.list_wakes(second.agent_id) == []
    assert agent_timers.cancel_wake(second.agent_id, timer.timer_id) is False
    assert agent_timers.list_wakes(first.agent_id) == [timer]
    assert agent_timers.cancel_wake(first.agent_id, timer.timer_id) is True


def test_poll_emits_only_the_globally_earliest_direct_envelope():
    later = worker(cwd="workspaces/later")
    earlier = worker(cwd="workspaces/earlier")
    agent_timers.create_wake(
        later.agent_id, message="later", after="2h", current=at(10)
    )
    timer = agent_timers.create_wake(
        earlier.agent_id, message="earlier", after="1h", current=at(10)
    )

    envelope = agent_timers.poll(at(13))

    assert envelope is not None
    assert envelope.agent_id == earlier.agent_id
    assert envelope.role is None
    assert envelope.source == f"timer:{timer.timer_id}"
    assert envelope.msg == "earlier"
    assert agent_timers.list_wakes(earlier.agent_id) == []
    assert len(agent_timers.list_wakes(later.agent_id)) == 1


def test_stale_candidate_does_not_consume_owners_later_timer(monkeypatch):
    first = worker(cwd="workspaces/first")
    second = worker(cwd="workspaces/second")
    stale = agent_timers.create_wake(
        first.agent_id, message="already taken", after="1h", current=at(9)
    )
    later = agent_timers.create_wake(
        first.agent_id, message="later", after="3h", current=at(9)
    )
    next_global = agent_timers.create_wake(
        second.agent_id, message="next", after="2h", current=at(9)
    )
    scan = agent_timers._due_candidates

    def consume_first_candidate(current):
        candidates = scan(current)
        assert agent_timers._take_due(first.agent_id, stale.timer_id, current) == stale
        return candidates

    monkeypatch.setattr(agent_timers, "_due_candidates", consume_first_candidate)

    envelope = agent_timers.poll(at(13))

    assert envelope is not None
    assert envelope.agent_id == second.agent_id
    assert envelope.source == f"timer:{next_global.timer_id}"
    assert agent_timers.list_wakes(first.agent_id) == [later]


def test_idle_malformed_and_closed_records_do_not_block_other_agents(beeloop_root):
    idle = worker(cwd="workspaces/idle")
    closed = as_fake(worker(cwd="workspaces/closed"))
    closed_timer = agent_timers.create_wake(
        closed.agent_id, message="closed", after="1h", current=at(10)
    )
    closed.close()
    malformed = beeloop_root / "runtime" / "agents" / "malformed"
    malformed.mkdir(parents=True)
    (malformed / "record.json").write_text("{broken", encoding="utf-8")
    active = worker(cwd="workspaces/active")
    active_timer = agent_timers.create_wake(
        active.agent_id, message="active", after="1h", current=at(10)
    )

    envelope = agent_timers.poll(at(12))

    assert envelope is not None
    assert envelope.agent_id == active.agent_id
    assert envelope.source == f"timer:{active_timer.timer_id}"
    assert agent_timers.list_wakes(closed.agent_id) == [closed_timer]
    assert agent_timers.list_wakes(idle.agent_id) == []
    assert agent_timers.poll(at(12)) is None


def test_poll_advances_before_dispatch_and_does_not_retry_an_occurrence():
    agent = worker()
    fired = agent_timers.create_wake(
        agent.agent_id,
        message="hourly",
        every="1h",
        current=at(10),
    )

    envelope = agent_timers.poll(at(12, 5))

    assert envelope is not None
    assert envelope.source == f"timer:{fired.timer_id}"
    assert agent_timers.list_wakes(agent.agent_id)[0].due_at == "2026-09-04T13:00:00Z"
    assert agent_timers.poll(at(12, 5)) is None


def test_closed_agent_cannot_create_a_timer():
    agent = as_fake(orchestrator())
    agent.close()

    with pytest.raises(timers.TimerError, match="closed"):
        agent_timers.create_wake(agent.agent_id, message="late", after="1h")


def test_malformed_valid_json_record_is_isolated(beeloop_root):
    malformed = beeloop_root / "runtime" / "agents" / "malformed-json"
    malformed.mkdir(parents=True)
    (malformed / "record.json").write_text(
        json.dumps({"status": "active", "timers": [{}]}), encoding="utf-8"
    )
    active = worker()
    agent_timers.create_wake(
        active.agent_id, message="still fires", after="1h", current=at(10)
    )

    assert agent_timers.poll(at(12)).agent_id == active.agent_id
