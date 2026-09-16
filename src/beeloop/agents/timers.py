"""Agent-record storage and delivery integration for wake timers."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from beeloop import timers
from beeloop.dispatch.envelope import Envelope, serialize

from . import records
from .agent import agent_class

def create_wake(
    agent_id: str,
    *,
    message: str,
    after: str | None = None,
    every: str | None = None,
    duration: str | None = None,
    current: dt.datetime | None = None,
) -> timers.Timer:
    if not isinstance(message, str) or not message.strip():
        raise timers.TimerError("message must not be empty")

    with records.files_lock(agent_id):
        record = records.read(agent_id)
        if record.get("status") == records.CLOSED:
            raise timers.TimerError("closed agents cannot create timers")
        timer = timers.create(
            records.new_agent_id(),
            {"message": message},
            after=after,
            every=every,
            duration=duration,
            current=current,
        )
        stored = _load_timers(record)
        record["timers"] = [asdict(item) for item in timers.ordered([*stored, timer])]
        records.write(record, _schema(record))
    return timer


def arm_recurring(record: dict[str, Any], *, message: str, every: str) -> None:
    timer = timers.create(
        records.new_agent_id(),
        {"message": message},
        every=every,
    )
    stored = _load_timers(record)
    record["timers"] = [asdict(item) for item in timers.ordered([*stored, timer])]


def list_wakes(agent_id: str) -> list[timers.Timer]:
    return timers.ordered(_load_timers(records.read(agent_id)))


def cancel_wake(agent_id: str, timer_id: str) -> bool:
    with records.files_lock(agent_id):
        record = records.read(agent_id)
        removed, remaining = timers.remove(_load_timers(record), timer_id)
        if removed:
            record["timers"] = [asdict(item) for item in remaining]
            records.write(record, _schema(record))
        return removed


def poll(current: dt.datetime | None = None) -> Envelope | None:
    """Remove or advance and return the globally earliest due wake."""
    current = current or dt.datetime.now(timers.UTC)
    for _, timer_id, agent_id in _due_candidates(current):
        if timer := _take_due(agent_id, timer_id, current):
            return Envelope(
                role=None,
                agent_id=agent_id,
                source=f"{timer.payload.get('_source', 'timer')}:{timer.timer_id}",
                msg=str(timer.payload["message"]),
            )
    return None


def _take_due(
    agent_id: str, timer_id: str, current: dt.datetime
) -> timers.Timer | None:
    try:
        with records.files_lock(agent_id):
            record = records.read(agent_id)
            if record.get("status") == records.CLOSED:
                return None
            due, remaining = timers.take_due(_load_timers(record), current)
            if due is None or due.timer_id != timer_id:
                return None
            record["timers"] = [asdict(item) for item in remaining]
            records.write(record, _schema(record))
            return due
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        records.AgentError,
        timers.TimerError,
    ):
        return None


def _due_candidates(current: dt.datetime) -> list[tuple[dt.datetime, str, str]]:
    candidates: list[tuple[dt.datetime, str, str]] = []
    directory = records.root() / "runtime" / "agents"
    for path in directory.glob("*/record.json"):
        try:
            record = json.loads(path.read_text("utf-8"))
            if record.get("status") == records.CLOSED:
                continue
            records.validate(record, _schema(record))
            for timer in _load_timers(record):
                due = timers.parse_timestamp(timer.due_at)
                if due <= current.astimezone(timers.UTC):
                    candidates.append((due, timer.timer_id, path.parent.name))
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            records.AgentError,
            timers.TimerError,
        ):
            continue
    return sorted(candidates)


def _load_timers(record: Mapping[str, Any]) -> list[timers.Timer]:
    stored = record.get("timers", [])
    if not isinstance(stored, list):
        raise timers.TimerError("agent timers must be a list")
    return [timers.Timer(**item) for item in stored]


def _schema(record: Mapping[str, Any]) -> str:
    return agent_class(record["type"]).SCHEMA


def main() -> int:
    if envelope := poll():
        print(serialize(envelope), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
