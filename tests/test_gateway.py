"""Gateway polling and CLI behavior."""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

from beeloop import cli, gateway


def _adapter(path: Path, body: str, *, executable: bool = True) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | 0o111)


def test_bare_command_runs_the_gateway(beeloop_root: Path, monkeypatch):
    selected = []
    monkeypatch.chdir(beeloop_root.parent)
    monkeypatch.setattr(gateway, "run", lambda root: selected.append(root) or 0)
    monkeypatch.setattr(cli, "run_gateway", gateway.run)

    assert cli.main([]) == 0
    assert selected == [beeloop_root.resolve()]


def test_help_explains_the_bare_command(capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--help"])

    assert stopped.value.code == 0
    assert "Run the BeeLoop gateway" in capsys.readouterr().out


def test_run_prepares_the_root_and_stops_cleanly(beeloop_root: Path, monkeypatch):
    polls = []

    def stop(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(gateway, "poll", polls.append)
    monkeypatch.setattr(gateway.time, "sleep", stop)

    assert gateway.run(beeloop_root) == 0
    assert polls == [beeloop_root]
    assert (beeloop_root / "inputs.d").is_dir()
    assert "started;" in (beeloop_root / "logs" / "loop.log").read_text()


def test_poll_filters_inputs_and_isolates_failures(beeloop_root: Path, monkeypatch):
    inputs = beeloop_root / "inputs.d"
    logs = beeloop_root / "logs"
    inputs.mkdir()
    logs.mkdir()
    _adapter(inputs / "a-valid", "printf 'unknown=value\\nmsg='; pwd")
    _adapter(inputs / "b-idle", ":")
    _adapter(inputs / "c-empty", "printf 'source=c\\nmsg=   '")
    _adapter(inputs / "d-fail", "echo broken >&2; exit 1")
    _adapter(inputs / "e-disabled", "printf 'source=e\\nmsg=disabled'", executable=False)
    _adapter(inputs / "z-valid", "printf 'source=z\\nmsg=last'")
    dispatched = []
    monkeypatch.setattr(
        gateway,
        "_dispatch",
        lambda envelope, root, log: dispatched.append((envelope, root, log)),
    )

    gateway.poll(beeloop_root)

    assert [item[0] for item in dispatched] == [
        b"unknown=value\nmsg=" + str(beeloop_root).encode(),
        b"source=z\nmsg=last",
    ]
    assert all(item[1] == beeloop_root for item in dispatched)
    assert "broken" in (logs / "inputs.log").read_text()
    loop_log = (logs / "loop.log").read_text()
    assert loop_log.index("input fired: a-valid") < loop_log.index(
        "input fired: z-valid"
    )
    assert "input failed: d-fail" in loop_log


def test_dispatch_uses_the_current_python(beeloop_root: Path, monkeypatch):
    written = io.BytesIO()

    class Input:
        def write(self, value):
            return written.write(value)

        def close(self):
            pass

    class Process:
        stdin = Input()

        def kill(self):
            raise AssertionError("dispatch should not be killed")

    calls = []

    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return Process()

    monkeypatch.setattr(subprocess, "Popen", popen)
    log = beeloop_root / "dispatch.log"

    gateway._dispatch(b"source=test\nmsg=hello", beeloop_root, log)

    assert written.getvalue() == b"source=test\nmsg=hello"
    assert calls[0][0] == [sys.executable, "-m", "beeloop.dispatch.dispatch"]
    assert calls[0][1]["cwd"] == beeloop_root
    assert calls[0][1]["start_new_session"] is True


@pytest.mark.parametrize("value", ["nope", "0", "-1", "nan", "inf"])
def test_interval_errors_are_actionable(value: str, monkeypatch, capsys):
    monkeypatch.setenv(gateway.INTERVAL_ENV, value)

    assert cli.main([]) == 2
    assert gateway.INTERVAL_ENV in capsys.readouterr().err
