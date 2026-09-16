"""Resolve the BeeLoop deployment root."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


class ConfigError(RuntimeError):
    pass


CONFIG_DIR = Path(".config") / "beeloop"
LOOP_CONFIG = "loop.toml"


def config_path() -> Path:
    try:
        return Path.home() / CONFIG_DIR / LOOP_CONFIG
    except RuntimeError as exc:
        raise ConfigError(f"cannot determine the BeeLoop config directory: {exc}") from exc


def root() -> Path:
    """Return the configured deployment root, or this checkout by default."""
    path = config_path()
    data = _read(path)
    resolved = _configured_root(path, data) if data is not None else _checkout_root()
    if not resolved.is_dir():
        raise ConfigError(f"{path}: configured root {resolved} is not a directory")
    return resolved.resolve()


def _checkout_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _configured_root(path: Path, data: dict[str, Any]) -> Path:
    configured = data.get("root")
    if not isinstance(configured, str) or not configured:
        raise ConfigError(f"{path} must contain a non-empty string `root`")
    resolved = Path(configured)
    if not resolved.is_absolute():
        raise ConfigError(f"{path}: `root` must be an absolute path")
    return resolved.resolve()


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return tomllib.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
