"""Role configuration and workspace preparation."""

from __future__ import annotations

import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from beebot import timers

from . import backends
from . import session_ttl
from .records import AgentError, root

DEFAULT_BACKEND = backends.DEFAULT
DEFAULT_PERMISSIONS = "yolo"
DEFAULT_TYPE = "Agent"


class UnknownRole(AgentError):
    pass


class RoleConfigError(ValueError):
    pass


@dataclass
class Role:
    directory: Path
    type: str = DEFAULT_TYPE
    backend: str = DEFAULT_BACKEND
    permissions: str = DEFAULT_PERMISSIONS
    cwd: Path | None = None
    options: Mapping[str, Any] = field(default_factory=dict)
    session_ttl: session_ttl.Policy | None = None
    prompts: Mapping[str, str] = field(default_factory=dict)
    heartbeat: str | None = None

    @property
    def template(self) -> Path:
        return root() / "templates" / self.directory.name

    def prompt(self, name: str, default: str | None = None) -> str | None:
        prompt = self.prompts.get(name, default)
        return prompt if prompt and prompt.strip() else None


def load_role(name: str) -> Role:
    """Load a role directory and its optional configuration."""
    directory = root() / "configs" / "roles" / name
    if not directory.is_dir():
        raise UnknownRole(f"no role {name!r}; expected a directory at {directory}")

    config: dict[str, Any] = {}
    toml = directory / "role.toml"
    if toml.exists():
        try:
            config = tomllib.loads(toml.read_text("utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise UnknownRole(f"the role config at {toml} is broken: {exc}") from exc

    backend = config.get("backend", DEFAULT_BACKEND)
    cwd = config.get("cwd")
    try:
        ttl = session_ttl.parse_policy(config.get("session_ttl"))
        prompts = _parse_prompts(config.get("prompts"))
        heartbeat = _parse_heartbeat(config.get("heartbeat"))
    except (session_ttl.PolicyError, RoleConfigError) as exc:
        raise UnknownRole(f"the role config at {toml} is broken: {exc}") from exc
    return Role(
        directory=directory,
        type=config.get("type", DEFAULT_TYPE),
        backend=backend,
        permissions=config.get("permissions", DEFAULT_PERMISSIONS),
        cwd=(root() / cwd).resolve() if cwd else None,
        options=config.get("backend_options", {}).get(backend, {}),
        session_ttl=ttl,
        prompts=prompts,
        heartbeat=heartbeat,
    )


def _parse_prompts(value: Any) -> Mapping[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise RoleConfigError("prompts must be a table")
    for name, prompt in value.items():
        if not isinstance(prompt, str):
            raise RoleConfigError(f"prompts.{name} must be a string")
    return dict(value)


def _parse_heartbeat(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise RoleConfigError("heartbeat must be a table")
    if set(value) != {"every"}:
        raise RoleConfigError("heartbeat must define exactly one field: every")
    try:
        timers.parse_duration(value["every"])
    except timers.TimerError as exc:
        raise RoleConfigError(str(exc)) from exc
    return value["every"]


def workspace(role: Role, cwd: Path | str | None) -> Path:
    """Resolve a delegated cwd, falling back to the role's cwd."""
    where = Path(cwd) if cwd else role.cwd
    if where is None:
        raise AgentError(
            f"neither role {role.directory.name!r} nor this envelope says where "
            f"to work; add `cwd` to {role.directory / 'role.toml'} or pass one in"
        )
    return (where if where.is_absolute() else root() / where).resolve()


def seed(source: Path, destination: Path) -> None:
    """Copy missing template files without overwriting workspace content."""
    if not source.is_dir():
        return
    for item in sorted(source.rglob("*")):
        target = destination / item.relative_to(source)
        if item.is_symlink():
            if target.exists() or target.is_symlink():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(item.readlink(), target_is_directory=item.is_dir())
        elif item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif not target.exists() and not target.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
