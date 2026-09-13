from __future__ import annotations

import argparse
import getpass
import importlib.util
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

from .common import (
    REQUIRED_ENV,
    SlackConfig,
    SlackPermalink,
    beebot_root,
    compact_ts,
    downloads_dir,
    is_slack_download_url,
    load_slack_config,
    parse_slack_permalink,
    read_env_file,
    require_known_source,
    sanitize_filename,
    secrets_path,
    validate_config,
    validate_permalink_source,
    write_secrets,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Authenticated private Slack helpers for BeeBot.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="write credentials, verify access, and send a test DM")
    setup_parser.add_argument("--import-env-file", type=Path)
    setup_parser.add_argument("--non-interactive", action="store_true")
    setup_parser.add_argument("--no-send-hi", action="store_true")

    verify_parser = subparsers.add_parser("verify", help="verify credentials and allowed DM access")
    verify_parser.add_argument("--send-hi", action="store_true")

    fetch_parser = subparsers.add_parser("fetch", help="fetch an authenticated Slack event and thread")
    fetch_parser.add_argument("--source", required=True)
    fetch_parser.add_argument("--permalink", required=True)
    fetch_parser.add_argument("--download-files", action="store_true")
    fetch_parser.add_argument("--output-dir", type=Path)

    reply_parser = subparsers.add_parser("reply", help="reply to an authenticated Slack source")
    reply_parser.add_argument("--source", required=True)
    reply_parser.add_argument("--text", required=True, help="reply text, or '-' to read stdin")

    upload_parser = subparsers.add_parser("upload", help="upload a local file to an authenticated Slack source")
    upload_parser.add_argument("--source", required=True)
    upload_parser.add_argument("--file", type=Path, required=True)
    upload_parser.add_argument("--caption", default="")
    upload_parser.add_argument("--alt-text", default="")

    dm_parser = subparsers.add_parser("dm", help="send a proactive DM only to the configured owner")
    dm_parser.add_argument("--text", default="", help="DM text, or '-' to read stdin")
    dm_parser.add_argument("--file", type=Path)
    dm_parser.add_argument("--caption", default="")
    dm_parser.add_argument("--alt-text", default="")

    thread_parser = subparsers.add_parser("dm-thread", help="send a proactive DM header and threaded body")
    thread_parser.add_argument("--header", required=True)
    thread_parser.add_argument("--text", required=True, help="thread body, or '-' to read stdin")

    args = parser.parse_args()
    root = beebot_root()
    if args.command == "setup":
        result = setup(root, args.import_env_file, args.non_interactive, not args.no_send_hi)
    elif args.command == "verify":
        result = verify(root, args.send_hi)
    elif args.command == "fetch":
        result = fetch(root, args.source, args.permalink, args.download_files, args.output_dir)
    elif args.command == "reply":
        result = reply(root, args.source, _read_text_arg(args.text))
    elif args.command == "upload":
        result = upload(root, args.source, args.file, args.caption, args.alt_text)
    elif args.command == "dm":
        result = proactive_dm(root, _read_text_arg(args.text), args.file, args.caption, args.alt_text)
    elif args.command == "dm-thread":
        result = proactive_dm_thread(root, args.header, _read_text_arg(args.text))
    else:
        raise AssertionError(args.command)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def setup(root: Path, import_path: Path | None, non_interactive: bool, send_hi: bool) -> dict[str, Any]:
    candidates = read_env_file(import_path) if import_path else {}
    current = load_slack_config(root)
    values = {
        "SLACK_APP_TOKEN": current.app_token or candidates.get("SLACK_APP_TOKEN", ""),
        "SLACK_BOT_TOKEN": current.bot_token or candidates.get("SLACK_BOT_TOKEN", ""),
        "SLACK_ALLOWED_USER_ID": current.allowed_user_id or candidates.get("SLACK_ALLOWED_USER_ID", ""),
    }
    if not non_interactive:
        for name in REQUIRED_ENV:
            if not values[name]:
                values[name] = _prompt_secret(name)
    if importlib.util.find_spec("slack_sdk") is None:
        raise RuntimeError("slack-sdk is not installed; install BeeBot with the slack extra")
    preserved = write_secrets(root, values)
    return {**verify(root, send_hi), "secrets_file": str(secrets_path(root)), "preserved": preserved}


def verify(root: Path, send_hi: bool = False) -> dict[str, Any]:
    config = _require_config(root)
    client = _web_client(config)
    auth = client.auth_test()
    dm_id = _open_allowed_dm(client, config.allowed_user_id)
    if send_hi:
        client.chat_postMessage(
            channel=dm_id,
            text="Hi from BeeBot. Send me a DM to test this private Slack path while the BeeLoop gateway is running.",
        )
    return {
        "ok": True,
        "team_id": auth.get("team_id"),
        "bot_user_id": auth.get("user_id"),
        "dm_channel": dm_id,
        "sent_hi": send_hi,
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
    _assert_target_message_from_allowed_user(context, config.allowed_user_id)
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


def proactive_dm(
    root: Path,
    text: str,
    file_path: Path | None,
    caption: str,
    alt_text: str,
) -> dict[str, Any]:
    if not text and file_path is None:
        raise ValueError("dm requires text or a file")
    config = _require_config(root)
    client = _web_client(config)
    dm_id = _open_allowed_dm(client, config.allowed_user_id)
    result: dict[str, Any] = {"ok": True, "channel": dm_id}
    if text:
        result["message_ts"] = client.chat_postMessage(channel=dm_id, text=text).get("ts")
    if file_path is not None:
        if not file_path.is_file():
            raise FileNotFoundError(str(file_path))
        response = client.files_upload_v2(
            channel=dm_id,
            file=str(file_path),
            title=file_path.name,
            initial_comment=caption,
            alt_txt=alt_text or None,
        )
        result["file"] = response.get("file", {})
    return result


def proactive_dm_thread(root: Path, header: str, text: str) -> dict[str, Any]:
    config = _require_config(root)
    client = _web_client(config)
    dm_id = _open_allowed_dm(client, config.allowed_user_id)
    header_response = client.chat_postMessage(channel=dm_id, text=header)
    header_ts = header_response.get("ts")
    body_response = client.chat_postMessage(channel=dm_id, text=text, thread_ts=header_ts)
    return {
        "ok": True,
        "channel": dm_id,
        "message_ts": header_ts,
        "thread_message_ts": body_response.get("ts"),
    }


def _require_config(root: Path) -> SlackConfig:
    config = load_slack_config(root)
    validate_config(config)
    return config


def _web_client(config: SlackConfig) -> Any:
    try:
        from slack_sdk import WebClient
    except ImportError as exc:
        raise RuntimeError("slack-sdk is not installed; install BeeBot with the slack extra") from exc
    return WebClient(token=config.bot_token)


def _open_allowed_dm(client: Any, allowed_user_id: str) -> str:
    response = client.conversations_open(users=allowed_user_id)
    if not (dm_id := (response.get("channel") or {}).get("id")):
        raise RuntimeError("Slack did not return the configured owner's DM channel")
    return dm_id


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


def _assert_target_message_from_allowed_user(context: dict[str, Any], allowed_user_id: str) -> None:
    if context["target_message"].get("user") != allowed_user_id:
        raise RuntimeError("Refusing Slack message not sent by SLACK_ALLOWED_USER_ID")


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


def _prompt_secret(name: str) -> str:
    if "TOKEN" in name:
        return getpass.getpass(f"{name}: ").strip()
    return input(f"{name}: ").strip()


def _read_text_arg(value: str) -> str:
    return sys.stdin.read().rstrip("\n") if value == "-" else value


if __name__ == "__main__":
    raise SystemExit(main())
