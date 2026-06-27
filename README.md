# BeeLoop

BeeLoop is a perpetually running loop, a gateway to a hive of agents. It
continuously listens for inputs, such as
cron events or Slack messages, and dispatches each event to the appropriate bee
agent.

BeeBot is the special full-access bee in that hive. It transforms the
Codex/Claude CLI into a personal assistant that remembers its past and grows
itself for your needs.

It is an event-triggered, stateful assistant like
[OpenClaw](https://github.com/openclaw/openclaw), but built with the same
minimalism as [SeedBot](https://github.com/RalphMao/seedbot).

BeeBot is your all-access autonomous assistant that:

- **Maintains continuity** across invocations through state files in `states/`.
- **Extends itself as needed** with new skills, tools, scoped bees, and
  scheduled follow-up inputs.
- **Delegates work as needed** to scoped bees such as `worker_bee` for focused
  workspace tasks, while owning the memory and context.
- **Coordinates overlapping inputs** so they cooperate, not collide.
- **Same Codex/Claude CLI, but stateful** — the same memory and continuity
  across every channel (CLI, Slack, cron).
- **Self-loops as needed, without telling** - uses whatever trigger is needed for the work.

Learn more about how BeeBot works in [AGENTS.md](AGENTS.md).

## Install

```bash
./install.sh
```

This performs simple local initialization and removes group/other access from
the repo tree, leaving access limited to this user.

## Run

Bee scripts run on the Codex CLI by default. In a bee request, add an
`engine=claude` header to route that bee to the Claude Code CLI instead.

### Codex (default)

BeeBot CLI for interactive uses (routed to Codex CLI):

```bash
./beebot
```

Start the `bee_loop` to listen for inputs (such as
[Slack](#building-slack-support), timer):

```bash
./bee_loop
```

Run the loop in the background with tmux/screen. Example:

```bash
tmux new -d -s bee_loop './bee_loop'
```

### Claude

```bash
BEEBOT_ENGINE=claude ./beebot
```

`bee_loop` passes request bodies on stdin; bee headers in that body are
parsed by the selected bee.

## Building Slack Support

This repo ships an English specification document for adding the private Slack
interface to BeeBot:
[`specs/slack_private.md`](specs/slack_private.md).

You can review it (optional) and ask BeeBot:

```text
Can you build this? specs/slack_private.md
```

## TODO

- `guest_bee` is currently a proof of concept. It is not an actively used
  feature yet. Manual invocation for testing:

  ```bash
  printf 'Public-safe prompt\n' | ./bees/guest_bee
  ```
