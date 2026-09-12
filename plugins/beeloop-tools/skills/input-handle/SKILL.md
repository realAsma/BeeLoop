---
name: input-handle
description: Handle or reply to a BeeLoop input. Use when a non-agent input needs source-specific handling or an external reply.
---

# Handle an input

The `source` on an input is its external session and reply handle. BeeLoop does
not deliver your ordinary model response back to that source.

For a source shaped as `<source-name>:<session-key>`:

1. Split once at the first `:` and require `<source-name>` to match
   `[a-z0-9][a-z0-9-]*`.
2. Look for
   `$BEEBOT_ROOT/runtime/sources/<source-name>/SKILL.md`.
3. If found, read it completely unless already read in this retained
   conversation. Follow it and pass the complete `source` to its documented
   reply helper.
4. If absent, handle the input normally; do not invent a reply mechanism.

For `agent:<agent-id>` sources, use the BeeLoop `messaging` skill instead.
