from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from beeloop.dispatch.envelope import parse as parse_envelope


SOURCE_ROOT = Path(__file__).parents[1] / "runtime" / "sources" / "slack-private"
sys.path.insert(0, str(SOURCE_ROOT))

from slack_private import cli as slack_cli  # noqa: E402
from slack_private import input as slack_input  # noqa: E402
from slack_private.common import (  # noqa: E402
    enqueue_authenticated_record,
    make_source,
    parse_slack_permalink,
    parse_source,
    pop_authenticated_record,
    require_known_source,
    sanitize_filename,
    validate_permalink_source,
    write_secrets,
)
from slack_private.input import format_envelope  # noqa: E402
from slack_private.listener import SlackIdentity, validate_events_api_payload  # noqa: E402

IDENTITY = SlackIdentity(team_id="T123", bot_user_id="UBOT")


def event_payload(**overrides):
    event = {
        "type": "message",
        "channel_type": "im",
        "team": "T123",
        "channel": "D123",
        "user": "UOWNER",
        "text": "run this",
        "ts": "1712345678.901234",
        "event_ts": "1712345678.901234",
    }
    event.update(overrides)
    return {"event_id": "Ev123", "team_id": "T123", "event": event}


def test_owner_dm_maps_each_thread_to_a_persistent_source():
    root = validate_events_api_payload(event_payload(), "UOWNER", IDENTITY)
    reply = validate_events_api_payload(
        event_payload(ts="1712345680.000001", thread_ts="1712345678.901234"),
        "UOWNER",
        IDENTITY,
    )
    next_root = validate_events_api_payload(
        event_payload(ts="1712345681.000001", event_ts="1712345681.000001"),
        "UOWNER",
        IDENTITY,
    )

    assert root is not None and reply is not None and next_root is not None
    assert root["source"] == reply["source"] == "slack-private:D123:1712345678901234"
    assert next_root["source"] != root["source"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"user": "UOTHER"},
        {"user": "UBOT"},
        {"bot_id": "B123"},
        {"channel_type": "channel"},
        {"subtype": "message_deleted"},
        {"subtype": "message_changed"},
        {"text": "", "files": []},
        {"team": "T999"},
    ],
)
def test_untrusted_or_unsupported_events_are_dropped(overrides):
    assert validate_events_api_payload(event_payload(**overrides), "UOWNER", IDENTITY) is None


def test_file_share_keeps_metadata_but_not_download_url():
    record = validate_events_api_payload(
        event_payload(
            subtype="file_share",
            text="",
            files=[
                {
                    "id": "F123",
                    "name": "diagram.png",
                    "url_private_download": "https://files.slack.com/private",
                    "mimetype": "image/png",
                    "size": 42,
                }
            ],
        ),
        "UOWNER",
        IDENTITY,
    )

    assert record is not None
    assert record["files"] == [{"id": "F123", "name": "diagram.png", "mimetype": "image/png", "size": 42}]


def test_envelope_uses_source_route_without_raw_message():
    record = {
        "source": "slack-private:D123:1712345678901234",
        "permalink": "https://workspace.slack.com/archives/D123/p1712345678901234",
    }
    parsed = parse_envelope(format_envelope(record))

    assert parsed.role == "orchestrator"
    assert parsed.instance == "default"
    assert parsed.source == record["source"]
    assert "run this" not in parsed.msg
    assert record["permalink"] in parsed.msg


def test_queue_deduplicates_and_authenticates_source(tmp_path: Path):
    record = {
        "event_id": "Ev123",
        "source": "slack-private:D123:1712345678901234",
        "permalink": "https://workspace.slack.com/archives/D123/p1712345678901234",
        "channel": "D123",
        "ts": "1712345678.901234",
        "thread_ts": "1712345678.901234",
        "files": [],
        "has_text": True,
    }

    assert enqueue_authenticated_record(tmp_path, record) is True
    assert enqueue_authenticated_record(tmp_path, record) is False
    assert require_known_source(tmp_path, record["source"]).thread_ts == record["thread_ts"]
    assert pop_authenticated_record(tmp_path) == record
    assert pop_authenticated_record(tmp_path) is None


def test_permalink_must_match_source():
    permalink = parse_slack_permalink(
        "https://workspace.slack.com/archives/D123/p1712345680000001?thread_ts=1712345678.901234"
    )
    validate_permalink_source(permalink, parse_source("slack-private:D123:1712345678901234"))

    with pytest.raises(RuntimeError, match="does not belong"):
        validate_permalink_source(permalink, parse_source("slack-private:D999:1712345678901234"))


def test_write_secrets_allowlists_keys_and_writes_private_file(tmp_path: Path):
    write_secrets(
        tmp_path,
        {
            "SLACK_APP_TOKEN": "xapp-test",
            "SLACK_BOT_TOKEN": "xoxb-test",
            "SLACK_ALLOWED_USER_ID": "UOWNER",
            "INTERNAL_TOKEN": "must-not-copy",
        },
    )
    target = tmp_path / "runtime" / "sources" / "slack-private" / "secrets.env"

    assert "INTERNAL_TOKEN" not in target.read_text(encoding="utf-8")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_reply_uses_authenticated_source(monkeypatch, tmp_path: Path):
    record = {
        "event_id": "Ev123",
        "source": make_source("D123", "1712345678.901234"),
        "permalink": "https://workspace.slack.com/archives/D123/p1712345678901234",
        "channel": "D123",
        "ts": "1712345678.901234",
        "thread_ts": "1712345678.901234",
        "files": [],
        "has_text": True,
    }
    enqueue_authenticated_record(tmp_path, record)

    class FakeClient:
        def chat_postMessage(self, **kwargs):
            assert kwargs == {"channel": "D123", "text": "done", "thread_ts": "1712345678.901234"}
            return {"channel": "D123", "ts": "1712345680.000001"}

    monkeypatch.setattr(slack_cli, "_require_config", lambda root: object())
    monkeypatch.setattr(slack_cli, "_web_client", lambda config: FakeClient())

    assert slack_cli.reply(tmp_path, record["source"], "done")["ok"] is True
    with pytest.raises(RuntimeError, match="has not been authenticated"):
        slack_cli.reply(tmp_path, make_source("D999", "1712345678.901234"), "no")


def test_download_does_not_send_token_to_non_slack_url(tmp_path: Path):
    class FakeClient:
        def files_info(self, file):
            assert file == "F123"
            return {"file": {"id": file, "name": "file.txt", "url_private_download": "https://example.invalid/file"}}

    downloaded = slack_cli._download_files(
        FakeClient(),
        "xoxb-secret",
        [{"files": [{"id": "F123"}]}],
        tmp_path,
    )

    assert downloaded == [{"id": "F123", "skipped": "invalid_private_download_url"}]


def test_thread_fetch_pages_until_target_message():
    class FakeClient:
        calls = 0

        def conversations_replies(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                assert "cursor" not in kwargs
                return {"messages": [{"ts": "1.000001"}], "response_metadata": {"next_cursor": "next"}}
            assert kwargs["cursor"] == "next"
            return {"messages": [{"ts": "2.000002"}], "response_metadata": {}}

    messages = slack_cli._fetch_replies_or_history(FakeClient(), "D123", "1.000001", "2.000002")

    assert [message["ts"] for message in messages] == ["1.000001", "2.000002"]


def test_listener_supervisor_starts_only_one_live_process(monkeypatch, tmp_path: Path):
    starts = []

    class FakeProcess:
        pid = 42

    def fake_popen(*args, **kwargs):
        starts.append((args, kwargs))
        return FakeProcess()

    monkeypatch.setattr(slack_input.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(slack_input, "_process_is_running", lambda pid: pid == 42)

    slack_input.ensure_listener_running(tmp_path)
    slack_input.ensure_listener_running(tmp_path)

    assert len(starts) == 1


def test_sanitize_filename():
    assert sanitize_filename("../../bad name?.png") == "bad_name_.png"
