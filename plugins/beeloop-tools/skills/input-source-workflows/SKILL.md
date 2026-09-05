---
name: input-source-workflows
description: Handle or reply to a BeeLoop input by loading the runtime workflow selected by its source prefix. Use when a non-agent input needs source-specific handling or an external reply.
---

# Input source workflows

The `source` on an input is its external session and reply handle. BeeLoop does
not deliver your ordinary model response back to that source.

For a source shaped as `<input-name>:<session-key>`:

1. Split once at the first `:` and require `<input-name>` to match
   `[a-z0-9][a-z0-9-]*`.
2. Look for
   `$BEEBOT_ROOT/runtime/inputs/<input-name>/SKILL.md`.
3. If it exists, read it completely before handling the input or replying. Use
   its helper exactly as documented and pass the complete source value.
4. If it does not exist, handle the input normally, but do not guess a reply
   mechanism or claim that your ordinary response reached the external source.

For `agent:<agent-id>` sources, use the BeeLoop `messaging` skill instead.
Never substitute a generic connector for a runtime workflow's reply helper.
