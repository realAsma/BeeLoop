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
# A review comment landed on a pull request.
role=orchestrator
instance=default
source=review:example/project:123
msg=Pull request 123 got a new review comment.
See https://example.com/example/project/pull/123 for the thread.
```

Header lines are single-line `key=value`. **`msg=` is always last** — everything
after it, newlines included, is the body. There is no blank-line separator.

The table below is the whole field set: any other header key is refused rather
than ignored, so a misspelling stops the envelope instead of quietly routing it
somewhere plausible. Omit a field you do not use — writing it empty means the
same thing, but says less. A header line beginning `#` is a comment and may
contain `=`; past `msg=` there are no comments, only body.

| Field | Meaning |
|---|---|
| `role` | a **role**, not an agent: a directory under `configs/roles/`. Creates a new agent. Used only when `agent_id` is absent. |
| `agent_id` | continue **this** conversation. |
| `cwd` | where the new agent works, overriding the role's own. Relative paths are under `BEEBOT_ROOT`. Optional, and only meaningful alongside `role`. |
| `instance` | conversation persistence under one `(source, cwd, role)`. Missing, empty, or exact lowercase `fresh` always creates a new agent. Every other non-empty value, including `default`, reuses the latest restorable agent for that route. Names are scoped to that triple, not global. |
| `source` | The source session that caused the input and, when supported, the handle an agent uses to find its reply workflow. BeeLoop compares it for equality and never parses it. |
| `msg` | prose: what happened and where to look. |

`agent_id` and `role` are never both meaningful — one continues, the other
creates. An envelope carrying both is rejected rather than resolved by
precedence, and so is one carrying `cwd` alongside `agent_id`: a cwd is fixed
when the agent is created. `instance` alongside `agent_id` is rejected too — an
`agent_id` names one exact agent, while an `instance` only selects which agent a
key resolves to.

Runtime inputs name sources as `<input-name>:<session-key>`. The input name is
lowercase hyphenated and selects `runtime/inputs/<input-name>/`; the session key
is a stable identifier for the smallest external conversation that shares a
reply destination. For example, every message in one Slack thread uses the
same complete source, while a different thread uses a different source.
Source-specific workflows may interpret this convention, but the gateway and
router continue to treat the value as opaque.

`source` is only part of a route key. Reusing one agent for a source session
also requires the same `cwd` and `role` and a stable non-`fresh` `instance`.
Missing, empty, or `fresh` instances create a new agent for every event.

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

**Own replies to the source.** If an input supports replies, keep the reply
implementation under `runtime/inputs/<input-name>/`. An optional `SKILL.md` in
that directory can tell agents how to interpret the source and invoke the
helper. A model response is not delivered to the external source automatically.

**No trailing newline is guaranteed.** The loop captures stdout with command
substitution, which strips them, and forwards the bytes verbatim.

**Re-emitting is free.** A polled source re-runs every tick, so a dropped
envelope comes back seconds later. That is why there is no queue directory and
no envelope files: durability is a property of polling, not of storage.

**A failing adapter is logged and skipped**, never fatal to the loop. Exiting
non-zero costs you this tick and nothing more.
