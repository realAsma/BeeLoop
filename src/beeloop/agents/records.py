"""Durable agent records, queues, and their locks."""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import secrets
import tempfile
import uuid
from contextlib import contextmanager
from functools import cache
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

import jsonschema

from beeloop.config import ConfigError, root as configured_root

from .backends import InputItem

UTC = dt.timezone.utc
STAMP = "%Y-%m-%dT%H:%M:%SZ"
PREPARED, DORMANT, CLOSED = "prepared", "dormant", "closed"


class AgentError(RuntimeError):
    pass


class UnknownAgent(AgentError):
    pass


def root() -> Path:
    """Return the configured deployment root."""
    try:
        return configured_root()
    except ConfigError as exc:
        raise AgentError(str(exc)) from exc


def runtime(*parts: str) -> Path:
    directory = root().joinpath("runtime", *parts)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def agent_path(agent_id: str) -> Path:
    return root().joinpath("runtime", "agents", agent_id)


def record_path(agent_id: str) -> Path:
    return agent_path(agent_id) / "record.json"


def queue_path(agent_id: str) -> Path:
    return agent_path(agent_id) / "queue.jsonl"


def now() -> str:
    return dt.datetime.now(UTC).strftime(STAMP)


def new_agent_id() -> str:
    """Return a time-ordered, unguessable UUIDv7 agent ID."""
    stamp = int(dt.datetime.now(UTC).timestamp() * 1000)
    raw = bytearray(stamp.to_bytes(6, "big") + secrets.token_bytes(10))
    raw[6] = (raw[6] & 0x0F) | 0x70
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(raw)))


@contextmanager
def files_lock(agent_id: str) -> Iterator[None]:
    """Lock record and queue updates independently of a model turn."""
    with open(agent_path(agent_id) / "files.lock", "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class Turn:
    """A model lock releasable under files_lock so arrivals cannot be stranded."""

    def __init__(self, handle: Any) -> None:
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        fcntl.flock(self._handle, fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None

    def __enter__(self) -> "Turn":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()


def _take_turn(agent_id: str) -> Turn | None:
    handle = open(agent_path(agent_id) / "model.lock", "a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return Turn(handle)


def read(agent_id: str) -> dict[str, Any]:
    path = record_path(agent_id)
    if not path.exists():
        raise UnknownAgent(f"no agent {agent_id!r} at {path}")
    return json.loads(path.read_text("utf-8"))


@cache
def _schema(name: str) -> dict:
    path = Path(__file__).resolve().parent / "assets" / f"{name}.json"
    try:
        return json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        available = sorted(item.stem for item in path.parent.glob("*.json"))
        raise AgentError(
            f"no record schema named {name!r}; assets/ has "
            f"{', '.join(available) or 'nothing'}"
        ) from None
    except (OSError, ValueError) as exc:
        raise AgentError(f"the record schema at {path} is broken: {exc}") from exc


def validate(record: Mapping[str, Any], schema: str) -> None:
    try:
        jsonschema.validate(record, _schema(schema))
    except jsonschema.ValidationError as exc:
        where = ".".join(str(part) for part in exc.absolute_path)
        raise AgentError(
            f"this is not a valid {schema} record"
            f"{f' at {where}' if where else ''}: {exc.message}"
        ) from None


def write(record: Mapping[str, Any], schema: str) -> None:
    validate(record, schema)
    write_file(
        record_path(record["agent_id"]), json.dumps(record, indent=2, sort_keys=True)
    )


def update(
    agent_id: str,
    schema: str,
    fields: Mapping[str, Any] | None = None,
    bump: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Update the latest record under its files lock."""
    def apply(record: dict[str, Any]) -> None:
        record.update(fields or {})
        for key, amount in (bump or {}).items():
            record[key] = (record.get(key) or 0) + amount

    return modify(agent_id, schema, apply)


def modify(
    agent_id: str,
    schema: str,
    change: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """Mutate the latest record atomically under its files lock."""
    with files_lock(agent_id):
        record = read(agent_id)
        change(record)
        write(record, schema)
        return record


def write_file(path: Path, body: str) -> None:
    """Atomically replace a file through a same-directory fsynced temporary."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    )
    try:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(handle.name, path)
    except BaseException:
        handle.close()
        os.unlink(handle.name)
        raise


def _park(agent_id: str, inputs: Sequence[InputItem]) -> None:
    with open(queue_path(agent_id), "a", encoding="utf-8") as handle:
        for item in inputs:
            handle.write(json.dumps({"source": item.source, "content": item.content}) + "\n")


def _take_queue(agent_id: str) -> list[InputItem]:
    path = queue_path(agent_id)
    if not path.exists():
        return []
    lines = [line for line in path.read_text("utf-8").splitlines() if line.strip()]
    if lines:
        path.write_text("", encoding="utf-8")
    return [InputItem(**json.loads(line)) for line in lines]


def claim(agent_id: str, inputs: Sequence[InputItem]) -> Turn | None:
    """Acquire the model turn or atomically park the inputs."""
    with files_lock(agent_id):
        held = _take_turn(agent_id)
        if held is None:
            _park(agent_id, inputs)
        return held


def drain_or_release(agent_id: str, held: Turn) -> list[InputItem]:
    """Take queued inputs or release the turn without an arrival race."""
    with files_lock(agent_id):
        batch = _take_queue(agent_id)
        if not batch:
            held.release()
        return batch
