"""The agent, and the one file it is.

An `Agent` object is a per-tick projection of `runtime/agents/<id>.json` plus its
role's static config. Nothing durable is held in memory, because the gateway
exits between ticks -- so if this class ever holds a PID, a subprocess handle, a
lock handle or a queue, it cannot be reconstructed and the design is broken.

The backend is stateless, which makes that record the only durable thing in the
system: the transcript still exists on the provider's disk, but it is reachable
only through a `session_id` that lives nowhere else.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import secrets
import shutil
import tempfile
import tomllib
import uuid
from contextlib import contextmanager
from functools import cache
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Iterator, Mapping, Sequence

import jsonschema

from . import backends
from .backends import BackendError, Delivery, InputItem, Session

# The public surface, declared here rather than in `__init__` so there is one
# list to keep rather than two. The package re-exports exactly this, so a name
# missing from it is private no matter how ordinary it looks.
__all__ = [
    # constants
    "CLOSED",
    "DEFAULT_BACKEND",
    "DEFAULT_PERMISSIONS",
    "DEFAULT_TYPE",
    "PREPARED",
    "STAMP",
    "UTC",
    # errors
    "AgentError",
    "NotResumable",
    "UnknownAgent",
    "UnknownRole",
    # paths
    "queue_path",
    "record_path",
    "root",
    "runtime",
    # records
    "Turn",
    "claim",
    "drain_or_release",
    "new_agent_id",
    "now",
    "read",
    "record_lock",
    "update",
    "validate",
    "write",
    # roles
    "Role",
    "load_role",
    "seed",
    "workspace",
    # the agent and its registry
    "REGISTRY",
    "Agent",
    "agent_class",
    "create",
    "register",
    "restore",
]

UTC = dt.timezone.utc
STAMP = "%Y-%m-%dT%H:%M:%SZ"

# What a role gets when it says nothing. Every field is optional and so is the
# file, which is what makes "adding a role is a directory" literally true --
# an empty directory is a working role. The default backend's NAME lives in the
# registry, not here: this module must survive a grep for any provider.
DEFAULT_BACKEND = backends.DEFAULT
DEFAULT_PERMISSIONS = "yolo"
# The ordinary class, registered under this name at import. A role that names
# no type gets it, which is what keeps a config-free directory a working role.
# Spelled out rather than `Agent.__name__` because `Role`'s field default is
# evaluated before that class exists; a test pins the two together.
DEFAULT_TYPE = "Agent"

# The adapter reports "active" itself, in the record updates it returns, so
# only the two statuses this module actually sets are named here.
PREPARED, CLOSED = "prepared", "closed"


class AgentError(RuntimeError):
    """Anything the gateway refuses."""


class UnknownAgent(AgentError):
    pass


class UnknownRole(AgentError):
    pass


class NotResumable(AgentError):
    """A closed agent. Allocate a new one; do not revive this."""


# --------------------------------------------------------------------- filing


def root() -> Path:
    """The one root every path is relative to.

    A function rather than a constant so a test can point BEEBOT_ROOT somewhere
    else after this module is imported.
    """
    if given := os.environ.get("BEEBOT_ROOT"):
        found = Path(given)
        if not found.is_dir():
            raise AgentError(
                f"BEEBOT_ROOT is set to {given!r}, which is not a directory; "
                f"unset it or point it at the BeeBot5.0 tree"
            )
        return found.resolve()
    return Path(__file__).resolve().parents[1]


def runtime(*parts: str) -> Path:
    directory = root().joinpath("runtime", *parts)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def record_path(agent_id: str) -> Path:
    return runtime("agents") / f"{agent_id}.json"


def queue_path(agent_id: str) -> Path:
    return runtime("queue") / f"{agent_id}.jsonl"


def now() -> str:
    return dt.datetime.now(UTC).strftime(STAMP)


def new_agent_id() -> str:
    """RFC 9562 layout by hand: 48 bits of millisecond epoch, then random.

    Time-ordered, so a directory listing is a creation-order audit, and
    unguessable, because knowing an address is the permission. `uuid.uuid7`
    arrives in 3.14 and this goes away; the tree runs 3.12.

    Deliberately not reused as a session id -- a refresh mints a new session
    under a stable agent id, and conflating them makes the second refresh
    impossible.
    """
    stamp = int(dt.datetime.now(UTC).timestamp() * 1000)
    raw = bytearray(stamp.to_bytes(6, "big") + secrets.token_bytes(10))
    raw[6] = (raw[6] & 0x0F) | 0x70  # version 7
    raw[8] = (raw[8] & 0x3F) | 0x80  # variant 10
    return str(uuid.UUID(bytes=bytes(raw)))


# ---------------------------------------------------------------------- locks


@contextmanager
def record_lock() -> Iterator[None]:
    """Microseconds, around a read-modify-write.

    Separate from the delivery lock on purpose: conflating them would make one
    agent's turn block every other agent's dispatch, and would leave no way for
    a tool to write to a record while that agent's turn is still running.
    """
    with open(runtime("agents") / ".lock", "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class Turn:
    """The delivery lock, as an object that can be released early.

    A context manager alone will not do: the holder has to give this up from
    *inside* the record lock, so that an arrival cannot park itself between the
    holder finding the queue empty and the holder letting go.
    """

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
    """Try to become the one process delivering to this agent."""
    path = runtime("locks") / f"{agent_id}"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return Turn(handle)


# ---------------------------------------------------------------- the record


def read(agent_id: str) -> dict[str, Any]:
    """One read, no lock, no replay. `os.replace` cannot tear."""
    path = record_path(agent_id)
    if not path.exists():
        raise UnknownAgent(f"no agent {agent_id!r} at {path}")
    return json.loads(path.read_text("utf-8"))


@cache
def _schema(name: str) -> dict:
    """One file per agent class, named for it, loaded on first use.

    A missing or broken file refuses every write of that type, rather than
    letting records through unvalidated and finding out later.
    """
    path = Path(__file__).resolve().parent / "assets" / f"{name}.json"
    try:
        return json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        available = sorted(q.stem for q in path.parent.glob("*.json"))
        raise AgentError(
            f"no record schema named {name!r}; assets/ has "
            f"{', '.join(available) or 'nothing'}"
        ) from None
    except (OSError, ValueError) as exc:
        raise AgentError(f"the record schema at {path} is broken: {exc}") from exc


def validate(record: Mapping[str, Any], schema: str) -> None:
    """Refuse a record that is not what its type says it is.

    The field names in this system are string keys in a dozen places, so a typo
    is otherwise silent: `update(id, {"statuss": CLOSED})` would write a junk
    field, leave `status` alone, and leave the agent open forever.
    """
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
    _write(record_path(record["agent_id"]), json.dumps(record, indent=2, sort_keys=True))


def update(
    agent_id: str,
    schema: str,
    fields: Mapping[str, Any] | None = None,
    bump: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Read-modify-write under the record lock.

    It re-reads rather than writing a caller's in-memory copy, because a tool
    running inside a live turn will write to this same file and a blind write
    would erase it.
    """
    with record_lock():
        record = read(agent_id)
        record.update(fields or {})
        for key, amount in (bump or {}).items():
            record[key] = (record.get(key) or 0) + amount
        write(record, schema)
        return record


def _write(path: Path, body: str) -> None:
    """Atomically replace one file using a same-directory, fsynced temp file."""
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


# ----------------------------------------------------------------- the queue


def _park(agent_id: str, inputs: Sequence[InputItem]) -> None:
    path = queue_path(agent_id)
    with open(path, "a", encoding="utf-8") as handle:
        for item in inputs:
            handle.write(json.dumps({"source": item.source, "content": item.content}) + "\n")


def _take_queue(agent_id: str) -> list[InputItem]:
    """Everything parked, in arrival order, and the file emptied."""
    path = queue_path(agent_id)
    if not path.exists():
        return []
    lines = [line for line in path.read_text("utf-8").splitlines() if line.strip()]
    if lines:
        path.write_text("", encoding="utf-8")
    return [InputItem(**json.loads(line)) for line in lines]


def claim(agent_id: str, inputs: Sequence[InputItem]) -> Turn | None:
    """Become the holder, or park and walk away.

    Both halves happen under the record lock, which is what closes the race: an
    arrival cannot append between a holder checking the queue and releasing.
    """
    with record_lock():
        held = _take_turn(agent_id)
        if held is None:
            _park(agent_id, inputs)
            return None
        return held


def drain_or_release(agent_id: str, held: Turn) -> list[InputItem]:
    """Take the next batch, or give up the turn.

    The release happens INSIDE the record lock, so nothing can park itself into
    a queue this has just declared empty.
    """
    with record_lock():
        batch = _take_queue(agent_id)
        if not batch:
            held.release()
        return batch


# ----------------------------------------------------------------- the role


@dataclass
class Role:
    """A rank, not a job.

    `orchestrator` and `worker` are roles; babysitting a PR is a task. The
    directory is the truth -- instructions, skills and MCP config live in it as
    files, and native discovery picks them up with no gateway involvement.

    The two axes, stated plainly because they are easy to confuse: a role
    selects a DIRECTORY, and its `type` selects a CLASS. Role is configuration;
    type is code.
    """

    directory: Path
    type: str = DEFAULT_TYPE
    backend: str = DEFAULT_BACKEND
    permissions: str = DEFAULT_PERMISSIONS
    cwd: Path | None = None
    options: Mapping[str, Any] = field(default_factory=dict)

    @property
    def template(self) -> Path:
        """The one subdirectory copied into an agent's cwd.

        Configuration sits outside it, so `role.toml` cannot leak into a
        workspace by construction and no blocklist is needed.
        """
        return self.directory / "template"


def load_role(name: str) -> Role:
    """Read `configs/roles/<name>/role.toml`, which need not exist.

    The `[backend_options.<backend>]` block is lifted out by name and passed on
    unread: inspecting it here would put provider knowledge in the one module
    that must not have any, and adding a role would stop being a directory.
    """
    directory = root() / "configs" / "roles" / name
    if not directory.is_dir():
        raise UnknownRole(f"no role {name!r}; expected a directory at {directory}")

    config: dict[str, Any] = {}
    toml = directory / "role.toml"
    if toml.exists():
        try:
            config = tomllib.loads(toml.read_text("utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            # Every failure out of this module is an AgentError; a bare
            # TOMLDecodeError escaping would be the one exception.
            raise UnknownRole(f"the role config at {toml} is broken: {exc}") from exc

    backend = config.get("backend", DEFAULT_BACKEND)
    cwd = config.get("cwd")
    return Role(
        directory=directory,
        type=config.get("type", DEFAULT_TYPE),
        backend=backend,
        permissions=config.get("permissions", DEFAULT_PERMISSIONS),
        cwd=(root() / cwd).resolve() if cwd else None,
        options=config.get("backend_options", {}).get(backend, {}),
    )


def workspace(role: Role, cwd: Path | str | None) -> Path:
    """Where an agent of this role, handed this cwd, will work.

    Resolution ONLY -- nothing is created and nothing is seeded, because the
    dispatcher computes this to build a routing key on every tick and a lookup
    that made directories would be a lookup with a side effect.

    The delegation wins over the role: a worker's directory comes from whoever
    delegated to it, and the role's own `cwd` is the fallback for a role that
    always works in one place.
    """
    where = Path(cwd) if cwd else role.cwd
    if where is None:
        raise AgentError(
            f"neither role {role.directory.name!r} nor this envelope says where "
            f"to work; add `cwd` to {role.directory / 'role.toml'} or pass one in"
        )
    return (where if where.is_absolute() else root() / where).resolve()


def seed(source: Path, destination: Path) -> None:
    """Copy what is not already there. Never overwrite; record nothing.

    Per file rather than the spec's all-or-nothing "a populated cwd is left
    alone": strictly safer, since it cannot clobber a human's edit, and it lets
    a half-set-up directory be completed. `rglob` includes dotfiles, and the
    whole tree is copied without naming anything in it -- this module may not
    know what a provider calls its config file.
    """
    if not source.is_dir():
        return
    for item in sorted(source.rglob("*")):
        target = destination / item.relative_to(source)
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


# ----------------------------------------------------------------- the agent


class Agent:
    """One agent, and the single file it is.

    A per-tick projection of runtime/agents/<id>.json plus its role's static
    config. Nothing durable is held in memory, because the gateway exits between
    ticks -- so a PID, a subprocess handle, a lock handle or a queue held here
    would all make the agent unreconstructible.
    """

    # Names a file in assets/. Inherited, so a subclass that only changes
    # BEHAVIOUR needs no schema of its own; one whose record shape genuinely
    # differs sets this to its own name and brings that file alongside.
    # Deliberately not `cls.__name__`: this names a schema, not a type, and
    # conflating them would force a duplicate file on every subclass.
    SCHEMA: ClassVar[str] = "Agent"

    def __init__(
        self,
        agent_id: str | None = None,
        *,
        role: str | None = None,
        cwd: Path | str | None = None,
        **extra: Any,
    ) -> None:
        """Restore an agent, or create one.

        `agent_id` continues an agent that exists; `role` creates one that does
        not. They are never both meaningful -- the same rule the envelope format
        states, enforced a layer down so a caller cannot get it wrong.

        Nothing names a type here: the class IS the type, and by the time this
        runs the caller has already resolved one to reach it.

        Naming an agent_id with no record raises rather than quietly creating a
        replacement: minting a new agent under the covers would strand whatever
        was pointing at the old id.
        """
        if agent_id is not None and role is not None:
            raise AgentError(
                "pass an agent_id to continue an agent, or a role to create "
                "one -- never both; they mean opposite things"
            )
        if agent_id is not None:
            self.record = read(agent_id)          # raises UnknownAgent
        elif role is not None:
            self.record = self._allocate(role, cwd, **extra)
        else:
            raise AgentError(
                "an agent needs either an agent_id to continue or a role to create"
            )
        self.role = load_role(self.record["role"])
        self.backend = backends.get(self.record["backend"])

    @classmethod
    def _allocate(
        cls,
        role_name: str,
        cwd: Path | str | None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Mint an identity and reserve a session, then write the record LAST.

        Spends nothing: `open` reserves an id without a model call, so losing a
        race to allocate costs zero. Writing last means a crash leaves a wasted
        uuid rather than a record naming a session that was never reserved.

        The cwd is settled and seeded first, so a failure there costs neither a
        uuid nor a reserved session.
        """
        role = load_role(role_name)
        where = workspace(role, cwd)
        where.mkdir(parents=True, exist_ok=True)
        seed(role.template, where)

        stamp = now()
        record = {
            "type": cls.__name__,
            "agent_id": new_agent_id(),
            "role": role_name,
            "backend": role.backend,
            # Already resolved by `workspace`, which the dispatcher calls too --
            # so a route's cwd and its agent's cwd compare as plain strings.
            "cwd": str(where),
            "session_id": backends.get(role.backend).open(),
            "status": PREPARED,
            "session_since": stamp,
            "session_turns": 0,
            "created": stamp,
            "last_turn": None,
            "turns": 0,
            "cost_usd": 0.0,
            **extra,
        }
        write(record, cls.SCHEMA)
        return record

    def _update(
        self,
        fields: Mapping[str, Any] | None = None,
        bump: Mapping[str, float] | None = None,
    ) -> dict[str, Any]:
        return update(self.agent_id, self.SCHEMA, fields, bump)

    # ----------------------------------------------------------- properties

    @property
    def agent_id(self) -> str:
        return self.record["agent_id"]

    @property
    def session_id(self) -> str:
        return self.record["session_id"]

    @property
    def status(self) -> str:
        return self.record["status"]

    @property
    def cwd(self) -> Path:
        return Path(self.record["cwd"])

    # --------------------------------------------------------------- acting

    def spin(self, inputs: Sequence[InputItem]) -> Delivery:
        """One lock acquisition, one turn per drain iteration.

        A caller hands this a batch once and does not loop: while inputs keep
        arriving for an agent that is already working, this keeps delivering
        them, in arrival order, until the queue is empty.
        """
        if self.status == CLOSED:
            raise NotResumable(
                f"agent {self.agent_id} is closed; allocate a new one"
            )

        held = claim(self.agent_id, inputs)
        if held is None:
            return Delivery(text="", parked=True)

        with held:
            batch: Sequence[InputItem] = inputs
            while True:
                try:
                    delivery = self._turn(batch)
                except BackendError as exc:
                    if self.backend.classify(str(exc)) != "terminal":
                        raise
                    # The session is gone. Whether this agent can outlive that
                    # is the subclass's answer, not the dispatcher's.
                    self.on_terminal(exc)
                    delivery = self._turn(batch)
                batch = drain_or_release(self.agent_id, held)
                if not batch:
                    return delivery

    def _turn(self, batch: Sequence[InputItem]) -> Delivery:
        delivery = self.backend.deliver(self.session(), batch)
        self.record = self._update(
            {**delivery.updates, "last_turn": now()},
            bump={
                "turns": 1,
                "session_turns": 1,
                "cost_usd": delivery.cost_usd or 0.0,
            },
        )
        return delivery

    def on_terminal(self, exc: BackendError) -> None:
        """The backend session no longer exists.

        A plain agent cannot survive that: there is nothing to brief a
        replacement from, so it dies here and whoever delegated it finds a
        closed record. A persistent agent overrides this to refresh -- a new
        session under the SAME agent_id, so nothing pointing at this agent has
        to be rewritten.
        """
        self.record = self._update({"status": CLOSED})
        raise NotResumable(
            f"agent {self.agent_id} lost its session and has no task to be "
            f"briefed from, so it is closed: {exc}"
        ) from exc

    def session(self) -> Session:
        """Rebuilt every call, never stored.

        Record fields and role fields meet here and nowhere else. The role half
        is late-bound on purpose: snapshotting it into the record would mean
        editing a role only affected agents created afterwards.
        """
        return Session(
            agent_id=self.agent_id,
            session_id=self.session_id,
            prepared=self.status == PREPARED,
            cwd=self.cwd,
            permissions=self.role.permissions,
            options=self.role.options,
        )

    def close(self) -> None:
        self.backend.close(self.session())
        self.record = self._update({"status": CLOSED})


# ------------------------------------------------------------------- the types


REGISTRY: dict[str, type[Agent]] = {}


def register(cls: type[Agent]) -> None:
    """Publish a class under its own name.

    The escape hatch for behaviour that config cannot express. Everything else
    stays a directory. The key is `cls.__name__` rather than a string the caller
    picks, so what a role asks for and what the class is called cannot drift --
    and a class's schema file in assets/ is named for it too.
    """
    REGISTRY[cls.__name__] = cls


register(Agent)


def agent_class(name: str, role: Role | None = None) -> type[Agent]:
    """Resolve one type name to the class that runs it.

    A name nothing is registered under is refused rather than quietly handed
    back an ordinary agent: `type = "Persistant"` must fail loudly. It costs
    nothing, because a role that says nothing gets the always-registered
    default -- so adding a role stays a directory plus a config file, touching
    no code here, and adding BEHAVIOUR is a subclass that registers itself.
    """
    if found := REGISTRY.get(name):
        return found
    known = ", ".join(sorted(REGISTRY))
    where = (
        f"role {role.directory.name!r} asks for it in {role.directory / 'role.toml'}"
        if role is not None
        else "a record asks for it"
    )
    raise AgentError(
        f"no agent class is registered under type {name!r}, and {where}; "
        f"the registered types are {known}"
    )


def restore(agent_id: str) -> Agent:
    """Continue an agent that exists, as whatever type it says it is."""
    return agent_class(read(agent_id)["type"])(agent_id)


def create(role: str, cwd: Path | str | None = None, **extra: Any) -> Agent:
    """Start a new agent of this ROLE, which mints its own id and record.

    Creation is role-driven: an envelope names a role, and the role's `type`
    names the class that runs it. That is what lets a new role be a directory
    while the gateway still knows what to instantiate.

    The class is not told which type it is. Resolving the name is this
    function's job, and the answer it gets back already knows its own name.
    """
    found = load_role(role)
    return agent_class(found.type, found)(role=role, cwd=cwd, **extra)
