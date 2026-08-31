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

import agents as ag
from agents import _agent  # the private queue helper this file patches
from agents.backends import InputItem, fake


def orchestrator(role: str = "orchestrator", **kwargs) -> ag.Agent:
    return ag.create(role, **kwargs)


def as_fake(agent: ag.Agent) -> ag.Agent:
    """Point an agent at the backend that talks to nothing."""
    poke(agent, {"backend": "fake"})
    return ag.restore(agent.agent_id)


def worker(**kwargs) -> ag.Agent:
    """An ordinary agent: owns no task, so it cannot be refreshed.

    The role directory is empty on purpose, so it names no cwd and every caller
    has to say where the agent works.
    """
    kwargs.setdefault("cwd", "workspaces/worker")
    return ag.create("worker", **kwargs)


def make_role(home: Path, name: str, config: str = "", template: dict | None = None) -> Path:
    """Write a role directory into the per-test tree."""
    directory = home / "configs" / "roles" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "role.toml").write_text(config, encoding="utf-8")
    for relative, body in (template or {}).items():
        path = directory / "template" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return directory


def poke(agent: ag.Agent, fields: dict) -> dict:
    """Write straight to a record, as an outside tool would."""
    return ag.update(agent.agent_id, agent.SCHEMA, fields)


# ---------------------------------------------------------------------- filing


def test_root_rejects_a_path_that_is_not_a_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("BEEBOT_ROOT", str(tmp_path / "nope"))
    with pytest.raises(ag.AgentError, match="BEEBOT_ROOT"):
        ag.root()


def test_agent_ids_are_version_7_and_ordered_by_time():
    """uuid7 orders by its millisecond prefix, not within a millisecond.

    Ids minted in the same millisecond are separated only by random bits, so the
    guarantee to assert is that the timestamp never goes backwards -- and that
    ids minted a millisecond apart sort.
    """
    minted = [ag.new_agent_id() for _ in range(200)]
    for value in minted:
        parsed = uuid.UUID(value)
        assert parsed.version == 7
        assert (parsed.int >> 62) & 0b11 == 0b10  # RFC 9562 variant

    stamps = [uuid.UUID(value).int >> 80 for value in minted]
    assert stamps == sorted(stamps)

    spaced = []
    for _ in range(5):
        spaced.append(ag.new_agent_id())
        time.sleep(0.002)
    assert spaced == sorted(spaced)


# ----------------------------------------------------------------------- roles


def test_an_empty_directory_is_a_working_role(beebot_root):
    """Adding a role is a directory -- and it is allowed to be an empty one."""
    assert list((beebot_root / "configs" / "roles" / "worker").iterdir()) == []

    role = ag.load_role("worker")
    assert role.backend == ag.DEFAULT_BACKEND
    assert role.permissions == ag.DEFAULT_PERMISSIONS
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
    on_disk = json.loads(ag.record_path(agent.agent_id).read_text("utf-8"))
    assert on_disk["status"] == ag.PREPARED
    assert on_disk["session_id"]
    assert on_disk["turns"] == 0
    assert fake.turns(agent.agent_id) == []


def test_update_rereads_so_a_write_during_a_turn_is_not_erased():
    """A tool writing into a live turn must not be clobbered by the turn.

    The turn holds an in-memory copy of the record from before the write; if it
    wrote that copy back instead of re-reading under the lock, the outside
    change would vanish.
    """
    agent = as_fake(orchestrator())
    outside = str(ag.root())
    poke(agent, {"cwd": outside})

    agent.spin([InputItem("slack:D0B8:1", "hello")])

    assert ag.read(agent.agent_id)["cwd"] == outside


def test_an_unknown_agent_names_the_file_it_looked_for():
    with pytest.raises(ag.UnknownAgent, match="runtime/agents"):
        ag.Agent("0198ff2a-0000-7000-8000-000000000000")


# ------------------------------------------------------------------ the locks


# ------------------------------------------------------------- spin and drain


def test_one_input_is_one_turn():
    agent = as_fake(orchestrator())
    delivery = agent.spin([InputItem("slack:D0B8:1", "hello")])

    assert delivery.parked is False
    assert delivery.text == "hello"
    assert fake.turns(agent.agent_id) == [[["slack:D0B8:1", "hello"]]]
    assert ag.read(agent.agent_id)["turns"] == 1


def test_the_source_of_every_item_survives_batching():
    agent = as_fake(orchestrator())
    agent.spin(
        [
            InputItem("slack:D0B8:1", "first"),
            InputItem("checkpoint:abc", "second"),
        ]
    )
    assert fake.turns(agent.agent_id) == [
        [["slack:D0B8:1", "first"], ["checkpoint:abc", "second"]]
    ]


def test_arrivals_during_a_turn_are_parked_and_drained_in_order():
    """Three messages sent mid-batch: parked, drained in order, one transcript."""
    agent = as_fake(orchestrator())
    held = ag.claim(agent.agent_id, [])
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
    assert delivered[0] == [["slack:D0B8:first", "the one that got the lock"]]
    assert delivered[1] == [
        ["slack:D0B8:0", "message 0"],
        ["slack:D0B8:1", "message 1"],
        ["slack:D0B8:2", "message 2"],
    ]
    assert ag.read(agent.agent_id)["turns"] == 2


def test_a_parked_delivery_is_not_counted_as_a_turn():
    agent = as_fake(orchestrator())
    held = ag.claim(agent.agent_id, [])
    ag.restore(agent.agent_id).spin([InputItem("slack:D0B8:1", "hi")])
    held.release()

    record = ag.read(agent.agent_id)
    assert record["turns"] == 0
    assert record["last_turn"] is None


def test_an_arrival_cannot_slip_between_the_empty_check_and_the_release():
    """The one race the design exists to close.

    The holder finds the queue empty; an arrival parks in the instant before the
    lock is dropped. Without the record lock spanning both, nobody drains it.
    """
    agent = as_fake(orchestrator())
    reached = threading.Event()
    proceed = threading.Event()
    real_take = _agent._take_queue

    def slow_take(agent_id: str):
        batch = real_take(agent_id)
        if not batch:
            reached.set()
            proceed.wait(5)
        return batch

    def arrive():
        reached.wait(5)
        # Blocks on the record lock until the holder has released the turn.
        started = time.monotonic()
        ag.restore(agent.agent_id).spin([InputItem("late", "arrived")])
        arrive.took = time.monotonic() - started

    _agent._take_queue = slow_take
    try:
        thread = threading.Thread(target=arrive)
        thread.start()
        agent.spin([InputItem("slack:D0B8:1", "first")])
        proceed.set()
        thread.join(10)
    finally:
        _agent._take_queue = real_take

    sources = [source for turn in fake.turns(agent.agent_id) for source, _ in turn]
    assert sources == ["slack:D0B8:1", "late"], "the late arrival was stranded"


def test_a_closed_agent_refuses_to_spin():
    agent = as_fake(orchestrator())
    agent.close()
    with pytest.raises(ag.NotResumable):
        agent.spin([InputItem("slack:D0B8:1", "hello")])


def test_stats_accumulate_across_turns():
    agent = as_fake(orchestrator())
    agent.spin([InputItem("a", "one")])
    agent.spin([InputItem("b", "two")])

    record = ag.read(agent.agent_id)
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
    for name in ("_agent.py", "backends/base.py", "backends/fake.py"):
        source = code_of(name)
        assert not re.search(r'["\']--[a-z]', source), f"a CLI flag reached {name}"
        assert not re.search(r"\bsubprocess\b", source), f"subprocess reached {name}"
        assert "claude" not in source.lower(), f"a provider name reached {name}"


def test_delivery_mode_never_escapes_the_backends_package():
    caller = (Path(ag.__file__).parent / "_agent.py").read_text("utf-8")
    assert "delivery_mode" not in caller
