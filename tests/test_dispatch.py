"""Envelope parsing and routing: everything the dispatcher does before it spends."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from beeloop import agents as ag
from beeloop.agents import records
from beeloop.agents.backends import BackendError
import beeloop.dispatch.dispatch as dsp
import beeloop.dispatch.envelope as env
import beeloop.dispatch.routes as rt
from tests.conftest import as_fake, poke


@pytest.fixture(autouse=True)
def routes(beeloop_root, monkeypatch):
    """Route into the per-test tree, not the checked-in one."""
    path = beeloop_root / "routes.jsonl"
    monkeypatch.setattr(rt, "routes_path", lambda: path)
    monkeypatch.setattr(rt, "DEFAULT_ROLE", "worker")
    return path


def agent_count() -> int:
    return len(list(records.runtime("agents").glob("*/record.json")))


# `worker` names no cwd, so every route-addressed envelope must.
WORKSPACE = "workspaces/worker"


def envelope(**fields) -> str:
    body = fields.pop("msg", "do the thing")
    headers = {
        "role": "worker",
        "agent_id": "",
        "cwd": WORKSPACE,
        "instance": "default",
        "backend": "",
        "source": "timer:t",
        **fields,
    }
    return "\n".join(f"{k}={v}" for k, v in headers.items()) + f"\nmsg={body}"


def key(**fields) -> rt.Route:
    """The routing key the default envelope resolves to, or a variation on it.

    Built through `Route.of` rather than by hand, so a test asking for
    `cwd=WORKSPACE` gets the same absolute string the dispatcher would compute
    -- a test that keyed on the relative form would pass while the real thing
    spawned a duplicate agent.
    """
    return rt.Route.of(env.parse(envelope(**fields)))


# ------------------------------------------------------------------- parsing


def test_msg_ends_the_headers_so_a_body_may_contain_anything():
    """A blank-line separator would make an empty first body line unrepresentable,
    and `key=value` parsing would eat a body line containing '='."""
    parsed = env.parse(envelope(msg="line one\n\nline three = with equals"))
    assert parsed.msg == "line one\n\nline three = with equals"
    assert parsed.source == "timer:t"


def test_a_missing_trailing_newline_parses():
    """The loop captures adapter stdout with command substitution, which strips
    trailing newlines, so nothing downstream may require one."""
    assert env.parse(envelope(msg="hi")).msg == "hi"


@pytest.mark.parametrize(
    "original",
    [
        env.Envelope(
            "worker", None, "timer:t", "line one\nline two", WORKSPACE,
            backend="codex",
        ),
        env.Envelope(None, "01a0-x", "agent:sender", "continue"),
    ],
)
def test_serialized_envelopes_round_trip(original):
    assert env.parse(env.serialize(original)) == original


def test_empty_headers_are_absent_not_present():
    """Adapters write `agent_id=` with nothing after it as a matter of course."""
    parsed = env.parse(envelope(agent_id=""))
    assert parsed.agent_id is None
    assert parsed.role == "worker"


def test_both_identifiers_is_refused_rather_than_resolved_by_precedence():
    with pytest.raises(env.EnvelopeError, match="both role="):
        env.parse(envelope(role="worker", agent_id="01a0-x"))


def test_a_cwd_alongside_an_agent_id_is_refused(routes):
    """A cwd is fixed when the agent is created, so naming one while continuing
    an existing conversation can only be a caller bug."""
    with pytest.raises(env.EnvelopeError, match="both cwd="):
        env.parse(envelope(role="", agent_id="01a0-x"))


def test_instance_and_agent_id_together_are_refused():
    """The two ways of addressing an agent, named at once. An `agent_id` is one
    exact agent; an `instance` only says which agent a key resolves to, so a
    caller holding both has already contradicted itself."""
    with pytest.raises(env.EnvelopeError, match="both instance="):
        env.parse(envelope(role="", cwd="", agent_id="01a0-x", instance="a"))


def test_backend_and_agent_id_together_are_refused():
    with pytest.raises(env.EnvelopeError, match="both backend="):
        env.parse(
            envelope(
                role="", cwd="", instance="", agent_id="01a0-x", backend="codex"
            )
        )


def test_an_unknown_field_is_refused_and_named():
    """The whole point of a closed field set: `roll=worker` used to be dropped in
    silence and routed to the default role, with nothing to read afterwards."""
    with pytest.raises(env.EnvelopeError, match="roll"):
        env.parse(envelope(roll="worker"))


def test_a_comment_header_is_ignored_even_when_it_contains_an_equals_sign():
    """The letterbox is hand-edited prose, and its comments talk about `msg=`.
    Without the `#` skip each one would land in the field set as junk."""
    parsed = env.parse("# talks about role=orchestrator\n" + envelope())
    assert parsed.role == "worker"


def test_a_comment_line_in_the_body_survives_verbatim():
    """The skip is a header rule. Past `msg=` nothing is inspected, or a body
    quoting a config file would come out with lines missing."""
    body = "look at:\n# not a comment here\ndone"
    assert env.parse(envelope(msg=body)).msg == body


def test_an_envelope_with_no_source_is_refused():
    with pytest.raises(env.EnvelopeError, match="source"):
        env.parse("role=worker\nmsg=hello")


def test_an_envelope_with_no_msg_line_is_refused():
    with pytest.raises(env.EnvelopeError, match="msg"):
        env.parse("role=worker\nsource=timer:t")


# ------------------------------------------------------------------- routing


def test_a_source_reaches_the_same_agent_after_a_restart(routes):
    """Nothing is held in memory: the second call is a cold process's view."""
    first = rt.agent_for(key())
    again = rt.agent_for(key())

    assert again.agent_id == first.agent_id
    assert len(routes.read_text().splitlines()) == 1


def test_the_envelopes_cwd_is_where_the_new_agent_works(routes):
    """spec 9: the cwd comes from the delegation, so it has to survive parsing
    and reach creation rather than being dropped between them."""
    parsed = env.parse(envelope(cwd="workspaces/delegated"))
    agent = rt.agent_for(rt.Route.of(parsed))

    assert agent.cwd == (records.root() / "workspaces" / "delegated").resolve()


def test_two_racing_ticks_on_one_unrouted_source_produce_one_agent(routes):
    """The only race in the system, and what replaced the lease."""
    seen: list[str] = []
    start = threading.Barrier(8)

    def tick():
        start.wait()
        seen.append(rt.agent_for(key(source="timer:contended")).agent_id)

    threads = [threading.Thread(target=tick) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert len(set(seen)) == 1, f"spawned {len(set(seen))} agents for one source"
    assert agent_count() == 1


def test_a_tombstone_unroutes(routes):
    first = rt.agent_for(key())
    key().remove()

    assert key().resolve() is None
    assert rt.agent_for(key()).agent_id != first.agent_id


def test_a_truncated_final_line_is_skipped_not_thrown(routes):
    first = rt.agent_for(key())
    with open(routes, "a") as handle:
        handle.write('{"source": "timer:t", "agent_i')

    assert key().resolve() == first.agent_id


def test_a_route_to_a_deleted_record_spawns_fresh_and_corrects_itself(routes):
    """A duplicated agent is a far better outcome than a dropped envelope."""
    first = rt.agent_for(key())
    records.record_path(first.agent_id).unlink()

    second = rt.agent_for(key())

    assert second.agent_id != first.agent_id
    assert key().resolve() == second.agent_id


def test_a_route_to_an_unrestorable_record_spawns_fresh_too(routes):
    """A missing record is not the only way a row stops meaning anything: an
    unregistered `type` leaves one pointing at a file nothing can instantiate.
    Both are no route, and neither may take the tick down with it."""
    first = rt.agent_for(key())
    poke(first, {"type": "NoSuchType"})

    assert rt.agent_for(key()).agent_id != first.agent_id


# --------------------------------------------------------------- the wide key


def test_one_source_under_two_roles_is_two_agents(routes):
    """`source` alone was the key, so the second of these was silently handed
    to the agent the first created -- an orchestrator answering as a worker."""
    worker = rt.agent_for(key(role="worker"))
    orchestrator = rt.agent_for(key(role="orchestrator"))

    assert orchestrator.agent_id != worker.agent_id
    assert key(role="worker").resolve() == worker.agent_id
    assert key(role="orchestrator").resolve() == orchestrator.agent_id


def test_one_source_in_two_workspaces_is_two_agents(routes):
    """What lets one timer drive a worker per repository without encoding the
    repository into `source`, which nothing downstream is allowed to parse."""
    here = rt.agent_for(key(cwd=WORKSPACE))
    there = rt.agent_for(key(cwd="workspaces/elsewhere"))

    assert there.agent_id != here.agent_id
    assert agent_count() == 2


def test_the_default_role_keys_the_same_as_naming_it(routes, monkeypatch):
    """The trap the whole design turns on: the key is the RESOLVED role and
    cwd. Keying on what arrived would make an envelope that says nothing and an
    envelope that spells out the same answer two keys -- and every such
    mismatch is a duplicate agent, months before anyone notices."""
    monkeypatch.setattr(rt, "DEFAULT_ROLE", "orchestrator")

    silent = key(role="", cwd="workspaces/orchestrator")
    spelled = key(role="orchestrator", cwd="workspaces/orchestrator")

    assert silent == spelled
    assert rt.agent_for(silent).agent_id == rt.agent_for(spelled).agent_id
    assert agent_count() == 1


def test_the_default_backend_keys_the_same_as_naming_it(routes):
    silent = key(backend="")
    spelled = key(backend="claude_code")

    assert silent == spelled
    assert rt.agent_for(silent).agent_id == rt.agent_for(spelled).agent_id
    assert agent_count() == 1


def test_backend_is_part_of_the_route_and_agent_identity(routes):
    claude = rt.agent_for(key(backend="claude_code"))
    codex = rt.agent_for(key(backend="codex"))

    assert claude.agent_id != codex.agent_id
    assert records.read(claude.agent_id)["backend"] == "claude_code"
    assert records.read(codex.agent_id)["backend"] == "codex"


def test_an_unknown_backend_is_refused_before_routing():
    with pytest.raises(BackendError, match="unknown backend"):
        key(backend="missing")


def test_the_keys_cwd_is_the_absolute_one_the_record_holds(routes):
    """Equal as plain strings, so a row can be checked against a record by eye
    and neither has to be resolved again to compare them."""
    agent = rt.agent_for(key())

    assert key().cwd == records.read(agent.agent_id)["cwd"]
    assert Path(key().cwd).is_absolute()


def test_a_row_from_before_the_key_widened_matches_nothing(routes):
    """It keys as (source, None, None, None). Starting a fresh agent is wrong and
    self-correcting; matching on `source` alone would mis-deliver instead."""
    with open(routes, "a") as handle:
        handle.write(json.dumps({"source": "timer:t", "agent_id": "01a0-stale"}) + "\n")

    assert key().resolve() is None


@pytest.mark.parametrize("include_null", [False, True], ids=["missing", "null"])
def test_legacy_routes_without_an_instance_are_ignored(routes, include_null):
    """Old implicit-default routes remain on disk but are no longer reachable."""
    route = key(instance="")
    row = {
        "source": route.source,
        "cwd": route.cwd,
        "role": route.role,
        "backend": route.backend,
        "agent_id": "01a0-preinstance",
    }
    if include_null:
        row["instance"] = None
    with open(routes, "a") as handle:
        handle.write(json.dumps(row) + "\n")

    assert route.resolve() is None


def test_an_accidental_fresh_route_row_is_ignored(routes):
    route = key(instance="fresh")
    route_row = route.row("01a0-fresh")
    routes.write_text(json.dumps(route_row) + "\n")

    assert route.resolve() is None


def test_default_and_other_named_instances_are_distinct(routes):
    """Two agents of one route differentiated only by their instance."""
    first = rt.agent_for(key(instance="default"))
    second = rt.agent_for(key(instance="other"))

    assert second.agent_id != first.agent_id
    assert rt.agent_for(key(instance="default")).agent_id == first.agent_id
    assert rt.agent_for(key(instance="other")).agent_id == second.agent_id
    assert agent_count() == 2


@pytest.mark.parametrize("instance", ["", "fresh"])
def test_ephemeral_instances_always_create_unrouted_agents(routes, instance):
    route = key(instance=instance)
    first = rt.agent_for(route)
    second = rt.agent_for(route)

    assert second.agent_id != first.agent_id
    assert "instance" not in records.read(first.agent_id)
    assert "instance" not in records.read(second.agent_id)
    assert route.resolve() is None
    route.remove()
    assert not routes.exists()


@pytest.mark.parametrize("instance", ["a", "Fresh"])
def test_an_instance_resumes_rather_than_recreating(routes, instance):
    """An instance names a slot, not a fresh start: the second envelope on one
    continues the conversation the first began."""
    first = rt.agent_for(key(instance=instance))

    assert rt.agent_for(key(instance=instance)).agent_id == first.agent_id
    assert agent_count() == 1


def test_the_record_carries_the_instance(routes):
    """Recorded like `role` and `cwd`, so a record says which agent it is
    without the table. Omitted when there is none -- which is what keeps every
    record written before the field existed valid against the schema."""
    named = rt.agent_for(key(instance="b"))
    ephemeral = rt.agent_for(key(instance="fresh"))

    assert records.read(named.agent_id)["instance"] == "b"
    assert "instance" not in records.read(ephemeral.agent_id)


def test_remove_unroutes_without_touching_the_agent(routes):
    """Ending a route is not deleting an agent. The record survives and stays
    reachable by id; only the key's answer is gone, so the next envelope on it
    starts a fresh conversation."""
    first = rt.agent_for(key())
    key().remove()

    assert key().resolve() is None
    assert records.read(first.agent_id)["agent_id"] == first.agent_id
    assert rt.agent_for(key()).agent_id != first.agent_id


def test_an_envelope_no_role_can_place_is_refused_at_the_key(routes):
    """`worker` names no cwd, so an envelope that names none either cannot be
    routed at all -- and says so before anything is created."""
    with pytest.raises(ag.AgentError, match="where to work"):
        key(role="worker", cwd="")


# ---------------------------------------------------------------- dispatching


def test_an_envelope_reaches_the_agent_its_source_is_routed_to(routes):
    agent = as_fake(rt.agent_for(key()))
    line = dsp.dispatch(env.parse(envelope(msg="write the time")))

    assert agent.agent_id in line
    assert records.read(agent.agent_id)["turns"] == 1


def test_a_second_envelope_continues_the_same_agent(routes):
    agent = as_fake(rt.agent_for(key()))
    dsp.dispatch(env.parse(envelope(msg="first")))
    dsp.dispatch(env.parse(envelope(msg="second")))

    assert agent_count() == 1
    assert records.read(agent.agent_id)["turns"] == 2


@pytest.mark.parametrize("instance", ["", "fresh"])
def test_ephemeral_dispatch_envelopes_create_a_new_agent_each_time(
    routes, beeloop_root, instance
):
    (beeloop_root / "configs" / "roles" / "worker" / "role.toml").write_text(
        'backend = "fake"\n', encoding="utf-8"
    )

    first = dsp.dispatch(env.parse(envelope(instance=instance, msg="first")))
    second = dsp.dispatch(env.parse(envelope(instance=instance, msg="second")))
    agent_ids = [line.split()[1] for line in (first, second)]

    assert len(set(agent_ids)) == 2
    assert all("instance" not in records.read(agent_id) for agent_id in agent_ids)
    assert not routes.exists()


def test_an_envelope_arriving_mid_turn_is_parked_not_dropped(routes):
    agent = as_fake(rt.agent_for(key()))
    held = records.claim(agent.agent_id, [])
    try:
        line = dsp.dispatch(env.parse(envelope(msg="arrives while busy")))
    finally:
        held.release()

    assert "parked" in line
    assert records.read(agent.agent_id)["turns"] == 0

    dsp.dispatch(env.parse(envelope(msg="the next tick")))
    assert records.read(agent.agent_id)["turns"] == 2, "the parked input was drained"


def test_an_envelope_naming_an_unknown_agent_fails_loudly(routes):
    with pytest.raises(ag.UnknownAgent):
        dsp.dispatch(env.parse("agent_id=01a04db8-0000-7000-8000-000000000000\n"
                               "source=timer:t\nmsg=hello"))
