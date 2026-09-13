from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

from .common import (
    beebot_root,
    load_runtime_state,
    load_slack_config,
    logs_dir,
    pop_authenticated_record,
    save_runtime_state,
    state_lock,
    throttled_notice,
    validate_config,
)


LISTENER_BACKOFF_SECONDS = 30


def main() -> int:
    root = beebot_root()
    config = load_slack_config(root)
    try:
        validate_config(config)
    except RuntimeError as exc:
        throttled_notice(root, "invalid_config", f"slack-private: {exc}; run the setup helper")
        return 0
    if importlib.util.find_spec("slack_sdk") is None:
        throttled_notice(root, "missing_slack_sdk", "slack-private: install the slack optional dependency")
        return 0
    ensure_listener_running(root)
    if record := pop_authenticated_record(root):
        print(format_envelope(record))
    return 0


def format_envelope(record: dict[str, object]) -> str:
    return "\n".join(
        [
            "role=orchestrator",
            "instance=default",
            f"source={record['source']}",
            "msg=Authenticated private Slack DM from the configured owner.",
            f"Message: {record['permalink']}",
            "Use the slack-private source skill to fetch the message and deliver every response.",
        ]
    )


def ensure_listener_running(root: Path) -> None:
    now = time.time()
    with state_lock(root):
        state = load_runtime_state(root)
        pid = int(state.get("listener_pid") or 0)
        if pid and _process_is_running(pid):
            return
        if now < float(state.get("next_listener_start_at") or 0):
            return
        env = os.environ.copy()
        source_path = str(root / "runtime" / "sources" / "slack-private")
        env["PYTHONPATH"] = source_path + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        log_path = logs_dir(root) / "listener.log"
        with log_path.open("a", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                [sys.executable, "-m", "slack_private.listener", "--root", str(root)],
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=log_file,
                close_fds=True,
                start_new_session=True,
                env=env,
            )
        state["listener_pid"] = process.pid
        state["next_listener_start_at"] = now + LISTENER_BACKOFF_SECONDS
        save_runtime_state(root, state)


def _process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


if __name__ == "__main__":
    raise SystemExit(main())
