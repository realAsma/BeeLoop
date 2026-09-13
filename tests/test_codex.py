"""What the Codex adapter sends to the CLI and reads from JSONL."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from beeloop.agents.backends import BackendError, InputItem, Session, get
from beeloop.agents.backends.codex import CodexBackend, _argv, _render, _result


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
    monkeypatch.setenv("BEELOOP_CODEX_BIN", "/usr/bin/codex")


def test_codex_is_registered():
    assert isinstance(get("codex"), CodexBackend)


def test_a_prepared_session_starts_and_an_active_one_resumes():
    fresh = _argv(session(prepared=True), "hi")
    assert "resume" not in fresh

    warm = _argv(session(prepared=False), "hi")
    assert warm[warm.index("resume") + 1] == session().session_id


def test_cwd_is_the_process_directory_and_never_a_flag():
    argv = _argv(session(cwd=Path("/tmp/somewhere")), "hi")
    assert "/tmp/somewhere" not in argv
    assert "--cd" not in argv


def test_permission_profiles_compile_to_codex_flags():
    assert "--approve-for-me" in _argv(
        session(permissions="approve_for_me"), "hi"
    )

    read = _argv(session(permissions="read"), "hi")
    assert read[read.index("--sandbox") + 1] == "read-only"

    edit = _argv(session(permissions="edit"), "hi")
    assert edit[edit.index("--sandbox") + 1] == "workspace-write"

    assert "--dangerously-bypass-approvals-and-sandbox" in _argv(session(), "hi")


def test_an_unknown_permission_profile_is_refused_before_spending():
    with pytest.raises(BackendError, match="not in this backend's catalog"):
        _argv(session(permissions="root"), "hi")


def test_model_is_replayed_on_fresh_and_resumed_turns():
    for prepared in (True, False):
        argv = _argv(session(prepared=prepared, options={"model": "gpt-test"}), "hi")
        assert argv[argv.index("--model") + 1] == "gpt-test"


def test_deliver_binds_the_agent_and_adopts_codex_thread_id(monkeypatch):
    spawned = {}
    output = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "codex-thread"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "done"},
                }
            ),
            json.dumps({"type": "turn.completed", "usage": {}}),
        ]
    )

    def run(argv, **kwargs):
        spawned.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(subprocess, "run", run)
    current = session()
    delivery = CodexBackend().deliver(current, [InputItem("slack:D0B8:1", "hi")])

    assert delivery.text == "done"
    assert delivery.updates == {"session_id": "codex-thread", "status": "active"}
    assert spawned["env"]["BEELOOP_AGENT_DIR"] == str(current.agent_dir)
    assert spawned["env"]["PATH"] == os.environ["PATH"]
    assert spawned["cwd"] == str(current.cwd)


def test_nonzero_exit_preserves_stderr_and_jsonl_diagnostics(monkeypatch):
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            1,
            '{"type":"error","message":"Session not found: dead"}',
            "warning",
        )

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(BackendError, match="Session not found") as caught:
        CodexBackend().deliver(session(), [InputItem("source", "hi")])

    assert str(caught.value).startswith("warning\n")


def test_result_uses_the_last_completed_agent_message():
    output = "\n".join(
        [
            '{"type":"thread.started","thread_id":"thread"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"a"}}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"b"}}',
        ]
    )
    assert _result(output) == ("thread", "b")


def test_failed_event_is_an_error_even_when_the_process_exits_zero():
    output = "\n".join(
        [
            '{"type":"thread.started","thread_id":"thread"}',
            '{"type":"turn.failed","error":{"message":"request failed"}}',
        ]
    )
    with pytest.raises(BackendError, match="request failed"):
        _result(output)


def test_missing_thread_and_invalid_json_are_adapter_errors():
    with pytest.raises(BackendError, match="thread.started"):
        _result('{"type":"turn.completed"}')
    with pytest.raises(BackendError, match="invalid JSONL"):
        _result("not json")


def test_prompt_rendering_preserves_batch_boundaries_and_sources():
    rendered = _render(
        [InputItem('a"b', "first"), InputItem("checkpoint:abc", "second")]
    )
    assert rendered.startswith("2 inputs arrived, in the order they were received.")
    assert 'source=\'a"b\'' in rendered
    assert '<input source="checkpoint:abc">' in rendered


def test_an_empty_batch_is_a_caller_bug():
    with pytest.raises(BackendError, match="empty batch"):
        _render([])


@pytest.mark.parametrize(
    "message",
    [
        "Session not found: dead",
        "thread/resume: failed to load rollout: THREAD NOT FOUND: dead",
        "thread is archived",
        "session f2e0f791-0000-4000-8000-000000000000 is archived",
        "THREAD named-session IS ALREADY ARCHIVED",
        "No rollout found for thread id f2e0f791-0000-4000-8000-000000000000",
    ],
)
def test_unavailable_session_faults_are_terminal(message):
    backend = CodexBackend()
    assert backend.classify(message) == "terminal"


def test_other_fault_classification():
    backend = CodexBackend()
    assert backend.classify("input exceeds the context window") == "context_full"
    assert backend.classify("connection reset") == "transient"


def test_open_returns_a_fresh_placeholder():
    backend = CodexBackend()
    assert backend.open() != backend.open()


def test_close_does_not_archive_a_prepared_placeholder(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("archive called"),
    )

    CodexBackend().close(session(prepared=True))


def test_close_archives_a_delivered_thread(monkeypatch):
    spawned = {}

    def run(argv, **kwargs):
        spawned["argv"] = argv
        spawned.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    current = session(prepared=False)
    CodexBackend().close(current)

    assert spawned == {
        "argv": ["/usr/bin/codex", "archive", current.session_id],
        "capture_output": True,
        "text": True,
    }


@pytest.mark.parametrize(
    "message",
    [
        "Error: thread not found: dead",
        "Session f2e0f791-0000-4000-8000-000000000000 is archived",
        "THREAD named-session IS ALREADY ARCHIVED",
        "No rollout found for thread id f2e0f791-0000-4000-8000-000000000000",
    ],
)
def test_close_tolerates_an_absent_or_archived_thread(monkeypatch, message):
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", message)

    monkeypatch.setattr(subprocess, "run", run)
    CodexBackend().close(session(prepared=False))


def test_close_reports_archive_failures(monkeypatch):
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            1,
            "details",
            "archive failed: artifact already archived",
        )

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(BackendError, match="artifact already archived\ndetails"):
        CodexBackend().close(session(prepared=False))
