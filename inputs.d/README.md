# Input adapters

An input adapter is an executable that turns something happening in the world —
a chat message, a code review comment, a timer coming due — into one
**envelope** for the gateway.

`../gateway/loop` runs every executable in this directory once per tick
(`BEEBOT_INPUT_INTERVAL`, default 1s) and pipes each envelope with a non-empty
`msg` to the dispatcher.

Adapters are deployment state rather than source, so this directory is
gitignored apart from this file. Copy `example.sample` to start one.

## The envelope

```text
role=orchestrator
agent_id=
cwd=
instance=
source=review:example/project:123
msg=Pull request 123 got a new review comment.
See https://example.com/example/project/pull/123 for the thread.
```

Header lines are single-line `key=value`. **`msg=` is always last** — everything
after it, newlines included, is the body. There is no blank-line separator.

| Field | Meaning |
|---|---|
| `role` | a **role**, not an agent: a directory under `configs/roles/`. Creates a new agent. Used only when `agent_id` is absent. |
| `agent_id` | continue **this** conversation. |
| `cwd` | where the new agent works, overriding the role's own. Relative paths are under `BEEBOT_ROOT`. Optional, and only meaningful alongside `role`. |
| `instance` | which of several agents under one `(source, cwd, role)`. Omit it for the one default agent there; name it for a second, parallel agent. Scoped to that triple, not a global name — the same label under a different triple is an unrelated agent. |
| `source` | *who caused this*, never where it goes. Compared for equality, never parsed. |
| `msg` | prose: what happened and where to look. |

`agent_id` and `role` are never both meaningful — one continues, the other
creates. An envelope carrying both is rejected rather than resolved by
precedence, and so is one carrying `cwd` alongside `agent_id`: a cwd is fixed
when the agent is created. `instance` alongside `agent_id` is rejected too — an
`agent_id` names one exact agent, while an `instance` only selects which agent a
key resolves to.

## Rules

**Print nothing when idle.** Empty stdout is the whole idle signal.

**stdout is the envelope channel.** Diagnostics go to stderr or your own log
file. A stray line on stdout corrupts the envelope.

**Be executable to run.** The loop skips anything not `chmod +x`, which is how
`*.sample` files stay inert. Enabling an adapter is `chmod +x`.

**Own your source runtime.** On every poll, validate or restart the pieces your
source depends on — receivers, queues, tokens. Nothing else will do it for you.

**Say what happened and where to look, not what to do.** Pass links and ids; the
agent decides. Never raw binaries or agent-specific wire formats.

**No trailing newline is guaranteed.** The loop captures stdout with command
substitution, which strips them, and forwards the bytes verbatim.

**Re-emitting is free.** A polled source re-runs every tick, so a dropped
envelope comes back seconds later. That is why there is no queue directory and
no envelope files: durability is a property of polling, not of storage.

**A failing adapter is logged and skipped**, never fatal to the loop. Exiting
non-zero costs you this tick and nothing more.
