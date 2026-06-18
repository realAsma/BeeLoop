# BeeBot

BeeBot transforms Codex/Claude CLI into your personal assistant that remembers
and grows itself for your needs.

BeeBot is inspired by [OpenClaw](https://github.com/openclaw/openclaw) and
extended from [SeedBot](https://github.com/RalphMao/seedbot).

## What is BeeBot

BeeBot is an event-triggered, stateful assistant. Each trigger — a Slack
message, a cron tick, a CLI prompt — spins up a fresh BeeBot, but continuity is
never lost. On every run, BeeBot:

- **Receives triggers** from various input sources (Slack threads, cron jobs,
  etc.) via the adapters in `inputs.d/`.
- **Maintains continuity** across invocations through state files in `states/`
  (inputs, tasks, workspaces).
- **Orchestrates work** — delegating to scoped bees like `worker_bee` (workspace-scoped delegation) when appropriate.
- **Handles concurrency** so overlapping triggers coordinate instead of
  colliding.

BeeBot is the owner-facing, full-access bee; the other bees are scoped, less
privileged agents it orchestrates. See [AGENTS.md](AGENTS.md) for the full
operating contract.

## What's special about BeeBot

- **Minimal code**: just a Bash wrapper and a loop — the underlying agent CLI
  (Codex or Claude) handles state, orchestration, and concurrency.
- **Stateful yet lean**: OpenClaw's continuity with SeedBot's minimalism.
- **Self-extending**: grows its own scoped bees, skills, and tools as your needs
  evolve.

## Install

```bash
./install.sh
```

This performs simple local initialization and removes group/other access from
the repo tree, leaving access limited to this user.

## Run

BeeBot runs on the Codex CLI by default. Set `BEEBOT_ENGINE=claude` to route to
the Claude Code CLI instead; the selected engine is inherited by every bee.

### Codex (default)

BeeBot CLI for interactive uses:

```bash
./beebot
```

Start the polling loop to listen for inputs (such as [Slack](#building-slack-support), timer):

```bash
./beebot_loop
```

Run the loop in the background with tmux/screen. Example:

```bash
tmux new -d -s beebot_loop './beebot_loop'
```

### Claude

Prefix any of the above with `BEEBOT_ENGINE=claude`:

```bash
BEEBOT_ENGINE=claude ./beebot

# for the loop, keep it inside the command so the bees inherit it:
tmux new -d -s beebot_loop 'BEEBOT_ENGINE=claude ./beebot_loop'
```

## Building Slack Support

Slack intake is not built in yet. This repo ships a specification document for
adding the private Slack interface to BeeBot:
[`specs/slack_private.md`](specs/slack_private.md).

To build it, ask BeeBot:

```bash
./beebot 'Can you build this? specs/slack_private.md'
```

## TODO

- `guest_bee` is currently a proof of concept. It is not tested or wired to any
  input yet. Manual invocation for testing:

  ```bash
  ./bees/guest_bee "Public-safe prompt"
  ```
