---
name: input-handle
description: Handle or reply to a BeeLoop input. Use when a non-agent input needs source-specific handling or an external reply.
---

# Handle an input

The `source` on an input is its external session and reply handle. BeeLoop does
not deliver your ordinary model response back to that source. Orchestrators
can call source helpers with `run_script(script=..., args=..., stdin=...)`,
using paths relative to `runtime/`. Check the returned `exit_code` before
reporting success. Follow the source instructions below.

For a source shaped as `<source-name>:<session-key>`:

1. Split once at the first `:` and require `<source-name>` to match
   `[a-z0-9][a-z0-9-]*`.
2. Call `get_beeloop_root()` and look for
   `<root>/runtime/sources/<source-name>/SKILL.md`.
3. If found, read it completely unless already read in this retained
   conversation. Follow it for replies and other source operations, passing
   the complete `source`.
4. If absent, handle the input normally; do not invent a reply mechanism.

For `agent:<agent-id>` sources, use the BeeLoop `messaging` skill instead.
