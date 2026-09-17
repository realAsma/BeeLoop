from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import shlex
import stat
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import parse_qs, urlparse

from beeloop.config import root as beeloop_root


SOURCE_NAME = "slack-private"
REQUIRED_ENV = ("SLACK_APP_TOKEN", "SLACK_BOT_TOKEN", "SLACK_ALLOWED_USER_ID")
SOURCE_RE = re.compile(rf"^{SOURCE_NAME}:(D[A-Z0-9]+):([0-9]+)$")
STATE_BASENAME = "state.json"
RUNTIME_BASENAME = "runtime.json"
LOCK_BASENAME = "state.lock"


@dataclass(frozen=True)
class SlackConfig:
    app_token: str
    bot_token: str
    allowed_user_id: str

    def missing(self) -> list[str]:
        values = {
            "SLACK_APP_TOKEN": self.app_token,
            "SLACK_BOT_TOKEN": self.bot_token,
            "SLACK_ALLOWED_USER_ID": self.allowed_user_id,
        }
        return [name for name in REQUIRED_ENV if not values[name]]


@dataclass(frozen=True)
class SlackPermalink:
    url: str
    channel: str
    ts: str
    thread_ts: str | None = None


@dataclass(frozen=True)
class SlackSource:
    value: str
    channel: str
    thread_ts: str


def source_root(root: Path) -> Path:
    return root / "runtime" / "sources" / SOURCE_NAME


def secrets_path(root: Path) -> Path:
    return source_root(root) / "secrets.env"


def state_dir(root: Path) -> Path:
    path = source_root(root) / "state"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir(root: Path) -> Path:
    path = source_root(root) / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def downloads_dir(root: Path) -> Path:
    path = source_root(root) / "downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_slack_config(root: Path) -> SlackConfig:
    values = read_env_file(secrets_path(root))
    return SlackConfig(
        app_token=values.get("SLACK_APP_TOKEN", ""),
        bot_token=values.get("SLACK_BOT_TOKEN", ""),
        allowed_user_id=values.get("SLACK_ALLOWED_USER_ID", ""),
    )


def validate_config(config: SlackConfig) -> None:
    if missing := config.missing():
        raise RuntimeError("Missing required values: " + ", ".join(missing))
    if not config.app_token.startswith("xapp-"):
        raise RuntimeError("SLACK_APP_TOKEN must be an xapp token")
    if not config.bot_token.startswith("xoxb-"):
        raise RuntimeError("SLACK_BOT_TOKEN must be an xoxb token")
    if not re.fullmatch(r"[UW][A-Z0-9]+", config.allowed_user_id):
        raise RuntimeError("SLACK_ALLOWED_USER_ID is not a Slack user ID")


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_assignment(line)
        if parsed is not None and parsed[0] in REQUIRED_ENV:
            values[parsed[0]] = parsed[1]
    return values


def make_source(channel: str, thread_ts: str) -> str:
    if not re.fullmatch(r"D[A-Z0-9]+", channel):
        raise ValueError("Slack source channel must be a DM channel")
    return f"{SOURCE_NAME}:{channel}:{compact_ts(thread_ts)}"


def parse_source(value: str) -> SlackSource:
    match = SOURCE_RE.fullmatch(value)
    if not match:
        raise ValueError(f"source must match {SOURCE_NAME}:<dm-channel>:<thread-ts>")
    return SlackSource(value=value, channel=match.group(1), thread_ts=unpack_ts(match.group(2)))


def parse_slack_permalink(url: str) -> SlackPermalink:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not _is_slack_host(parsed.hostname):
        raise ValueError("Slack permalink must use HTTPS on slack.com")
    parts = [part for part in parsed.path.split("/") if part]
    try:
        archive_index = parts.index("archives")
        channel = parts[archive_index + 1]
        packed_ts = parts[archive_index + 2]
    except (ValueError, IndexError) as exc:
        raise ValueError("Slack permalink must contain /archives/<channel>/p<timestamp>") from exc
    if not packed_ts.startswith("p"):
        raise ValueError("Slack permalink timestamp must start with p")
    thread_values = parse_qs(parsed.query).get("thread_ts") or []
    return SlackPermalink(
        url=url,
        channel=channel,
        ts=unpack_ts(packed_ts[1:]),
        thread_ts=thread_values[0] if thread_values else None,
    )


def validate_permalink_source(permalink: SlackPermalink, source: SlackSource) -> None:
    thread_ts = permalink.thread_ts or permalink.ts
    if permalink.channel != source.channel or thread_ts != source.thread_ts:
        raise RuntimeError("Slack permalink does not belong to the supplied source")


def compact_ts(value: str) -> str:
    return re.sub(r"\D", "", value)


def unpack_ts(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) <= 6:
        raise ValueError("Slack timestamp is too short")
    return f"{digits[:-6]}.{digits[-6:]}"


def sanitize_filename(value: str, fallback: str = "file") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned or fallback


def metadata_for_file(file_info: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "id",
        "created",
        "timestamp",
        "name",
        "title",
        "mimetype",
        "filetype",
        "pretty_type",
        "size",
        "mode",
        "is_external",
        "external_type",
    )
    return {field: file_info[field] for field in fields if field in file_info}


def enqueue_authenticated_record(root: Path, record: dict[str, Any]) -> bool:
    with state_lock(root):
        state = _load_state(root)
        event_id = record["event_id"]
        if event_id in state["seen_event_ids"]:
            return False
        state["queue"].append(record)
        state["seen_event_ids"] = [*state["seen_event_ids"], event_id][-2000:]
        state["sessions"][record["source"]] = {
            "channel": record["channel"],
            "thread_ts": record["thread_ts"],
        }
        _save_json(state_dir(root) / STATE_BASENAME, state)
        return True


def event_was_seen(root: Path, event_id: str) -> bool:
    with state_lock(root):
        return event_id in _load_state(root)["seen_event_ids"]


def pop_authenticated_record(root: Path) -> dict[str, Any] | None:
    with state_lock(root):
        state = _load_state(root)
        if not state["queue"]:
            return None
        record = state["queue"].pop(0)
        _save_json(state_dir(root) / STATE_BASENAME, state)
        return record


def require_known_source(root: Path, value: str) -> SlackSource:
    source = parse_source(value)
    with state_lock(root):
        session = _load_state(root)["sessions"].get(value)
    if session != {"channel": source.channel, "thread_ts": source.thread_ts}:
        raise RuntimeError("Slack source has not been authenticated by this input")
    return source


def load_runtime_state(root: Path) -> dict[str, Any]:
    path = state_dir(root) / RUNTIME_BASENAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_runtime_state(root: Path, data: Mapping[str, Any]) -> None:
    _save_json(state_dir(root) / RUNTIME_BASENAME, data)


@contextlib.contextmanager
def state_lock(root: Path) -> Iterator[None]:
    path = state_dir(root) / LOCK_BASENAME
    with path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def throttled_notice(root: Path, key: str, message: str, seconds: int = 300) -> None:
    now = time.time()
    with state_lock(root):
        state = load_runtime_state(root)
        notices = state.setdefault("notices", {})
        if now - float(notices.get(key, 0)) < seconds:
            return
        notices[key] = now
        save_runtime_state(root, state)
    print(message, file=sys.stderr, flush=True)


def is_slack_download_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and _is_slack_host(parsed.hostname)


def _load_state(root: Path) -> dict[str, Any]:
    path = state_dir(root) / STATE_BASENAME
    if not path.exists():
        return {"queue": [], "seen_event_ids": [], "sessions": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"queue": [], "seen_event_ids": [], "sessions": {}}
    data.setdefault("queue", [])
    data.setdefault("seen_event_ids", [])
    data.setdefault("sessions", {})
    return data


def _parse_assignment(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    name, separator, value = stripped.partition("=")
    if separator != "=" or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name.strip()):
        return None
    try:
        parsed = shlex.split(value, comments=False, posix=True)
    except ValueError:
        return None
    return name.strip(), parsed[0] if parsed else ""


def _is_slack_host(hostname: str | None) -> bool:
    return bool(hostname and (hostname == "slack.com" or hostname.endswith(".slack.com")))


def _save_json(path: Path, data: Mapping[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n", private=True)


def _atomic_write_text(path: Path, text: str, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)
    if private:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
