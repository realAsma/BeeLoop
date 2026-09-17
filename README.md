# BeeLoop

BeeLoop runs long-lived, directory-backed agents that receive events and message one another.

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
claude plugin marketplace add .
claude plugin install beeloop-tools@beeloop-tools
```

<details>
<summary>Root for BeeLoop and Memory Files (Optional)</summary>

BeeLoop uses this folder as its root and creates the orchestrator workspace
when it is first needed. To use a different folder, create
`~/.config/beeloop/loop.toml` before starting BeeLoop:

```bash
mkdir -p "$HOME/.config/beeloop"
cat > "$HOME/.config/beeloop/loop.toml" <<EOF
root = "$HOME/my-beeloop"
EOF
```

BeeLoop State stores memory files in `~/.beeloop_states` by default. See the
[BeeLoop State configuration](https://github.com/realAsma/BeeLoop-State#configuring-the-root-folder-for-memory-files-optional)
to change this location.

</details>

## Getting Started

Launch the BeeLoop gateway in a detached tmux session:

```sh
tmux new-session -d -s beeloop beeloop
```

This runs the gateway in the background, where it waits for inputs.

### Connect a Slack App

To receive inputs from Slack, follow the
[private Slack input setup](runtime/sources/slack-private/README.md).
