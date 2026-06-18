# BeeBot

BeeBot transforms Codex CLI into your personal assistant that remembers and
grows itself for your needs.

BeeBot is inspired by [OpenClaw](https://github.com/openclaw/openclaw) and
[SeedBot](https://github.com/RalphMao/seedbot).

## What's special about BeeBot

- Stateful like OpenClaw, but lean and agile.
- Built to be minimal like SeedBot
- Minimal code: a Bash wrapper and a loop; Codex handles state, orchestration,
  and concurrency.
- Extends capability with custom scoped agents, tools, skills, and helpers.

Learn more about BeeBot design and how it works in [AGENTS.md](AGENTS.md).

## Install

```bash
./install.sh
```

This performs simple local initialization and removes group/other access from
the repo tree, leaving access limited to this user.

## Run

Start the polling loop, runs in the background:

```bash
tmux new -d -s beebot './beebot_loop'
```

Use BeeBot CLI (routes to Codex CLI):

```bash
./beebot
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

- Add Claude support.
- `guest_bee` is currently a proof of concept. It is not tested or wired to any
  input yet. Manual invocation for testing:

  ```bash
  ./bees/guest_bee "Public-safe prompt"
  ```
