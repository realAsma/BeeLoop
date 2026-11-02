# BeeLoop

BeeLoop keeps ongoing work coordinated across conversations. It remembers
progress, delegates work to agents, and responds through the tools you already
use.

## Setup

```sh
pip install -e .
```

### Codex Setup

```sh
codex plugin marketplace add git@github.com:realAsma/BeeLoop-State.git
codex plugin add beeloop-state@beeloop-state
codex plugin marketplace add .
codex plugin add beeloop-tools@beeloop-tools
```

### Claude Setup

```sh
claude plugin marketplace add git@github.com:realAsma/BeeLoop-State.git
claude plugin install beeloop-state@beeloop-state
claude plugin marketplace add ./
claude plugin install beeloop-tools@beeloop-tools
```

## Getting Started

Start BeeLoop in a detached tmux session using the Python environment where
BeeLoop and its source dependencies are installed:

```sh
tmux new-session -d -s beeloop beeloop
```

### Connect a Slack App

For the best experience, use Slack as the main communication channel. Follow the
[private Slack input setup](runtime/sources/slack-private/README.md).

Then record it as the primary communication channel:

```sh
echo 'The primary communication channel with the user is the configured Slack app in runtime/sources/slack-private.' >> orchestrator/MEMORY.md
```

## Using BeeLoop

Ask for work in Slack, then follow up in the conversation to change direction
or check progress. Agents have memorable names such as `calm-otter`.

For example, request a feature: BeeLoop can open a draft pull request, notify
you in Slack, and handle GitHub review comments until ready. Ask it to set up
this workflow first; GitHub setup varies by organization.

## Customize

To customize BeeLoop's personality, copy the template and edit `SOUL.md`:

```sh
cp orchestrator/SOUL_template.md orchestrator/SOUL.md
```

Add [skills](orchestrator/.agents/skills/) for workflows you repeat.
For storage locations, configuration, and upgrades, see
[Configuration](configs/README.md).
