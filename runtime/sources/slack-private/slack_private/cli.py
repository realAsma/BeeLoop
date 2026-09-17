from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

from .common import (
    SlackConfig,
    SlackPermalink,
    beeloop_root,
    compact_ts,
    downloads_dir,
    is_slack_download_url,
    load_slack_config,
    parse_slack_permalink,
    require_known_source,
    sanitize_filename,
    secrets_path,
    validate_config,
    validate_permalink_source,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Authenticated private Slack helpers for BeeLoop.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("setup", help="verify credentials and enable intake")

    fetch_parser = subparsers.add_parser("fetch", help="fetch an authenticated Slack event and thread")
    fetch_parser.add_argument("--source", required=True)
    fetch_parser.add_argument("--permalink", required=True)
    fetch_parser.add_argument("--download-files", action="store_true")
    fetch_parser.add_argument("--output-dir", type=Path)

    reply_parser = subparsers.add_parser("reply", help="reply to an authenticated Slack source")
    reply_parser.add_argument("--source", required=True)
    reply_parser.add_argument("--text", required=True, help="reply text, or '-' to read stdin")

    dm_thread_parser = subparsers.add_parser(
        "dm-thread", help="open a new DM thread with the configured owner and post a body into it"
    )
    dm_thread_parser.add_argument("--header", required=True, help="short top-level message")
    dm_thread_parser.add_argument("--text", required=True, help="thread body, or '-' to read stdin")

    upload_parser = subparsers.add_parser("upload", help="upload a local file to an authenticated Slack source")
    upload_parser.add_argument("--source", required=True)
    upload_parser.add_argument("--file", type=Path, required=True)
    upload_parser.add_argument("--caption", default="")
    upload_parser.add_argument("--alt-text", default="")

    args = parser.parse_args()
    root = beeloop_root()
    if args.command == "setup":
        result = setup(root)
    elif args.command == "fetch":
        result = fetch(root, args.source, args.permalink, args.download_files, args.output_dir)
    elif args.command == "reply":
        result = reply(root, args.source, _read_text_arg(args.text))
    elif args.command == "dm-thread":
        result = dm_thread(root, args.header, _read_text_arg(args.text))
    elif args.command == "upload":
        result = upload(root, args.source, args.file, args.caption, args.alt_text)
    else:
        raise AssertionError(args.command)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def setup(root: Path) -> dict[str, Any]:
    if importlib.util.find_spec("slack_sdk") is None:
        raise RuntimeError("slack-sdk is not installed; install BeeLoop with the slack extra")
    config = _require_config(root)
    client = _web_client(config)
    auth = client.auth_test()
    response = client.conversations_open(users=config.allowed_user_id)
    if not (dm_id := (response.get("channel") or {}).get("id")):
        raise RuntimeError("Slack did not return the configured owner's DM channel")
    _enable_input(root)
    return {
        "ok": True,
        "team_id": auth.get("team_id"),
        "bot_user_id": auth.get("user_id"),
        "dm_channel": dm_id,
        "secrets_file": str(secrets_path(root)),
    }


def fetch(
    root: Path,
    source_value: str,
    permalink_value: str,
    download_files: bool,
    output_dir: Path | None,
) -> dict[str, Any]:
    config = _require_config(root)
    source = require_known_source(root, source_value)
    permalink = parse_slack_permalink(permalink_value)
    validate_permalink_source(permalink, source)
    client = _web_client(config)
    context = _fetch_context(client, permalink)
    if context["target_message"].get("user") != config.allowed_user_id:
        raise RuntimeError("Refusing Slack message not sent by SLACK_ALLOWED_USER_ID")
    downloaded: list[dict[str, Any]] = []
    if download_files:
        target_dir = output_dir or downloads_dir(root) / f"{source.channel}_{compact_ts(permalink.ts)}"
        messages = [message for message in context["messages"] if message.get("user") == config.allowed_user_id]
        downloaded = _download_files(client, config.bot_token, messages, target_dir)
    return {
        "ok": True,
        "source": source_value,
        "permalink": permalink_value,
        "target_message": context["target_message"],
        "messages": context["messages"],
        "downloaded_files": downloaded,
    }


def reply(root: Path, source_value: str, text: str) -> dict[str, Any]:
    source = require_known_source(root, source_value)
    client = _web_client(_require_config(root))
    response = client.chat_postMessage(channel=source.channel, text=text, thread_ts=source.thread_ts)
    return {"ok": True, "channel": response.get("channel"), "ts": response.get("ts")}


def dm_thread(root: Path, header: str, text: str) -> dict[str, Any]:
    """Start a new owner DM thread: the header at top level, the body as its first reply.

    For scheduled work that the owner asked for but did not just message about,
    so there is no inbound source to reply to. The destination is not a
    parameter: it is the DM channel Slack opens for `SLACK_ALLOWED_USER_ID`.
    """
    config = _require_config(root)
    client = _web_client(config)
    response = client.conversations_open(users=config.allowed_user_id)
    if not (channel := (response.get("channel") or {}).get("id")):
        raise RuntimeError("Slack did not return the configured owner's DM channel")
    header_message = client.chat_postMessage(channel=channel, text=header)
    if not (thread_ts := header_message.get("ts")):
        raise RuntimeError("Slack did not return a timestamp for the thread header")
    body_message = client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts)
    return {"ok": True, "channel": channel, "thread_ts": thread_ts, "ts": body_message.get("ts")}


def upload(
    root: Path,
    source_value: str,
    file_path: Path,
    caption: str,
    alt_text: str,
) -> dict[str, Any]:
    if not file_path.is_file():
        raise FileNotFoundError(str(file_path))
    source = require_known_source(root, source_value)
    client = _web_client(_require_config(root))
    response = client.files_upload_v2(
        channel=source.channel,
        file=str(file_path),
        title=file_path.name,
        initial_comment=caption,
        thread_ts=source.thread_ts,
        alt_txt=alt_text or None,
    )
    return {"ok": True, "file": response.get("file", {})}


def _require_config(root: Path) -> SlackConfig:
    config = load_slack_config(root)
    validate_config(config)
    return config


def _web_client(config: SlackConfig) -> Any:
    try:
        from slack_sdk import WebClient
    except ImportError as exc:
        raise RuntimeError("slack-sdk is not installed; install BeeLoop with the slack extra") from exc
    return WebClient(token=config.bot_token)


def _enable_input(root: Path) -> None:
    adapter = root / "inputs.d" / "slack-private"
    adapter.chmod(adapter.stat().st_mode | 0o111)


def _fetch_context(client: Any, permalink: SlackPermalink) -> dict[str, Any]:
    thread_root_ts = permalink.thread_ts or permalink.ts
    messages = _fetch_replies_or_history(client, permalink.channel, thread_root_ts, permalink.ts)
    target = next((message for message in messages if message.get("ts") == permalink.ts), None)
    if target is None:
        raise RuntimeError("Could not find target Slack message in fetched context")
    return {"target_message": target, "messages": messages}


def _fetch_replies_or_history(client: Any, channel: str, thread_ts: str, target_ts: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    cursor: str | None = None
    try:
        for _ in range(50):
            response = client.conversations_replies(
                channel=channel,
                ts=thread_ts,
                inclusive=True,
                limit=200,
                **({"cursor": cursor} if cursor else {}),
            )
            messages.extend(response.get("messages") or [])
            if any(message.get("ts") == target_ts for message in messages):
                return messages
            cursor = (response.get("response_metadata") or {}).get("next_cursor") or None
            if not cursor:
                break
        if messages:
            return messages
    except Exception:
        pass
    response = client.conversations_history(channel=channel, latest=target_ts, inclusive=True, limit=1)
    return response.get("messages") or []


def _download_files(
    client: Any,
    bot_token: str,
    messages: list[dict[str, Any]],
    target_dir: Path,
) -> list[dict[str, Any]]:
    target_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for message in messages:
        for file_info in message.get("files") or []:
            file_id = file_info.get("id")
            if not file_id or file_id in seen:
                continue
            seen.add(file_id)
            details = client.files_info(file=file_id).get("file") or file_info
            url = details.get("url_private_download") or details.get("url_private")
            if not url or not is_slack_download_url(url):
                downloaded.append({"id": file_id, "skipped": "invalid_private_download_url"})
                continue
            name = sanitize_filename(details.get("name") or details.get("title") or f"{file_id}.bin")
            local_path = target_dir / f"{sanitize_filename(file_id)}_{name}"
            request = urllib.request.Request(url, headers={"Authorization": f"Bearer {bot_token}"})
            with urllib.request.urlopen(request) as response, local_path.open("wb") as output:
                output.write(response.read())
            downloaded.append({"id": file_id, "path": str(local_path), "name": details.get("name")})
    return downloaded


def _read_text_arg(value: str) -> str:
    return sys.stdin.read().rstrip("\n") if value == "-" else value


if __name__ == "__main__":
    raise SystemExit(main())
