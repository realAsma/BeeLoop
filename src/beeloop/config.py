"""Persistent configuration for a BeeLoop deployment."""

from __future__ import annotations

import os
import tempfile
import tomllib
from pathlib import Path
from typing import Any


class ConfigError(RuntimeError):
    pass


CONFIG_DIR = Path(".config") / "beeloop"
LOOP_CONFIG = "loop.toml"
STATE_CONFIG = "state.toml"
ROOT_DIRECTORIES = (
    "runtime",
    "logs",
    "inputs.d",
    "configs",
    "templates",
    "workspaces",
)


def config_path() -> Path:
    try:
        return Path.home() / CONFIG_DIR / LOOP_CONFIG
    except RuntimeError as exc:
        raise ConfigError(f"cannot determine the BeeLoop config directory: {exc}") from exc


def state_config_path() -> Path:
    return config_path().with_name(STATE_CONFIG)


def root() -> Path:
    """Return the deployment root recorded by ``beeloop setup``."""
    resolved = _configured_root()
    path = config_path()
    if not resolved.is_dir():
        raise ConfigError(
            f"{path}: configured root {resolved} is not a directory; "
            "run `beeloop setup --root PATH`"
        )
    return resolved.resolve()


def _configured_root() -> Path:
    path = config_path()
    data = _read(path)
    configured = data.get("root")
    if not isinstance(configured, str) or not configured:
        raise ConfigError(f"{path} must contain a non-empty string `root`")
    resolved = Path(configured)
    if not resolved.is_absolute():
        raise ConfigError(f"{path}: `root` must be an absolute path")
    return resolved.resolve()


def setup_root(given: Path | None = None) -> Path:
    """Configure and prepare the deployment root."""
    path = config_path()
    if given is None and path.exists():
        selected = _configured_root()
        should_write = False
    else:
        selected = (given or Path.cwd()).expanduser().resolve()
        should_write = True

    try:
        selected.mkdir(parents=True, exist_ok=True)
        if not selected.is_dir():
            raise NotADirectoryError(selected)
        for name in ROOT_DIRECTORIES:
            (selected / name).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"cannot prepare BeeLoop root {selected}: {exc}") from exc
    if should_write:
        try:
            _write(path, f'root = {_toml_string(str(selected))}\n')
        except OSError as exc:
            raise ConfigError(f"cannot write {path}: {exc}") from exc
    return selected


def setup_state_dir(given: Path | None = None) -> Path:
    """Configure and prepare the state directory."""
    path = state_config_path()
    if given is None and path.exists():
        selected = _configured_state_dir()
        should_write = False
    else:
        selected = (given or Path.home() / ".beeloop_states").expanduser().resolve()
        should_write = True

    try:
        selected.mkdir(parents=True, exist_ok=True)
        if not selected.is_dir():
            raise NotADirectoryError(selected)
    except OSError as exc:
        raise ConfigError(f"cannot prepare BeeLoop state directory {selected}: {exc}") from exc
    if should_write:
        try:
            _write(path, f'state_dir = {_toml_string(str(selected))}\n')
        except OSError as exc:
            raise ConfigError(f"cannot write {path}: {exc}") from exc
    return selected


def _configured_state_dir() -> Path:
    path = state_config_path()
    data = _read(path)
    configured = data.get("state_dir")
    if not isinstance(configured, str) or not configured:
        raise ConfigError(f"{path} must contain a non-empty string `state_dir`")
    resolved = Path(configured)
    if not resolved.is_absolute():
        raise ConfigError(f"{path}: `state_dir` must be an absolute path")
    return resolved.resolve()


def _read(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        raise ConfigError("BeeLoop is not configured; run `beeloop setup`") from None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc


def _write(path: Path, body: str) -> None:
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
        Path(handle.name).unlink(missing_ok=True)
        raise


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
