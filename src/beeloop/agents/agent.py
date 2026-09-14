"""Agent lifecycle and type registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Mapping, Sequence

from . import backends
from . import records
from . import session_ttl
from .backends import BackendError, Delivery, InputItem, Session
from .records import (
    CLOSED,
    DORMANT,
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
        backend: str | None = None,
        **extra: Any,
    ) -> None:
        if agent_id is not None and role is not None:
            raise AgentError(
                "pass an agent_id to continue an agent, or a role to create "
                "one -- never both; they mean opposite things"
            )
        if agent_id is not None and backend is not None:
            raise AgentError(
                "pass a backend when creating an agent, not when restoring one"
            )
        if agent_id is not None:
            self.record = read(agent_id)
        elif role is not None:
            self.record = self._allocate(role, cwd, backend, **extra)
        else:
            raise AgentError(
                "an agent needs either an agent_id to continue or a role to create"
            )
        self.role = load_role(self.record["role"])
        self.backend = backends.get(self.record["backend"])
        self.ttl_policy = session_ttl.load_policy(self.agent_id)

    @classmethod
    def _allocate(
        cls,
        role_name: str,
        cwd: Path | str | None,
        backend: str | None,
        **extra: Any,
    ) -> dict[str, Any]:
        role = load_role(role_name)
        backend_name = backend or role.backend
        adapter = backends.get(backend_name)
        where = workspace(role, cwd)
        where.mkdir(parents=True, exist_ok=True)
        seed(role.template, where)

        stamp = now()
        agent_id = new_agent_id()
        record = {
            "type": cls.__name__,
            "agent_id": agent_id,
            "role": role_name,
            "backend": backend_name,
            "cwd": str(where),
            "session_id": adapter.open(),
            "status": PREPARED,
            "session_since": stamp,
            "session_last_turn": None,
            "session_turns": 0,
            "created": stamp,
            "last_turn": None,
            "turns": 0,
            "cost_usd": 0.0,
            **extra,
        }
        expiry_prompt = role.prompt("session_expire", session_ttl.EXPIRY_MESSAGE)
        session_ttl.arm(record, role.session_ttl, expiry_prompt)
        heartbeat_prompt = role.prompt("heartbeat")
        if heartbeat_prompt is not None and role.heartbeat is not None:
            from . import timers as agent_timers

            agent_timers.arm_recurring(
                record,
                message=heartbeat_prompt,
                every=role.heartbeat,
            )
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
    def session_id(self) -> str | None:
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
                delivery = self._process(batch)
                batch = drain_or_release(self.agent_id, held)
                if not batch:
                    return delivery

    def _process(self, batch: Sequence[InputItem]) -> Delivery:
        policy = self.ttl_policy
        expiry = (
            [item for item in batch if session_ttl.is_expiry_input(item)]
            if policy is not None
            else []
        )
        ordinary = [item for item in batch if item not in expiry]
        delivery = Delivery(text="")

        if expiry and self.status != DORMANT:
            if session_ttl.is_due(self.record, policy, now()):
                delivery = self._expire_session(expiry[0])
            else:
                def rearm(record: dict[str, Any]) -> None:
                    session_ttl.rearm(record, policy, expiry[0].content)

                self.record = records.modify(self.agent_id, self.SCHEMA, rearm)
                delivery = Delivery(text="session TTL deferred; recent activity")
        if ordinary:
            delivery = (
                self._wake_session(ordinary)
                if self.status == DORMANT
                else self._turn(self._with_session_init(ordinary))
            )
        return delivery

    def _with_session_init(self, batch: Sequence[InputItem]) -> Sequence[InputItem]:
        prompt = self.role.prompt("session_init")
        if self.record["turns"] or prompt is None:
            return batch
        return [session_ttl.init_input(prompt), *batch]

    def _turn(self, batch: Sequence[InputItem]) -> Delivery:
        try:
            delivery = self.backend.deliver(self.session(), batch)
        except BackendError as exc:
            if self.backend.classify(str(exc)) != "terminal":
                raise
            self.on_terminal(exc)
            return self._turn(batch)
        self._record_delivery(delivery, move_ttl=True)
        return delivery

    def _record_delivery(self, delivery: Delivery, *, move_ttl: bool) -> None:
        stamp = now()

        def change(record: dict[str, Any]) -> None:
            record.update({**delivery.updates, "last_turn": stamp})
            record["turns"] += 1
            record["session_turns"] += 1
            record["cost_usd"] += delivery.cost_usd or 0.0
            if move_ttl:
                record["session_last_turn"] = stamp
                session_ttl.move(record, self.ttl_policy)

        self.record = records.modify(self.agent_id, self.SCHEMA, change)

    def _expire_session(self, item: InputItem) -> Delivery:
        old_session = self.session()
        saved = True
        try:
            delivery = self.backend.deliver(old_session, [item])
            self._record_delivery(delivery, move_ttl=False)
            handoff = delivery.text.strip() or "The previous session saved no handoff."
        except BackendError:
            saved = False
            handoff = (
                "The previous session expired before it could save. Recover any "
                "existing work for this cwd from BeeLoop State."
            )

        try:
            session_ttl.write_handoff(self.agent_id, handoff)
        finally:
            try:
                self.backend.close(old_session)
            finally:
                def sleep(record: dict[str, Any]) -> None:
                    record.update(
                        {
                            "status": DORMANT,
                            "session_id": None,
                            "session_since": None,
                            "session_last_turn": None,
                        }
                    )
                    session_ttl.remove(record)

                self.record = records.modify(self.agent_id, self.SCHEMA, sleep)
        outcome = "saved" if saved else "failed"
        return Delivery(text=f"session TTL expired; handoff {outcome}")

    def _wake_session(self, inputs: Sequence[InputItem]) -> Delivery:
        stamp = now()
        session_id = self.backend.open()
        expiry_prompt = self.role.prompt(
            "session_expire", session_ttl.EXPIRY_MESSAGE
        )

        def wake(record: dict[str, Any]) -> None:
            record.update(
                {
                    "session_id": session_id,
                    "status": PREPARED,
                    "session_since": stamp,
                    "session_last_turn": None,
                    "session_turns": 0,
                }
            )
            session_ttl.arm(
                record,
                self.ttl_policy,
                expiry_prompt,
            )

        self.record = records.modify(self.agent_id, self.SCHEMA, wake)

        handoff = session_ttl.read_handoff(self.agent_id)
        batch = (
            [
                session_ttl.restore_input(
                    handoff,
                    self.role.prompt("session_resume", session_ttl.RESUME_MESSAGE),
                ),
                *inputs,
            ]
            if handoff
            else list(inputs)
        )
        delivery = self._turn(batch)
        if handoff:
            session_ttl.clear_handoff(self.agent_id)
        return delivery

    def on_terminal(self, exc: BackendError) -> None:
        """Close an agent whose backend session cannot be resumed."""
        self.record = self._update({"status": CLOSED})
        raise NotResumable(
            f"agent {self.agent_id} lost its session and has no task to be "
            f"briefed from, so it is closed: {exc}"
        ) from exc

    def session(self) -> Session:
        if self.session_id is None:
            raise AgentError(f"agent {self.agent_id} has no active backend session")
        return Session(
            agent_id=self.agent_id,
            agent_dir=agent_path(self.agent_id).resolve(),
            session_id=self.session_id,
            prepared=self.status == PREPARED,
            cwd=self.cwd,
            permissions=self.role.permissions,
            options=self.role.options_for(self.record["backend"]),
        )

    def close(self) -> None:
        if self.status != DORMANT:
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


def create(
    role: str,
    cwd: Path | str | None = None,
    backend: str | None = None,
    **extra: Any,
) -> Agent:
    found = load_role(role)
    return agent_class(found.type, found)(
        role=role, cwd=cwd, backend=backend, **extra
    )
