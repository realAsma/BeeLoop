# BeeBot

BeeBot transforms the Codex/Claude CLI into a personal assistant that remembers
its past and grows itself for your needs.

It is an event-triggered, stateful assistant like
[OpenClaw](https://github.com/openclaw/openclaw), but built with the same
minimalism as [SeedBot](https://github.com/RalphMao/seedbot).

BeeBot is part of a hive of agents, scoped for access or workspace.
`beebot_loop` continuously listens for non-empty input from scripts in
`inputs.d/`, such as cron events or Slack messages, and dispatches each event to
the appropriate bee agent.

BeeBot is the "Queen Bee" of this hive, your all-access autonomous assistant
that:

- **Maintains continuity** across invocations through state files in `states/`.
- **Self-extends capabilities**, by adding new skills, tools, and even new
  scoped bees.
- **Adds self-triggering events** to `inputs.d/`, such as adding a wake-up timer
  for follow-up.
- **Delegates work as needed** to scoped bees such as `worker_bee` for focused
  workspace tasks, while owning the memory and context.
- **Coordinates overlapping inputs** so they cooperate, not collide.
- **Same Codex/Claude CLI, but stateful** — the same memory and continuity
  across every channel (CLI, Slack, cron).

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

Start the `beebot_loop` to listen for inputs (such as
[Slack](#building-slack-support), timer):

```bash
./beebot_loop
```

Run the loop in the background with tmux/screen. Example:

```bash
tmux new -d -s beebot_loop './beebot_loop'
```

### Claude

```bash
BEEBOT_ENGINE=claude ./beebot
```

`beebot_loop` passes request bodies on stdin; bee headers in that body are
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
