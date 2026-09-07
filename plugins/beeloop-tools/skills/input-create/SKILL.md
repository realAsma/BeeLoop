---
name: input-create
description: Create or update a BeeLoop input that turns external events into gateway envelopes. Use when adding an event source under inputs.d or changing how its events are reported, routed, or replied to.
---

# Create an input

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

If the source accepts replies, keep its reply implementation in the same
runtime input directory. Its interface is input-specific, but it must accept
the complete source and reply content, validate the source namespace, own
destination lookup and authentication, and report delivery failure clearly.

Consider adding `$BEEBOT_ROOT/runtime/inputs/<input-name>/SKILL.md` when the
agent needs source-specific handling or reply guidance. The adapter must emit
sources as `<input-name>:<session-key>` so the installed `input-handle` skill
can load this file from the source prefix. Give the runtime skill valid
frontmatter and document the session-key format, what incoming events mean,
the exact reply-helper invocation, when to reply, and how to handle delivery
success or failure.

Keep draft launchers non-executable. Enable a finished input with
`chmod +x "$BEEBOT_ROOT/inputs.d/<input-name>"`.
