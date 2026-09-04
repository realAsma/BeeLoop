---
name: input-adapters
description: Create or update a harness input adapter that turns external events into gateway envelopes. Use when adding an event source under inputs.d or changing how its events are reported or routed.
---

# Input adapters

An input adapter turns an external event into one envelope for an agent in the
harness.

Before editing an adapter, read `$BEEBOT_ROOT/inputs.d/README.md` when it is
available; it is the authoritative envelope contract for that deployment.

Create adapters under `$BEEBOT_ROOT/inputs.d/`. The gateway polls each adapter
once per tick. It must emit either no stdout when idle or one complete envelope
when an event is ready. Send diagnostics to stderr or a log, because any other
stdout corrupts the envelope.

Choose one addressing mode:

- To create or reuse by route, emit `role`, `source`, and optional `cwd` and
  `instance`. `role` names a directory under `configs/roles/`, not an agent.
  Missing, empty, or exact lowercase `fresh` creates a new agent each time; any
  other non-empty instance reuses the latest restorable agent for the
  `(source, cwd, role)` route.
- To continue an exact conversation, emit `agent_id` and `source`. Do not add
  `role`, `cwd`, or `instance` with `agent_id`.

Use only `role`, `agent_id`, `cwd`, `instance`, `source`, and `msg`. Omit unused
headers. Put `msg=` last: its value and every following line form the message
body. Make `source` identify who caused the event; it is an opaque equality key,
not a destination. Write the message as prose describing what happened and
where to inspect it, with useful IDs and links. Do not send raw binaries or
prescribe a response; the receiving agent decides what to do.

Poll the source directly on each invocation; it remains the durable state and
may surface an outstanding event again on a later tick. Do not build a queue of
envelope files. If the source needs a receiver, queue, credential, or similar
runtime, the adapter owns checking or restarting it. A poll failure exits
nonzero.

The loop runs only executable files. Keep drafts and samples non-executable;
enable a finished adapter with `chmod +x`.
