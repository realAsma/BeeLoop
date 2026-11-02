"""Durable agent records, queues, and their locks."""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import re
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

AGENT_ID_PATTERN = r"[a-z]{2,12}-[a-z]{2,12}"
_ADJECTIVES = (
    "able agile alert amber ample aqua avid azure "
    "balanced basic beaming bold brave breezy bright brisk "
    "bronze calm candid careful cheerful clever cloudy cobalt "
    "comfy coral cozy crisp curious dapper daring dawn "
    "deep deft eager early easy elated emerald even "
    "fair faithful fancy fast festive fiery fine focused "
    "fond frank fresh friendly frosty gentle glad glowing "
    "golden graceful grand green happy hardy hazy helpful "
    "honest humble icy ideal ivory jolly joyful keen "
    "kind leafy light lively loyal lucky lunar mellow "
    "merry mighty mint modest neat nimble noble novel "
    "olive patient peach pearl perky placid playful polite "
    "proud quiet rapid ready regal rosy royal ruby "
    "sage sandy serene sharp silver simple sleek smart "
    "smooth snowy solar solid spry steady still sunny "
    "swift tidy vivid warm wise witty young zesty "
).split()
_NOUNS = (
    "alpaca ant ape badger bat bear beaver bee "
    "beetle bison boar bobcat buffalo bunny camel canary "
    "carp cat cheetah chick clam cobra condor coral "
    "cougar coyote crab crane cricket crow deer dingo "
    "dog dolphin donkey dove dragon duck eagle egret "
    "elk falcon ferret finch firefly fish flamingo fly "
    "fox frog gazelle gecko gerbil goat goose gopher "
    "gull hamster hare hawk hedgehog heron ibis jackal "
    "jaguar jay koala koi lark lemur leopard lion "
    "llama lobster loon lynx magpie manta mink mole "
    "moose moth mouse mule newt octopus orca osprey "
    "otter owl ox panda panther parrot pelican penguin "
    "perch pigeon pony puma quail rabbit raven robin "
    "salmon seal shark sheep sloth snail snake sparrow "
    "spider squid swan swift tern tiger toad trout "
    "turtle wasp weasel whale wolf wren yak zebra "
).split()


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
    if not isinstance(agent_id, str) or re.fullmatch(AGENT_ID_PATTERN, agent_id) is None:
        raise AgentError(f"invalid agent ID {agent_id!r}; expected an adjective-noun name")
    return root().joinpath("runtime", "agents", agent_id)


def record_path(agent_id: str) -> Path:
    return agent_path(agent_id) / "record.json"


def queue_path(agent_id: str) -> Path:
    return agent_path(agent_id) / "queue.jsonl"


def now() -> str:
    return dt.datetime.now(UTC).strftime(STAMP)


def new_agent_id() -> str:
    """Reserve a readable ID by atomically creating its agent directory."""
    directory = runtime("agents")
    capacity = len(_ADJECTIVES) * len(_NOUNS)
    start = secrets.randbelow(capacity)
    for offset in range(capacity):
        adjective, noun = divmod((start + offset) % capacity, len(_NOUNS))
        agent_id = f"{_ADJECTIVES[adjective]}-{_NOUNS[noun]}"
        try:
            (directory / agent_id).mkdir()
        except FileExistsError:
            continue
        return agent_id
    raise AgentError(f"all {capacity:,} agent names are reserved")


def new_timer_id() -> str:
    """Return a time-ordered UUIDv7 timer ID."""
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
