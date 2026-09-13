"""Generic one-shot and recurring timer schedules."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

UTC = dt.timezone.utc
STAMP = "%Y-%m-%dT%H:%M:%SZ"
_DURATION = re.compile(r"^([1-9][0-9]*)([smhd])$")
_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class TimerError(RuntimeError):
    pass


@dataclass(frozen=True)
class Timer:
    timer_id: str
    due_at: str
    payload: Mapping[str, Any]
    every_seconds: int | None = None
    until: str | None = None


def parse_duration(value: str) -> int:
    """Parse a positive duration in compact `10s`, `5m`, `2h`, or `1d` form."""
    if not isinstance(value, str) or not (match := _DURATION.fullmatch(value)):
        raise TimerError(
            "duration must be a positive integer followed by s, m, h, or d"
        )
    amount, unit = match.groups()
    return int(amount) * _SECONDS[unit]


def timestamp(value: dt.datetime) -> str:
    """Return a canonical second-resolution UTC timestamp."""
    if value.tzinfo is None:
        raise TimerError("timer datetimes must include a timezone")
    return value.astimezone(UTC).strftime(STAMP)


def parse_timestamp(value: str) -> dt.datetime:
    try:
        return dt.datetime.strptime(value, STAMP).replace(tzinfo=UTC)
    except (TypeError, ValueError):
        raise TimerError(f"invalid timer timestamp: {value!r}") from None


def create(
    timer_id: str,
    payload: Mapping[str, Any],
    *,
    after: str | None = None,
    every: str | None = None,
    duration: str | None = None,
    current: dt.datetime | None = None,
) -> Timer:
    """Create a one-shot or recurring timer from compact durations."""
    if (after is None) == (every is None):
        raise TimerError("pass exactly one of after or every")
    if duration is not None and every is None:
        raise TimerError("duration is only meaningful with every")

    current = current or dt.datetime.now(UTC)
    interval = parse_duration(every) if every is not None else None
    delay = interval if interval is not None else parse_duration(after or "")
    duration_seconds = parse_duration(duration) if duration is not None else None
    if interval is not None and duration_seconds is not None:
        if duration_seconds < interval:
            raise TimerError("duration must include at least the first occurrence")

    due = current + dt.timedelta(seconds=delay)
    return Timer(
        timer_id=timer_id,
        due_at=timestamp(due),
        payload=payload,
        every_seconds=interval,
        until=(
            timestamp(current + dt.timedelta(seconds=duration_seconds))
            if duration_seconds is not None
            else None
        ),
    )


def ordered(timers: Sequence[Timer]) -> list[Timer]:
    """Return timers in deterministic due-time and ID order."""
    return sorted(
        timers,
        key=lambda timer: (parse_timestamp(timer.due_at), timer.timer_id),
    )


def remove(timers: Sequence[Timer], timer_id: str) -> tuple[bool, list[Timer]]:
    kept = [timer for timer in timers if timer.timer_id != timer_id]
    return len(kept) != len(timers), ordered(kept)


def take_due(
    timers: Sequence[Timer], current: dt.datetime | None = None
) -> tuple[Timer | None, list[Timer]]:
    """Take the earliest due timer and return its atomically updated schedule."""
    current = current or dt.datetime.now(UTC)
    current_stamp = timestamp(current)
    scheduled = ordered(timers)
    due = next((timer for timer in scheduled if timer.due_at <= current_stamp), None)
    if due is None:
        return None, scheduled

    scheduled.remove(due)
    if due.every_seconds is not None:
        previous = parse_timestamp(due.due_at)
        elapsed = max(
            0,
            int((parse_timestamp(current_stamp) - previous).total_seconds()),
        )
        steps = elapsed // due.every_seconds + 1
        following = previous + dt.timedelta(seconds=steps * due.every_seconds)
        if due.until is None or following <= parse_timestamp(due.until):
            scheduled.append(replace(due, due_at=timestamp(following)))
    return due, ordered(scheduled)
