"""What the Claude adapter puts on a command line, and what it makes of the
answers. Nothing here spawns the CLI."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from beebot.agents.backends import BackendError, InputItem, Session
from beebot.agents.backends.claude import ClaudeBackend, _argv, _render


def session(**kwargs) -> Session:
    base = dict(
        agent_id="0198ff2a-0000-7000-8000-000000000000",
        agent_dir=Path(
            "/tmp/runtime/agents/0198ff2a-0000-7000-8000-000000000000"
        ),
        session_id="f2e0f791-0000-4000-8000-000000000000",
        prepared=True,
        cwd=Path("/tmp"),
        permissions="yolo",
        options={},
    )
    base.update(kwargs)
    return Session(**base)


@pytest.fixture(autouse=True)
def binary(monkeypatch):
    monkeypatch.setenv("BEEBOT_CLAUDE_BIN", "/usr/bin/claude")


# ------------------------------------------------------------------ the argv


def test_a_prepared_session_creates_and_an_active_one_resumes():
    fresh = _argv(session(prepared=True), "hi")
    assert "--session-id" in fresh and "--resume" not in fresh

    warm = _argv(session(prepared=False), "hi")
    assert "--resume" in warm and "--session-id" not in warm


def test_cwd_is_never_a_flag():
    """Verified: a session adopts the process cwd, so it is passed as the
    subprocess's directory and never inferred back from the provider."""
    argv = _argv(session(cwd=Path("/tmp/somewhere")), "hi")
    assert "/tmp/somewhere" not in argv
    assert "--add-dir" not in argv


def test_the_config_is_replayed_identically_on_every_call():
    """There is no `the first call carries it` branch, because configuration
    does not survive a resume."""
    first = _argv(session(prepared=True, options={"model": "opus"}), "hi")
    tenth = _argv(session(prepared=False, options={"model": "opus"}), "hi")

    assert first[first.index("--session-id") + 2 :] == tenth[
        tenth.index("--resume") + 2 :
    ]
    for flag in ("--permission-mode", "--model", "--output-format"):
        assert flag in first and flag in tenth


def test_the_plugin_is_installed_rather_than_passed_per_invocation():
    """`--plugin-dir` is documented as this-session-only and `--mcp-config` is
    additive; neither is needed once the plugin is installed, and
    `--strict-mcp-config` would disable the operator's own servers."""
    argv = _argv(session(), "hi")
    for flag in ("--plugin-dir", "--mcp-config", "--strict-mcp-config"):
        assert flag not in argv


def test_deliver_binds_the_child_to_this_agents_own_directory(monkeypatch):
    """The whole design rests on this: the CLI passes its environment to the
    stdio MCP servers it starts, so this is what the loop server binds to."""
    spawned = {}

    def run(argv, **kwargs):
        spawned.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, json.dumps({"session_id": "s"}), "")

    monkeypatch.setattr(subprocess, "run", run)
    current = session()
    ClaudeBackend().deliver(current, [InputItem("slack:D0B8:1", "hi")])

    assert spawned["env"]["BEEBOT_AGENT_DIR"] == str(current.agent_dir)
    assert current.agent_dir.name == current.agent_id
    # Inherited, not replaced: the child needs PATH, HOME and BEEBOT_ROOT.
    assert spawned["env"]["PATH"] == os.environ["PATH"]


def test_a_permission_profile_outside_the_catalog_is_refused_before_spending():
    with pytest.raises(BackendError, match="not in this backend's catalog"):
        _argv(session(permissions="root"), "hi")


@pytest.mark.parametrize(
    ("permissions", "mode"),
    [("approve_for_me", "auto"), ("read", "plan")],
)
def test_the_profile_compiles_to_the_tools_own_enum(permissions, mode):
    argv = _argv(session(permissions=permissions), "hi")
    assert argv[argv.index("--permission-mode") + 1] == mode


# ---------------------------------------------------------------- the prompt


def test_one_item_is_framed_but_not_counted():
    rendered = _render([InputItem("slack:D0B8:1", "hello")])
    assert rendered == '<input source="slack:D0B8:1">\nhello\n</input>'


def test_a_batch_announces_its_size_and_keeps_every_source():
    rendered = _render(
        [InputItem("slack:D0B8:1", "first"), InputItem("checkpoint:abc", "second")]
    )
    assert rendered.startswith("2 inputs arrived, in the order they were received.")
    assert rendered.index("slack:D0B8:1") < rendered.index("checkpoint:abc")
    assert '<input source="checkpoint:abc">' in rendered


def test_a_source_with_quotes_cannot_break_out_of_its_attribute():
    rendered = _render([InputItem('a"b', "x")])
    assert 'source=\'a"b\'' in rendered


def test_an_empty_batch_is_a_caller_bug():
    with pytest.raises(BackendError, match="empty batch"):
        _render([])


# ------------------------------------------------------------------ classify


def test_a_missing_session_is_terminal():
    message = (
        "No conversation found with session ID: "
        "15862deb-6f97-468b-bf2b-eef2c9d5942f"
    )
    assert ClaudeBackend().classify(message) == "terminal"


def test_anything_unrecognized_is_transient_not_terminal():
    """Calling an unknown failure terminal would throw away a live conversation."""
    assert ClaudeBackend().classify("Connection reset by peer") == "transient"
    assert ClaudeBackend().classify("") == "transient"


def test_open_spends_nothing_and_returns_a_fresh_id():
    backend = ClaudeBackend()
    assert backend.open() != backend.open()
