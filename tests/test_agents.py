"""Rules about agents, records and locks that must not quietly stop holding."""

from __future__ import annotations

import ast
import json
import re
import threading
import time
import tomllib
import uuid
from pathlib import Path

import pytest

from beeloop import agents as ag
from beeloop.agents import records, roles, session_ttl
from beeloop.agents.backends import BackendError, InputItem, fake
from beeloop.config import config_path
from tests.conftest import SOURCE, as_fake, make_role, orchestrator, poke, worker


# ---------------------------------------------------------------------- filing


def test_root_rejects_a_path_that_is_not_a_directory(monkeypatch, tmp_path):
    config_path().write_text(f'root = "{tmp_path / "nope"}"\n', encoding="utf-8")
    with pytest.raises(ag.AgentError, match="not a directory"):
        records.root()


def test_resolving_an_agent_path_does_not_create_it():
    missing = "0198ff2a-0000-7000-8000-000000000000"

    path = records.agent_path(missing)

    assert path == records.root() / "runtime" / "agents" / missing
    assert not path.exists()


def test_agent_ids_are_version_7_and_ordered_by_time():
    """uuid7 orders by its millisecond prefix, not within a millisecond.

    Ids minted in the same millisecond are separated only by random bits, so the
    guarantee to assert is that the timestamp never goes backwards -- and that
    ids minted a millisecond apart sort.
    """
    minted = [records.new_agent_id() for _ in range(200)]
    for value in minted:
        parsed = uuid.UUID(value)
        assert parsed.version == 7
        assert (parsed.int >> 62) & 0b11 == 0b10  # RFC 9562 variant

    stamps = [uuid.UUID(value).int >> 80 for value in minted]
    assert stamps == sorted(stamps)

    spaced = []
    for _ in range(5):
        spaced.append(records.new_agent_id())
        time.sleep(0.002)
    assert spaced == sorted(spaced)


# ----------------------------------------------------------------------- roles


def test_an_empty_directory_is_a_working_role(beeloop_root):
    """Adding a role is a directory -- and it is allowed to be an empty one."""
    directory = beeloop_root / "configs" / "roles" / "empty"
    directory.mkdir()

    role = ag.load_role("empty")
    assert role.backend == roles.DEFAULT_BACKEND
    assert role.permissions == roles.DEFAULT_PERMISSIONS
    assert role.cwd is None
    assert role.options == {}


@pytest.mark.parametrize(
    ("name", "backend", "permissions", "cwd"),
    [
        ("orchestrator", "codex", "approve_for_me", "orchestrator"),
        ("worker", "claude_code", "approve_for_me", None),
        ("planner", "claude_code", "read", None),
    ],
)
def test_checked_in_roles_select_their_backend_and_permissions(
    name, backend, permissions, cwd
):
    role = ag.load_role(name)

    assert role.backend == backend
    assert role.permissions == permissions
    assert role.cwd == ((records.root() / cwd).resolve() if cwd else None)


def test_a_missing_role_names_the_directory_it_wanted():
    with pytest.raises(ag.UnknownRole, match="roles/nonesuch"):
        ag.load_role("nonesuch")


def test_backend_options_for_another_backend_are_not_visible(beeloop_root):
    role_dir = beeloop_root / "configs" / "roles" / "orchestrator"
    (role_dir / "role.toml").write_text(
        'backend = "fake"\n'
        "[backend_options.claude_code]\n"
        'model = "opus"\n'
        "[backend_options.fake]\n"
        'marker = "mine"\n',
        encoding="utf-8",
    )
    assert ag.load_role("orchestrator").options == {"marker": "mine"}


def test_backend_override_uses_that_backends_options(beeloop_root):
    make_role(
        beeloop_root,
        "configured",
        'backend = "fake"\ncwd = "workspaces/configured"\n'
        "[backend_options.codex]\n"
        'model = "gpt-test"\n'
        "[backend_options.fake]\n"
        'marker = "default"\n',
    )

    agent = ag.create("configured", backend="codex")

    assert agent.record["backend"] == "codex"
    assert agent.session().options == {"model": "gpt-test"}


def test_unknown_backend_is_rejected_before_an_agent_is_written():
    with pytest.raises(BackendError, match="unknown backend"):
        worker(backend="missing")

    assert not list(records.runtime("agents").glob("*/record.json"))


def test_editing_a_role_reaches_an_agent_that_already_exists(beeloop_root):
    """The role is late-bound, so a human edits files and the next turn sees it."""
    agent = as_fake(orchestrator())
    assert agent.session().permissions == "approve_for_me"

    toml = beeloop_root / "configs" / "roles" / "orchestrator" / "role.toml"
    toml.write_text('backend = "fake"\npermissions = "read"\n', encoding="utf-8")

    assert ag.restore(agent.agent_id).session().permissions == "read"


def test_a_broken_role_config_names_the_file_instead_of_leaking_a_parse_error(beeloop_root):
    """Every failure out of this module is an AgentError; a TOMLDecodeError
    escaping load_role would be the one exception to that."""
    toml = beeloop_root / "configs" / "roles" / "worker" / "role.toml"
    toml.write_text('backend = "unclosed\n', encoding="utf-8")

    with pytest.raises(ag.UnknownRole, match="role.toml is broken"):
        ag.load_role("worker")


def test_orchestrator_role_defines_its_lifecycle_flows(beeloop_root):
    role = ag.load_role("orchestrator")
    config = tomllib.loads((role.directory / "role.toml").read_text("utf-8"))

    assert role.cwd == (beeloop_root / "orchestrator").resolve()
    assert role.session_ttl == session_ttl.Policy(
        idle_seconds=3600, max_age_seconds=86400
    )
    assert role.heartbeat == "4h"
    assert role.prompts == {
        "session_init": (
            "This is your first turn. Use the first-turn flow."
        ),
        "session_expire": (
            "This session is expiring. Use the BeeLoop State save flow and return "
            "a concise handoff hint for continuing the work later."
        ),
        "session_resume": (
            "This is a new session for existing work. Use the BeeLoop State continue "
            "flow with the previous session handoff below."
        ),
        "heartbeat": "This is a heartbeat. Use the heartbeat flow.",
    }
    assert config["allowed_receivers"]["roles"] == ["*"]

    workspace = SOURCE / "orchestrator"
    canonical = workspace / ".agents" / "skills"
    instructions = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    first_turn = (canonical / "orchestrator-first-turn" / "SKILL.md").read_text()
    outside = (canonical / "outside-orchestrator" / "SKILL.md").read_text()
    assert "`event-driven-operation` skill" in instructions
    assert "`worker-delegation` skill" in instructions
    assert "Call `get_agent_id()` first" in first_turn
    assert "`outside-orchestrator` skill" in first_turn
    assert "On every user turn" in outside
    assert "simple question directly" in outside
    assert all(term in outside for term in ("coding", "planning", "job launching"))
    assert "primary in the BeeLoop harness" in outside
    assert all(
        term in outside
        for term in (
            "only after completion",
            "`agent_art/messages/<short-sender-descriptor>`",
            "primary's current working directory",
        )
    )
    assert (workspace / ".gitignore").read_text(encoding="utf-8") == (
        "/secrets.env\n/agent_art/\n/workspaces/\n"
    )
    for name in (
        "event-driven-operation",
        "orchestrator-first-turn",
        "orchestrator-heartbeat",
        "outside-orchestrator",
        "worker-delegation",
        "workspace-management",
    ):
        assert (canonical / name / "SKILL.md").is_file()
        claude_link = workspace / ".claude" / "skills" / name
        canonical_source = workspace / ".agents" / "skills" / name
        assert claude_link.is_symlink()
        assert claude_link.resolve() == canonical_source.resolve()


def test_role_prompts_are_an_open_string_registry(beeloop_root):
    make_role(
        beeloop_root,
        "prompted",
        "[prompts]\n"
        'session_init = "  keep this spacing  "\n'
        'custom_hook = "custom"\n'
        'disabled = "   "\n',
    )

    role = ag.load_role("prompted")

    assert role.prompt("session_init") == "  keep this spacing  "
    assert role.prompt("custom_hook") == "custom"
    assert role.prompt("disabled", "fallback") is None
    assert role.prompt("missing", "fallback") == "fallback"
    assert role.prompt("missing") is None


@pytest.mark.parametrize("config", ['prompts = "no"\n', "[prompts]\nbad = 1\n"])
def test_role_prompts_reject_non_tables_and_non_strings(beeloop_root, config):
    make_role(beeloop_root, "broken-prompts", config)

    with pytest.raises(ag.UnknownRole, match="prompts"):
        ag.load_role("broken-prompts")


# ------------------------------------------------------------- the workspace


def test_agent_creation_makes_a_missing_workspace(beeloop_root):
    make_role(beeloop_root, "bare", 'backend = "fake"\ncwd = "workspaces/bare"\n')
    agent = ag.create("bare")

    assert agent.cwd.is_dir()
    assert list(agent.cwd.iterdir()) == []


def test_a_cwd_passed_in_beats_the_one_the_role_names(beeloop_root):
    """The delegation decides where a worker works; the role only defaults it."""
    make_role(
        beeloop_root,
        "configured",
        'backend = "fake"\ncwd = "workspaces/configured"\n',
    )
    agent = ag.create("configured", cwd="workspaces/elsewhere")

    assert agent.cwd == (beeloop_root / "workspaces" / "elsewhere").resolve()
    assert list(agent.cwd.iterdir()) == []


def test_a_role_that_says_nowhere_to_work_is_refused_and_named(beeloop_root):
    """A role directory is configuration, not a place to work."""
    with pytest.raises(ag.AgentError, match="neither role 'worker'"):
        ag.create("worker")


# ---------------------------------------------------------------- the record


def test_an_agent_restored_from_a_record_matches_one_just_created():
    """If anything were carried in memory across a tick, this fails."""
    made = as_fake(orchestrator())
    woken = ag.restore(made.agent_id)

    assert woken.record == made.record
    assert woken.session() == made.session()


def test_creating_an_agent_writes_the_handle_before_anything_is_spent():
    agent = orchestrator()
    on_disk = json.loads(records.record_path(agent.agent_id).read_text("utf-8"))
    assert on_disk["status"] == records.PREPARED
    assert on_disk["session_id"]
    assert on_disk["turns"] == 0
    assert fake.turns(agent.agent_id) == []


def test_an_agent_owns_its_record_and_a_snapshot_of_its_role_config(beeloop_root):
    agent = orchestrator()
    directory = records.agent_path(agent.agent_id)
    role_config = beeloop_root / "configs" / "roles" / "orchestrator" / "role.toml"
    copied_config = role_config.read_text("utf-8")

    assert records.record_path(agent.agent_id) == directory / "record.json"
    assert records.queue_path(agent.agent_id) == directory / "queue.jsonl"
    assert (directory / "config.toml").read_text("utf-8") == copied_config

    role_config.write_text('permissions = "read"\n', encoding="utf-8")
    assert (directory / "config.toml").read_text("utf-8") == copied_config


def test_an_empty_role_creates_an_empty_agent_config(beeloop_root):
    directory = beeloop_root / "configs" / "roles" / "empty"
    directory.mkdir()
    agent = ag.create("empty", cwd="workspaces/empty")

    assert (records.agent_path(agent.agent_id) / "config.toml").read_text("utf-8") == ""


def test_update_rereads_so_a_write_during_a_turn_is_not_erased():
    """A tool writing into a live turn must not be clobbered by the turn.

    The turn holds an in-memory copy of the record from before the write; if it
    wrote that copy back instead of re-reading under the lock, the outside
    change would vanish.
    """
    agent = as_fake(orchestrator())
    outside = str(records.root())
    poke(agent, {"cwd": outside})

    agent.spin([InputItem("slack:D0B8:1", "hello")])

    assert records.read(agent.agent_id)["cwd"] == outside


# ------------------------------------------------------------------ the locks


def test_queue_and_locks_live_with_the_agent():
    agent = as_fake(orchestrator())
    held = records.claim(agent.agent_id, [])
    assert held is not None
    try:
        parked = ag.restore(agent.agent_id).spin([InputItem("late", "wait")])
        assert parked.parked is True

        directory = records.agent_path(agent.agent_id)
        assert {path.name for path in directory.iterdir()} == {
            "config.toml",
            "files.lock",
            "model.lock",
            "queue.jsonl",
            "record.json",
        }
        assert not (records.root() / "runtime" / "queue").exists()
        assert not (records.root() / "runtime" / "locks").exists()
        assert not (records.root() / "runtime" / "agents" / ".lock").exists()
    finally:
        held.release()


# ------------------------------------------------------------- spin and drain


def test_one_input_is_one_turn():
    agent = as_fake(orchestrator())
    delivery = agent.spin([InputItem("slack:D0B8:1", "hello")])
    init = agent.role.prompt("session_init")

    assert delivery.parked is False
    assert delivery.text == f"{init} | hello"
    assert fake.turns(agent.agent_id) == [
        [[session_ttl.INIT_SOURCE, init], ["slack:D0B8:1", "hello"]]
    ]
    assert records.read(agent.agent_id)["turns"] == 1


def test_session_init_is_a_separate_input_on_only_the_first_turn(beeloop_root):
    make_role(
        beeloop_root,
        "initialized",
        'backend = "fake"\ncwd = "workspaces/initialized"\n'
        "[prompts]\n"
        'session_init = "initialize"\n',
    )
    agent = ag.create("initialized")

    agent.spin([InputItem("user", "first")])
    agent.spin([InputItem("user", "second")])

    assert fake.turns(agent.agent_id) == [
        [[session_ttl.INIT_SOURCE, "initialize"], ["user", "first"]],
        [["user", "second"]],
    ]


def test_the_source_of_every_item_survives_batching():
    agent = as_fake(orchestrator())
    agent.spin(
        [
            InputItem("slack:D0B8:1", "first"),
            InputItem("checkpoint:abc", "second"),
        ]
    )
    assert fake.turns(agent.agent_id) == [
        [
            [session_ttl.INIT_SOURCE, agent.role.prompt("session_init")],
            ["slack:D0B8:1", "first"],
            ["checkpoint:abc", "second"],
        ]
    ]


def test_arrivals_during_a_turn_are_parked_and_drained_in_order():
    """Three messages sent mid-batch: parked, drained in order, one transcript."""
    agent = as_fake(orchestrator())
    held = records.claim(agent.agent_id, [])
    assert held is not None

    for index in range(3):
        parked = ag.restore(agent.agent_id).spin(
            [InputItem(f"slack:D0B8:{index}", f"message {index}")]
        )
        assert parked.parked is True

    assert fake.turns(agent.agent_id) == []  # nothing delivered while held
    held.release()

    agent.spin([InputItem("slack:D0B8:first", "the one that got the lock")])

    delivered = fake.turns(agent.agent_id)
    assert delivered[0] == [
        [session_ttl.INIT_SOURCE, agent.role.prompt("session_init")],
        ["slack:D0B8:first", "the one that got the lock"],
    ]
    assert delivered[1] == [
        ["slack:D0B8:0", "message 0"],
        ["slack:D0B8:1", "message 1"],
        ["slack:D0B8:2", "message 2"],
    ]
    assert records.read(agent.agent_id)["turns"] == 2


def test_a_parked_delivery_is_not_counted_as_a_turn():
    agent = as_fake(orchestrator())
    held = records.claim(agent.agent_id, [])
    ag.restore(agent.agent_id).spin([InputItem("slack:D0B8:1", "hi")])
    held.release()

    record = records.read(agent.agent_id)
    assert record["turns"] == 0
    assert record["last_turn"] is None


def test_an_arrival_cannot_slip_between_the_empty_check_and_the_release():
    """The one race the design exists to close.

    The holder finds the queue empty; an arrival parks in the instant before the
    lock is dropped. Without the files lock spanning both, nobody drains it.
    """
    agent = as_fake(orchestrator())
    reached = threading.Event()
    proceed = threading.Event()
    real_take = records._take_queue

    def slow_take(agent_id: str):
        batch = real_take(agent_id)
        if not batch:
            reached.set()
            proceed.wait(5)
        return batch

    def arrive():
        reached.wait(5)
        # Blocks on the files lock until the holder has released the turn.
        started = time.monotonic()
        ag.restore(agent.agent_id).spin([InputItem("late", "arrived")])
        arrive.took = time.monotonic() - started

    records._take_queue = slow_take
    try:
        thread = threading.Thread(target=arrive)
        thread.start()
        agent.spin([InputItem("slack:D0B8:1", "first")])
        proceed.set()
        thread.join(10)
    finally:
        records._take_queue = real_take

    sources = [source for turn in fake.turns(agent.agent_id) for source, _ in turn]
    assert sources == [session_ttl.INIT_SOURCE, "slack:D0B8:1", "late"], (
        "the late arrival was stranded"
    )


def test_a_closed_agent_refuses_to_spin():
    agent = as_fake(orchestrator())
    agent.close()
    with pytest.raises(ag.NotResumable):
        agent.spin([InputItem("slack:D0B8:1", "hello")])


def test_stats_accumulate_across_turns():
    agent = as_fake(orchestrator())
    agent.spin([InputItem("a", "one")])
    agent.spin([InputItem("b", "two")])

    record = records.read(agent.agent_id)
    assert record["turns"] == 2
    assert record["session_turns"] == 2
    assert record["created"] <= record["last_turn"]
    assert record["session_since"] == record["created"]


# ------------------------------------------------------------ the grep tests


def code_of(name: str) -> str:
    """The module with its docstrings and comments removed.

    Prose is allowed to say "subprocess"; code is not. Without this the test
    would be arguing with the documentation instead of the implementation.
    """
    source = (Path(ag.__file__).parent / name).read_text("utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))


def test_no_provider_leaks_into_the_contract():
    """If a flag or a provider name appears in this code, dispatch will end up
    branching on which backend it is talking to."""
    for name in ("agent.py", "records.py", "roles.py", "backends/base.py", "backends/fake.py"):
        source = code_of(name)
        assert not re.search(r'["\']--[a-z]', source), f"a CLI flag reached {name}"
        assert not re.search(r"\bsubprocess\b", source), f"subprocess reached {name}"
        assert "claude" not in source.lower(), f"a provider name reached {name}"


def test_delivery_mode_never_escapes_the_backends_package():
    caller = (Path(ag.__file__).parent / "agent.py").read_text("utf-8")
    assert "delivery_mode" not in caller
