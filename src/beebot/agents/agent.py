"""Agent lifecycle and type registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Mapping, Sequence

from . import backends
from .backends import BackendError, Delivery, InputItem, Session
from .records import (
    CLOSED,
    PREPARED,
    AgentError,
    agent_path,
    claim,
    drain_or_release,
    new_agent_id,
    now,
    read,
    update,
    write,
    write_file,
)
from .roles import Role, load_role, seed, workspace


class NotResumable(AgentError):
    pass


class Agent:
    """An agent reconstructed from its record and current role configuration."""

    SCHEMA: ClassVar[str] = "Agent"

    def __init__(
        self,
        agent_id: str | None = None,
        *,
        role: str | None = None,
        cwd: Path | str | None = None,
        **extra: Any,
    ) -> None:
        if agent_id is not None and role is not None:
            raise AgentError(
                "pass an agent_id to continue an agent, or a role to create "
                "one -- never both; they mean opposite things"
            )
        if agent_id is not None:
            self.record = read(agent_id)
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
        role = load_role(role_name)
        where = workspace(role, cwd)
        where.mkdir(parents=True, exist_ok=True)
        seed(role.template, where)

        stamp = now()
        agent_id = new_agent_id()
        record = {
            "type": cls.__name__,
            "agent_id": agent_id,
            "role": role_name,
            "backend": role.backend,
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
        directory = agent_path(agent_id)
        directory.mkdir(parents=True)
        role_config = role.directory / "role.toml"
        write_file(
            directory / "config.toml",
            role_config.read_text("utf-8") if role_config.exists() else "",
        )
        write(record, cls.SCHEMA)
        return record

    def _update(
        self,
        fields: Mapping[str, Any] | None = None,
        bump: Mapping[str, float] | None = None,
    ) -> dict[str, Any]:
        return update(self.agent_id, self.SCHEMA, fields, bump)

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

    def spin(self, inputs: Sequence[InputItem]) -> Delivery:
        """Deliver a batch and drain inputs parked during the turn."""
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
        """Close an agent whose backend session cannot be resumed."""
        self.record = self._update({"status": CLOSED})
        raise NotResumable(
            f"agent {self.agent_id} lost its session and has no task to be "
            f"briefed from, so it is closed: {exc}"
        ) from exc

    def session(self) -> Session:
        return Session(
            agent_id=self.agent_id,
            agent_dir=agent_path(self.agent_id).resolve(),
            session_id=self.session_id,
            prepared=self.status == PREPARED,
            cwd=self.cwd,
            permissions=self.role.permissions,
            options=self.role.options,
        )

    def close(self) -> None:
        self.backend.close(self.session())
        self.record = self._update({"status": CLOSED})


REGISTRY: dict[str, type[Agent]] = {Agent.__name__: Agent}


def register(cls: type[Agent]) -> None:
    REGISTRY[cls.__name__] = cls


def agent_class(name: str, role: Role | None = None) -> type[Agent]:
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
    return agent_class(read(agent_id)["type"])(agent_id)


def create(role: str, cwd: Path | str | None = None, **extra: Any) -> Agent:
    found = load_role(role)
    return agent_class(found.type, found)(role=role, cwd=cwd, **extra)
