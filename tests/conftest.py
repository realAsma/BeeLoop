"""Isolation: no test may see, or write to, the real runtime tree."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def beebot_root(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """A whole BeeBot tree per test.

    The roles are copied rather than pointed at, so a test that writes into a
    role directory cannot corrupt the checked-in one.
    """
    home = Path(str(tmp_path))
    shutil.copytree(ROOT / "configs", home / "configs")
    # Made here rather than checked in: git cannot track an empty directory,
    # and empty is the whole point -- `worker` is the role that says nothing.
    (home / "configs" / "roles" / "worker").mkdir(exist_ok=True)
    (home / "runtime").mkdir()
    monkeypatch.setenv("BEEBOT_ROOT", str(home))
    return home
