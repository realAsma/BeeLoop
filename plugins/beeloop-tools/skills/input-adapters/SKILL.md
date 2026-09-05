---
name: input-adapters
description: Create or update an input adapter for the BeeLoop harness that turns external events into gateway envelopes. Use when adding an event source under inputs.d or changing how its events are reported or routed.
---

# Input adapters

Read `$BEEBOT_ROOT/inputs.d/README.md` before creating or updating an input; it
defines the envelope contract.

For each input:

- Choose a lowercase hyphenated `<input-name>`. Emit every source as
  `<input-name>:<session-key>`, where the session key is the smallest stable
  external conversation that shares a reply destination. Reuse the complete
  source for that conversation and use a different source for a different one;
  for example, all messages in one Slack thread share a source, while separate
  threads do not.
- Put the substantive adapter and helper code in
  `$BEEBOT_ROOT/runtime/inputs/<input-name>/`.
- Create `$BEEBOT_ROOT/inputs.d/<input-name>` as a thin launcher that resolves
  `$BEEBOT_ROOT` and invokes the runtime implementation. Put no polling or
  envelope-building logic in the launcher.
- Apply every polling, stdout, envelope, routing, and dependency-ownership rule
  from `inputs.d/README.md` to the runtime implementation.

`source` tells the agent which external session caused the input and provides
the handle needed to reply. It is also part of the router key. To reuse one
agent for a source session, keep `role` and `cwd` stable and use a stable
non-`fresh` `instance`, normally `default`. Omit `instance`, leave it empty, or
use `fresh` when each event should create a new agent.

If the source accepts replies, keep its reply implementation in the same
runtime input directory. Its interface is input-specific, but it must accept
the complete source and reply content, validate the source namespace, own
destination lookup and authentication, and report delivery failure clearly.

Consider adding `$BEEBOT_ROOT/runtime/inputs/<input-name>/SKILL.md` when the
agent needs source-specific workflow or reply guidance. Give it valid skill
frontmatter and document the source format and session boundary, what incoming
events mean, the exact reply-helper invocation, when to reply, and how to
handle delivery success or failure. The installed `input-source-workflows`
skill loads this file from the source prefix.

Keep draft launchers non-executable. Enable a finished input with
`chmod +x "$BEEBOT_ROOT/inputs.d/<input-name>"`.
