"""Persistent BeeLoop configuration and setup."""

from __future__ import annotations

import os
import shutil
import tomllib
from pathlib import Path

import pytest

from beeloop import cli
from beeloop.config import ConfigError, ROOT_DIRECTORIES, config_path, root, setup_root


def test_setup_stores_an_absolute_root_and_prepares_the_tree(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)

    selected = setup_root(Path("deployment"))

    assert selected == (tmp_path / "deployment").resolve()
    assert tomllib.loads(config_path().read_text("utf-8")) == {
        "root": str(selected)
    }
    assert all((selected / name).is_dir() for name in ROOT_DIRECTORIES)
    assert root() == selected


def test_omitted_root_preserves_existing_configuration(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    first = setup_root(tmp_path / "first")
    setup_root(tmp_path / "second")

    assert setup_root() == (tmp_path / "second").resolve()
    assert root() != first


def test_omitted_root_recreates_the_configured_tree(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    selected = setup_root(tmp_path / "deployment")
    shutil.rmtree(selected)

    assert setup_root() == selected
    assert selected.is_dir()


def test_missing_and_malformed_configuration_are_actionable(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "missing-home"))
    try:
        root()
    except ConfigError as exc:
        assert "beeloop setup" in str(exc)
    else:
        raise AssertionError("missing configuration was accepted")

    config_path().parent.mkdir(parents=True)
    config_path().write_text("not = [valid", encoding="utf-8")
    try:
        root()
    except ConfigError as exc:
        assert str(config_path()) in str(exc)
    else:
        raise AssertionError("malformed configuration was accepted")


@pytest.mark.parametrize("body", ["other = 1\n", 'root = "relative"\n'])
def test_config_requires_an_absolute_root(tmp_path: Path, monkeypatch, body: str):
    monkeypatch.setenv("HOME", str(tmp_path / "invalid-home"))
    config_path().parent.mkdir(parents=True)
    config_path().write_text(body, encoding="utf-8")

    with pytest.raises(ConfigError, match="root"):
        root()


def test_full_setup_configures_loop_and_state(tmp_path: Path, monkeypatch):
    project = Path(__file__).resolve().parents[1]
    home = tmp_path / "home"
    deployment = tmp_path / "deployment"
    states = tmp_path / "states"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv(
        "PATH", f"{project / 'state_store'}{os.pathsep}{os.environ['PATH']}"
    )

    assert cli.main(
        ["setup", "--root", str(deployment), "--state-dir", str(states)]
    ) == 0

    assert root() == deployment.resolve()
    assert tomllib.loads(
        (home / ".config" / "beeloop" / "state.toml").read_text("utf-8")
    ) == {"state_dir": str(states.resolve())}
    assert (states / "schema.json").is_file()
    assert (states / "index.jsonl").is_file()


def test_full_setup_reports_a_missing_state_executable(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cli.shutil, "which", lambda _: None)

    assert cli.main(["setup", "--root", str(tmp_path / "root")]) == 2
    assert "beeloop-state" in capsys.readouterr().err
