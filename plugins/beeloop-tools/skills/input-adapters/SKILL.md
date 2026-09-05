---
name: input-adapters
description: Create or update a harness input adapter that turns external events into gateway envelopes. Use when adding an event source under inputs.d or changing how its events are reported or routed.
---

# Input adapters

Read `$BEEBOT_ROOT/inputs.d/README.md` before creating or updating an input; it
defines the envelope contract.

For each input:

- Put the substantive adapter and helper code in
  `$BEEBOT_ROOT/runtime/inputs/<input-name>/`.
- Create `$BEEBOT_ROOT/inputs.d/<input-name>` as a thin launcher that resolves
  `$BEEBOT_ROOT` and invokes the runtime implementation. Put no polling or
  envelope-building logic in the launcher.
- Apply every polling, stdout, envelope, routing, and dependency-ownership rule
  from `inputs.d/README.md` to the runtime implementation.

Keep draft launchers non-executable. Enable a finished input with
`chmod +x "$BEEBOT_ROOT/inputs.d/<input-name>"`.
