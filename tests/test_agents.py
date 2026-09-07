"""Rules about agents, records and locks that must not quietly stop holding."""

from __future__ import annotations

import ast
import json
import re
import threading
import time
import uuid
from pathlib import Path

import pytest

from beebot import agents as ag
from beebot.agents import records, roles, session_ttl
from beebot.agents.backends import InputItem, fake
from tests.conftest import as_fake, make_role, orchestrator, poke, worker


# ---------------------------------------------------------------------- filing


def test_root_rejects_a_path_that_is_not_a_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("BEEBOT_ROOT", str(tmp_path / "nope"))
    with pytest.raises(ag.AgentError, match="BEEBOT_ROOT"):
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


def test_an_empty_directory_is_a_working_role(beebot_root):
    """Adding a role is a directory -- and it is allowed to be an empty one."""
    assert list((beebot_root / "configs" / "roles" / "worker").iterdir()) == []

    role = ag.load_role("worker")
    assert role.backend == roles.DEFAULT_BACKEND
    assert role.permissions == roles.DEFAULT_PERMISSIONS
    assert role.cwd is None
    assert role.options == {}


def test_a_missing_role_names_the_directory_it_wanted():
    with pytest.raises(ag.UnknownRole, match="roles/nonesuch"):
        ag.load_role("nonesuch")


def test_backend_options_for_another_backend_are_not_visible(beebot_root):
    role_dir = beebot_root / "configs" / "roles" / "orchestrator"
    (role_dir / "role.toml").write_text(
        'backend = "fake"\n'
        "[backend_options.claude_code]\n"
        'model = "opus"\n'
        "[backend_options.fake]\n"
        'marker = "mine"\n',
        encoding="utf-8",
    )
    assert ag.load_role("orchestrator").options == {"marker": "mine"}


def test_editing_a_role_reaches_an_agent_that_already_exists(beebot_root):
    """The role is late-bound, so a human edits files and the next turn sees it."""
    agent = as_fake(orchestrator())
    assert agent.session().permissions == "yolo"

    toml = beebot_root / "configs" / "roles" / "orchestrator" / "role.toml"
    toml.write_text('backend = "fake"\npermissions = "read"\n', encoding="utf-8")

    assert ag.restore(agent.agent_id).session().permissions == "read"


def test_a_broken_role_config_names_the_file_instead_of_leaking_a_parse_error(beebot_root):
    """Every failure out of this module is an AgentError; a TOMLDecodeError
    escaping load_role would be the one exception to that."""
    toml = beebot_root / "configs" / "roles" / "worker" / "role.toml"
    toml.write_text('backend = "unclosed\n', encoding="utf-8")

    with pytest.raises(ag.UnknownRole, match="role.toml is broken"):
        ag.load_role("worker")


def test_orchestrator_role_defines_its_lifecycle_flows():
    role = ag.load_role("orchestrator")
    instructions = (role.template / "AGENTS.md").read_text("utf-8")

    assert role.session_ttl == session_ttl.Policy(
        idle_seconds=3600, max_age_seconds=86400
    )
    assert role.heartbeat == "4h"
    assert role.prompts == {
        "session_init": (
            "This is session initialization. Use the session initialization flow."
        ),
        "session_expire": (
            "This session is expiring. Use the BeeBot State save flow and return "
            "a concise handoff hint for continuing the work later."
        ),
        "session_resume": (
            "This is a new session for existing work. Use the BeeBot State continue "
            "flow with the previous session handoff below."
        ),
        "heartbeat": "This is a heartbeat. Use the heartbeat flow.",
    }
    assert "## Session initialization flow" in instructions
    assert "ordered inputs and workspace" in instructions
    assert "## Heartbeat flow" in instructions
    assert "Collect ready results" in instructions
    assert "cancel this heartbeat's bound timer" in instructions


def test_role_prompts_are_an_open_string_registry(beebot_root):
    make_role(
        beebot_root,
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
def test_role_prompts_reject_non_tables_and_non_strings(beebot_root, config):
    make_role(beebot_root, "broken-prompts", config)

    with pytest.raises(ag.UnknownRole, match="prompts"):
        ag.load_role("broken-prompts")


# --------------------------------------------------------------- the seeding

SEEDED = 'backend = "fake"\ncwd = "workspaces/seeded"\n'
TEMPLATE = {
    "AGENTS.md": "the standing instructions",
    "skills/logging/SKILL.md": "how to log",
    ".mcp.json": "{}",
}


def test_a_fresh_cwd_is_seeded_with_everything_in_the_template(beebot_root):
    """The gap this closes: an agent whose cwd is not its role directory never
    saw the role's instructions, so they had to ride in every envelope."""
    make_role(beebot_root, "seeded", SEEDED, TEMPLATE)
    agent = ag.create("seeded")

    assert (agent.cwd / "AGENTS.md").read_text("utf-8") == "the standing instructions"
    assert (agent.cwd / "skills" / "logging" / "SKILL.md").exists()
    assert (agent.cwd / ".mcp.json").exists(), "a dotfile was skipped"


def test_seeding_never_overwrites_a_file_that_is_already_there(beebot_root):
    """A human edits a live agent's instructions in place; the next agent
    created into that same cwd must not silently undo the edit."""
    make_role(beebot_root, "seeded", SEEDED, TEMPLATE)
    first = ag.create("seeded")
    (first.cwd / "AGENTS.md").write_text("edited by hand", encoding="utf-8")

    second = ag.create("seeded")

    assert second.cwd == first.cwd
    assert (second.cwd / "AGENTS.md").read_text("utf-8") == "edited by hand"


def test_the_role_config_never_reaches_a_workspace(beebot_root):
    """Only `template/` is copied, so this holds by construction rather than by
    a blocklist that the next config file would outgrow."""
    agent = ag.create("logger")

    assert (agent.cwd / "AGENTS.md").exists()
    assert not (agent.cwd / "role.toml").exists()


def test_a_role_with_no_template_seeds_nothing_and_does_not_fail(beebot_root):
    """Adding a role is a directory, and material in it is optional."""
    make_role(beebot_root, "bare", 'backend = "fake"\ncwd = "workspaces/bare"\n')
    agent = ag.create("bare")

    assert agent.cwd.is_dir()
    assert list(agent.cwd.iterdir()) == []


def test_a_cwd_passed_in_beats_the_one_the_role_names(beebot_root):
    """The delegation decides where a worker works; the role only defaults it."""
    make_role(beebot_root, "seeded", SEEDED, TEMPLATE)
    agent = ag.create("seeded", cwd="workspaces/elsewhere")

    assert agent.cwd == (beebot_root / "workspaces" / "elsewhere").resolve()
    assert (agent.cwd / "AGENTS.md").exists()


def test_a_role_that_says_nowhere_to_work_is_refused_and_named(beebot_root):
    """A role directory is not a place to work, so the old fallback to it would
    seed a role with its own template and let an agent write over its config."""
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


def test_an_agent_owns_its_record_and_a_snapshot_of_its_role_config(beebot_root):
    agent = orchestrator()
    directory = records.agent_path(agent.agent_id)
    role_config = beebot_root / "configs" / "roles" / "orchestrator" / "role.toml"
    copied_config = role_config.read_text("utf-8")

    assert records.record_path(agent.agent_id) == directory / "record.json"
    assert records.queue_path(agent.agent_id) == directory / "queue.jsonl"
    assert (directory / "config.toml").read_text("utf-8") == copied_config

    role_config.write_text('permissions = "read"\n', encoding="utf-8")
    assert (directory / "config.toml").read_text("utf-8") == copied_config


def test_an_empty_role_creates_an_empty_agent_config():
    agent = worker()

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


def test_session_init_is_a_separate_input_on_only_the_first_turn(beebot_root):
    make_role(
        beebot_root,
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
