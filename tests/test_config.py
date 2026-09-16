"""BeeLoop deployment root resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from beeloop import cli, config
from beeloop.config import ConfigError, config_path, root


def _configure(directory: Path) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'root = "{directory}"\n', encoding="utf-8")


def test_missing_config_uses_checkout_from_any_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    config_path().unlink()

    selected = root()

    assert selected == Path(config.__file__).resolve().parents[2]
    assert (selected / "configs" / "roles" / "orchestrator").is_dir()
    adapter = selected / "inputs.d" / "beeloop-timers"
    assert adapter.is_file()
    assert adapter.stat().st_mode & 0o111


def test_configured_root_overrides_the_checkout(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    _configure(deployment)

    assert root() == deployment.resolve()


def test_malformed_configuration_is_actionable(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text("not = [valid", encoding="utf-8")

    with pytest.raises(ConfigError, match=str(config_path())):
        root()


@pytest.mark.parametrize("body", ["other = 1\n", 'root = "relative"\n'])
def test_config_requires_an_absolute_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(body, encoding="utf-8")

    with pytest.raises(ConfigError, match="root"):
        root()


def test_configured_root_must_exist(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    missing = tmp_path / "missing"
    _configure(missing)

    with pytest.raises(ConfigError, match="is not a directory"):
        root()


def test_root_command_prints_the_selected_root(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    _configure(deployment)

    assert cli.main(["root"]) == 0
    assert capsys.readouterr().out.strip() == str(deployment.resolve())
