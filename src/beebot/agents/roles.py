"""Role configuration and workspace preparation."""

from __future__ import annotations

import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from . import backends
from .records import AgentError, root

DEFAULT_BACKEND = backends.DEFAULT
DEFAULT_PERMISSIONS = "yolo"
DEFAULT_TYPE = "Agent"


class UnknownRole(AgentError):
    pass


@dataclass
class Role:
    directory: Path
    type: str = DEFAULT_TYPE
    backend: str = DEFAULT_BACKEND
    permissions: str = DEFAULT_PERMISSIONS
    cwd: Path | None = None
    options: Mapping[str, Any] = field(default_factory=dict)

    @property
    def template(self) -> Path:
        return self.directory / "template"


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
    return Role(
        directory=directory,
        type=config.get("type", DEFAULT_TYPE),
        backend=backend,
        permissions=config.get("permissions", DEFAULT_PERMISSIONS),
        cwd=(root() / cwd).resolve() if cwd else None,
        options=config.get("backend_options", {}).get(backend, {}),
    )


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
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
