"""Role configuration and workspace resolution."""

from __future__ import annotations

import json
import tomllib
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from beeloop import timers

from . import backends
from . import session_ttl
from .backends.base import validate_permissions
from .records import AgentError, agent_path, read, root

DEFAULT_BACKEND = backends.DEFAULT
DEFAULT_PERMISSIONS = "yolo"
DEFAULT_TYPE = "Agent"
CONFIG_FIELDS = frozenset({
    "type", "backend", "permissions", "cwd", "backend_options", "session_ttl",
    "prompts", "heartbeat", "allowed_receivers",
})


class UnknownRole(AgentError):
    pass


class RoleConfigError(ValueError):
    pass


@dataclass(frozen=True)
class AllowedReceivers:
    roles: frozenset[str] = frozenset()
    ids: frozenset[str] = frozenset()

    def allows(self, role: str, agent_id: str) -> bool:
        return (
            "*" in self.roles or "*" in self.ids
            or role in self.roles or agent_id in self.ids
        )

    def allows_role(self, role: str) -> bool:
        return "*" in self.roles or role in self.roles


@dataclass
class Role:
    directory: Path
    type: str = DEFAULT_TYPE
    backend: str = DEFAULT_BACKEND
    permissions: str = DEFAULT_PERMISSIONS
    cwd: Path | None = None
    backend_options: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    session_ttl: session_ttl.Policy | None = None
    prompts: Mapping[str, str] = field(default_factory=dict)
    heartbeat: str | None = None
    allowed_receivers: AllowedReceivers = field(default_factory=AllowedReceivers)

    def prompt(self, name: str, default: str | None = None) -> str | None:
        prompt = self.prompts.get(name, default)
        return prompt if prompt and prompt.strip() else None

    @property
    def options(self) -> Mapping[str, Any]:
        return self.options_for(self.backend)

    def options_for(self, backend: str) -> Mapping[str, Any]:
        return self.backend_options.get(backend, {})


def load_role(name: str, config: Mapping[str, Any] | None = None) -> Role:
    """Load a role directory and its optional configuration."""
    if not isinstance(name, str) or not name:
        raise UnknownRole("role must be a non-empty string")
    directory = root() / "configs" / "roles" / name
    if not directory.is_dir():
        raise UnknownRole(f"no role {name!r}; expected a directory at {directory}")

    defaults: dict[str, Any] = {}
    toml = directory / "role.toml"
    if toml.exists():
        try:
            defaults = tomllib.loads(toml.read_text("utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise UnknownRole(f"the role config at {toml} is broken: {exc}") from exc

    try:
        if config is not None:
            validate_overrides(config)
        overrides = {key: value for key, value in (config or {}).items() if key != "instance"}
        return parse_role(directory, merge_config(defaults, overrides))
    except (session_ttl.PolicyError, RoleConfigError) as exc:
        raise UnknownRole(f"the role config at {toml} is broken: {exc}") from exc


def parse_role(directory: Path, config: Mapping[str, Any]) -> Role:
    """Apply the same role contract to file defaults and merged overrides."""
    if unknown := config.keys() - CONFIG_FIELDS:
        raise RoleConfigError(f"unknown config field(s): {', '.join(sorted(unknown))}")
    for name in ("type", "backend"):
        if name in config and (not isinstance(config[name], str) or not config[name]):
            raise RoleConfigError(f"{name} must be a non-empty string")
    cwd = config.get("cwd")
    if cwd is not None and (not isinstance(cwd, str) or not cwd):
        raise RoleConfigError("cwd must be a non-empty string or null")
    backend = config.get("backend", DEFAULT_BACKEND)
    adapter = backends.get(backend)
    permissions = validate_permissions(config.get("permissions", DEFAULT_PERMISSIONS))
    options = config.get("backend_options", {})
    if not isinstance(options, Mapping):
        raise RoleConfigError("backend_options must be a table")
    for name, values in options.items():
        if not isinstance(values, Mapping):
            raise RoleConfigError(f"backend_options.{name} must be a table")
        backends.get(name).validate_config(values)
    adapter.validate_config(options.get(backend, {}))
    return Role(
        directory=directory,
        type=config.get("type", DEFAULT_TYPE),
        backend=backend,
        permissions=permissions,
        cwd=(root() / cwd).resolve() if cwd else None,
        backend_options=options,
        session_ttl=session_ttl.parse_policy(config.get("session_ttl")),
        prompts=_parse_prompts(config.get("prompts")),
        heartbeat=_parse_heartbeat(config.get("heartbeat")),
        allowed_receivers=parse_allowed_receivers(config.get("allowed_receivers", {})),
    )


def validate_overrides(config: Any) -> None:
    if not isinstance(config, dict):
        raise RoleConfigError("config must be an object")
    if not all(isinstance(key, str) for key in config):
        raise RoleConfigError("config field names must be strings")
    if unknown := config.keys() - CONFIG_FIELDS - {"instance"}:
        raise RoleConfigError(f"unknown config field(s): {', '.join(sorted(unknown))}")
    if "instance" in config:
        value = config["instance"]
        if not isinstance(value, str) or not value or value == "fresh":
            raise RoleConfigError("instance must be a non-empty string other than 'fresh'")
    try:
        if json.loads(json.dumps(config, allow_nan=False)) != config:
            raise RoleConfigError("config must contain only JSON objects, arrays, and scalar values")
    except (TypeError, ValueError) as exc:
        raise RoleConfigError(f"config must contain JSON values: {exc}") from exc


def merge_config(defaults: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Merge tables recursively; explicit scalars, lists, and null replace defaults."""
    merged = deepcopy(dict(defaults))
    for key, value in overrides.items():
        merged[key] = (
            merge_config(merged[key], value)
            if isinstance(merged.get(key), Mapping) and isinstance(value, Mapping)
            else deepcopy(value)
        )
    return merged


def load_agent_config(agent_id: str) -> dict[str, Any]:
    """Read policy snapshots with the agent's explicit overrides applied."""
    directory = agent_path(agent_id)
    config = tomllib.loads((directory / "config.toml").read_text("utf-8"))
    return merge_config(config, read(agent_id).get("config", {}))


def parse_allowed_receivers(value: Any) -> AllowedReceivers:
    if not isinstance(value, Mapping):
        raise RoleConfigError("allowed_receivers must be a table")
    if unknown := value.keys() - {"roles", "ids"}:
        raise RoleConfigError(f"unknown allowed_receivers field(s): {', '.join(sorted(unknown))}")
    for name in ("roles", "ids"):
        values = value.get(name, [])
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise RoleConfigError(f"allowed_receivers.{name} must be a list of strings")
    return AllowedReceivers(frozenset(value.get("roles", [])), frozenset(value.get("ids", [])))


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
    if cwd is not None and (not isinstance(cwd, (str, Path)) or not str(cwd)):
        raise AgentError("cwd must be a non-empty path")
    where = Path(cwd) if cwd is not None else role.cwd
    if where is None:
        raise AgentError(
            f"neither role {role.directory.name!r} nor this envelope says where "
            f"to work; add `cwd` to {role.directory / 'role.toml'} or pass one in"
        )
    return (where if where.is_absolute() else root() / where).resolve()
