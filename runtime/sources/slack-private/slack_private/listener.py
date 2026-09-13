from __future__ import annotations

import argparse
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import (
    beebot_root,
    enqueue_authenticated_record,
    event_was_seen,
    load_slack_config,
    make_source,
    metadata_for_file,
    validate_config,
)


ACCEPTED_SUBTYPES = {None, "file_share"}


@dataclass(frozen=True)
class SlackIdentity:
    team_id: str
    bot_user_id: str


def validate_events_api_payload(
    payload: dict[str, Any],
    allowed_user_id: str,
    identity: SlackIdentity,
) -> dict[str, Any] | None:
    event_id = payload.get("event_id")
    event = payload.get("event")
    if not event_id or not isinstance(event, dict):
        return None
    team_values = [value for value in (payload.get("team_id"), event.get("team")) if value]
    if not team_values or any(value != identity.team_id for value in team_values):
        return None
    if event.get("type") != "message" or event.get("channel_type") != "im":
        return None
    if event.get("user") != allowed_user_id:
        return None
    if event.get("user") == identity.bot_user_id or event.get("bot_id"):
        return None
    if event.get("subtype") not in ACCEPTED_SUBTYPES:
        return None

    files = [
        metadata_for_file(file_info)
        for file_info in event.get("files") or []
        if _supported_file(file_info)
    ]
    has_text = bool(str(event.get("text") or "").strip())
    if not has_text and not files:
        return None

    channel = event.get("channel")
    ts = event.get("ts")
    if not channel or not ts:
        return None
    thread_ts = event.get("thread_ts") or ts
    try:
        source = make_source(channel, thread_ts)
    except ValueError:
        return None
    return {
        "source": source,
        "channel": channel,
        "ts": ts,
        "thread_ts": thread_ts,
        "event_ts": event.get("event_ts") or ts,
        "event_id": event_id,
        "files": files,
        "has_text": has_text,
    }


def run_listener(root: Path) -> int:
    config = load_slack_config(root)
    try:
        validate_config(config)
    except RuntimeError as exc:
        _log(str(exc))
        return 0

    try:
        from slack_sdk import WebClient
        from slack_sdk.socket_mode import SocketModeClient
        from slack_sdk.socket_mode.response import SocketModeResponse
    except ImportError:
        _log("slack-sdk is not installed")
        return 0

    web_client = WebClient(token=config.bot_token)
    try:
        auth = web_client.auth_test()
    except Exception as exc:
        _log(f"auth.test failed: {exc}")
        return 0

    identity = SlackIdentity(
        team_id=str(auth.get("team_id") or ""),
        bot_user_id=str(auth.get("user_id") or ""),
    )
    if not identity.team_id or not identity.bot_user_id:
        _log("auth.test did not return team_id and user_id")
        return 0

    socket_client = SocketModeClient(app_token=config.app_token, web_client=web_client)

    def handle_request(client: Any, request: Any) -> None:
        try:
            client.send_socket_mode_response(SocketModeResponse(envelope_id=request.envelope_id))
        except Exception as exc:
            _log(f"Socket Mode ack failed: {exc}")
            return
        if request.type != "events_api":
            return
        record = validate_events_api_payload(request.payload, config.allowed_user_id, identity)
        if record is None or event_was_seen(root, record["event_id"]):
            return
        try:
            response = web_client.chat_getPermalink(channel=record["channel"], message_ts=record["ts"])
        except Exception as exc:
            _log(f"chat.getPermalink failed for event {record['event_id']}: {exc}")
            return
        if not (permalink := response.get("permalink")):
            _log(f"chat.getPermalink returned no permalink for event {record['event_id']}")
            return
        record["permalink"] = permalink
        if enqueue_authenticated_record(root, record):
            _log(f"queued authenticated Slack DM event {record['event_id']}")

    socket_client.socket_mode_request_listeners.append(handle_request)
    try:
        socket_client.connect()
    except Exception as exc:
        _log(f"Socket Mode connection failed: {exc}")
        return 0
    _log("listener connected")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        socket_client.disconnect()
    return 0


def _supported_file(file_info: Any) -> bool:
    return isinstance(file_info, dict) and bool(file_info.get("id")) and file_info.get("mode") != "tombstone"


def _log(message: str) -> None:
    print(f"slack-private listener: {message}", file=sys.stderr, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args()
    return run_listener((args.root or beebot_root()).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
