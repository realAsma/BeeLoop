"""Session TTL policy, timer scheduling, and continuation handoffs."""

from __future__ import annotations

import datetime as dt
import tomllib
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from beeloop import timers

from . import records
from .backends import InputItem

EXPIRY_MESSAGE = (
    "The TTL for this session has expired. Save the current work to BeeLoop "
    "State, then return the saved work name and a concise continuation handoff."
)
RESUME_MESSAGE = (
    "Restore the referenced BeeLoop State first, then process the remaining "
    "inputs in order."
)
INIT_SOURCE = "session-init"
RESTORE_SOURCE = "session-restore"
EXPIRY_SOURCE = "session-ttl"
_TIMER_SOURCE_FIELD = "_source"


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class Policy:
    idle_seconds: int | None = None
    max_age_seconds: int | None = None


def parse_policy(value: Any) -> Policy | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise PolicyError("session_ttl must be a table")
    if unknown := value.keys() - {"idle", "max_age"}:
        raise PolicyError(f"unknown session_ttl field(s): {', '.join(sorted(unknown))}")
    if not value:
        raise PolicyError("session_ttl must define idle or max_age")
    try:
        return Policy(
            idle_seconds=(
                timers.parse_duration(value["idle"]) if "idle" in value else None
            ),
            max_age_seconds=(
                timers.parse_duration(value["max_age"])
                if "max_age" in value
                else None
            ),
        )
    except timers.TimerError as exc:
        raise PolicyError(str(exc)) from exc


def load_policy(agent_id: str) -> Policy | None:
    path = records.agent_path(agent_id) / "config.toml"
    try:
        config = tomllib.loads(path.read_text("utf-8"))
        return parse_policy(config.get("session_ttl"))
    except (OSError, tomllib.TOMLDecodeError, PolicyError) as exc:
        raise records.AgentError(f"the agent config at {path} is broken: {exc}") from exc


def arm(record: dict[str, Any], policy: Policy | None, prompt: str | None) -> None:
    if policy is None or prompt is None:
        return
    scheduled = _stored(record)
    scheduled.append(
        timers.Timer(
            timer_id=records.new_agent_id(),
            due_at=deadline(record, policy),
            payload={"message": prompt, _TIMER_SOURCE_FIELD: EXPIRY_SOURCE},
        )
    )
    record["timers"] = [asdict(item) for item in timers.ordered(scheduled)]


def move(record: dict[str, Any], policy: Policy | None) -> None:
    if policy is None:
        return
    scheduled = _stored(record)
    found = next((item for item in scheduled if is_expiry_timer(item)), None)
    if found is None:
        return
    scheduled[scheduled.index(found)] = replace(found, due_at=deadline(record, policy))
    record["timers"] = [asdict(item) for item in timers.ordered(scheduled)]


def remove(record: dict[str, Any]) -> None:
    if "timers" not in record:
        return
    kept = [item for item in _stored(record) if not is_expiry_timer(item)]
    record["timers"] = [asdict(item) for item in timers.ordered(kept)]


def deadline(record: Mapping[str, Any], policy: Policy) -> str:
    since = timers.parse_timestamp(record["session_since"])
    last_turn = record.get("session_last_turn")
    idle_base = timers.parse_timestamp(last_turn) if last_turn else since
    choices: list[dt.datetime] = []
    if policy.idle_seconds is not None:
        choices.append(idle_base + dt.timedelta(seconds=policy.idle_seconds))
    if policy.max_age_seconds is not None:
        choices.append(since + dt.timedelta(seconds=policy.max_age_seconds))
    return timers.timestamp(min(choices))


def is_due(record: Mapping[str, Any], policy: Policy, current: str) -> bool:
    return deadline(record, policy) <= current


def rearm(record: dict[str, Any], policy: Policy, prompt: str) -> None:
    if not any(is_expiry_timer(item) for item in _stored(record)):
        arm(record, policy, prompt)


def is_expiry_input(item: InputItem) -> bool:
    return item.source.startswith(f"{EXPIRY_SOURCE}:")


def is_expiry_timer(timer: timers.Timer) -> bool:
    return timer.payload.get(_TIMER_SOURCE_FIELD) == EXPIRY_SOURCE


def write_handoff(agent_id: str, text: str) -> None:
    records.write_file(handoff_path(agent_id), text)


def read_handoff(agent_id: str) -> str | None:
    path = handoff_path(agent_id)
    if not path.exists():
        return None
    text = path.read_text("utf-8").strip()
    return text or None


def clear_handoff(agent_id: str) -> None:
    path = handoff_path(agent_id)
    if path.exists():
        path.unlink()


def init_input(prompt: str) -> InputItem:
    return InputItem(INIT_SOURCE, prompt)


def restore_input(handoff: str, prompt: str | None) -> InputItem:
    handoff_text = f"Previous session handoff:\n{handoff}"
    return InputItem(
        RESTORE_SOURCE,
        f"{prompt}\n\n{handoff_text}" if prompt else handoff_text,
    )


def handoff_path(agent_id: str) -> Path:
    return records.agent_path(agent_id) / "handoff.md"


def _stored(record: Mapping[str, Any]) -> list[timers.Timer]:
    return [timers.Timer(**item) for item in record.get("timers", [])]
