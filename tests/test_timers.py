"""Generic scheduling and agent-owned wake timer integration."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict

import pytest

from beebot import timers
from beebot.agents import records
from beebot.agents import timers as agent_timers
from tests.conftest import as_fake, orchestrator, worker


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
        timers.create("id", {}, current=at(12))
    with pytest.raises(timers.TimerError, match="exactly one"):
        timers.create("id", {}, after="1h", every="1h", current=at(12))
    with pytest.raises(timers.TimerError, match="only meaningful"):
        timers.create("id", {}, after="1h", duration="2h", current=at(12))
    with pytest.raises(timers.TimerError, match="first occurrence"):
        timers.create("id", {}, every="2h", duration="1h", current=at(12))


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


def test_legacy_record_has_no_timer_field_until_its_first_timer(beebot_root):
    agent = worker()
    assert "timers" not in records.read(agent.agent_id)
    assert agent_timers.list_wakes(agent.agent_id) == []

    timer = agent_timers.create_wake(
        agent.agent_id, message="check", after="1h", current=at(12)
    )

    assert records.read(agent.agent_id)["timers"] == [asdict(timer)]
    adapter = beebot_root / "inputs.d" / agent_timers.ADAPTER_NAME
    assert adapter.stat().st_mode & 0o111
    assert "beebot.agents.timers" in adapter.read_text("utf-8")


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


def test_shared_adapter_installation_is_idempotent(beebot_root):
    first = worker(cwd="workspaces/first")
    second = worker(cwd="workspaces/second")
    agent_timers.create_wake(
        first.agent_id, message="first", after="1h", current=at(12)
    )
    path = beebot_root / "inputs.d" / agent_timers.ADAPTER_NAME
    inode = path.stat().st_ino

    agent_timers.create_wake(
        second.agent_id, message="second", after="1h", current=at(12)
    )

    assert path.stat().st_ino == inode


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


def test_idle_malformed_and_closed_records_do_not_block_other_agents(beebot_root):
    idle = worker(cwd="workspaces/idle")
    closed = as_fake(worker(cwd="workspaces/closed"))
    closed_timer = agent_timers.create_wake(
        closed.agent_id, message="closed", after="1h", current=at(10)
    )
    closed.close()
    malformed = beebot_root / "runtime" / "agents" / "malformed"
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


def test_malformed_valid_json_record_is_isolated(beebot_root):
    malformed = beebot_root / "runtime" / "agents" / "malformed-json"
    malformed.mkdir(parents=True)
    (malformed / "record.json").write_text(
        json.dumps({"status": "active", "timers": [{}]}), encoding="utf-8"
    )
    active = worker()
    agent_timers.create_wake(
        active.agent_id, message="still fires", after="1h", current=at(10)
    )

    assert agent_timers.poll(at(12)).agent_id == active.agent_id
