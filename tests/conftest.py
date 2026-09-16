"""Isolation: no test may see, or write to, the real runtime tree."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from beeloop import agents as ag
from beeloop.agents import records
from beeloop.config import config_path

# The source tree, for its checked-in configs and templates. It is not the
# configured root:
# the packages come from the install, and each test gets a root of its own below.
SOURCE = Path(__file__).resolve().parents[1]


def orchestrator(role: str = "orchestrator", **kwargs) -> ag.Agent:
    kwargs.setdefault("cwd", "workspaces/orchestrator")
    return ag.create(role, **kwargs)


def as_fake(agent: ag.Agent) -> ag.Agent:
    poke(agent, {"backend": "fake"})
    return ag.restore(agent.agent_id)


def worker(**kwargs) -> ag.Agent:
    kwargs.setdefault("cwd", "workspaces/worker")
    return ag.create("worker", **kwargs)


def make_role(
    home: Path, name: str, config: str = "", template: dict | None = None
) -> Path:
    directory = home / "configs" / "roles" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "role.toml").write_text(config, encoding="utf-8")
    for relative, body in (template or {}).items():
        path = home / "templates" / name / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return directory


def poke(agent: ag.Agent, fields: dict) -> dict:
    return records.update(agent.agent_id, agent.SCHEMA, fields)


@pytest.fixture(autouse=True)
def beeloop_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A whole BeeLoop tree per test.

    The roles are copied rather than pointed at, so a test that writes into a
    role directory cannot corrupt the checked-in one.
    """
    home = tmp_path / "root"
    user_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(user_home))
    shutil.copytree(SOURCE / "configs", home / "configs")
    shutil.copytree(SOURCE / "templates", home / "templates")
    # Made here rather than checked in: git cannot track an empty directory,
    # and empty is the whole point -- `worker` is the role that says nothing.
    (home / "configs" / "roles" / "worker").mkdir(exist_ok=True)
    (home / "runtime").mkdir()
    config = config_path()
    config.parent.mkdir(parents=True)
    config.write_text(f'root = "{home}"\n', encoding="utf-8")
    return home
