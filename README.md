# BeeLoop

BeeLoop runs long-lived, directory-backed agents that receive events and message one another.

## Quick Start

```sh
python3 -m pip install -e .
codex plugin marketplace add git@github.com:realAsma/BeeLoop-State.git
codex plugin add beeloop-state@beeloop-state
codex plugin marketplace add .
codex plugin add beeloop-tools@beeloop-tools
```

BeeLoop uses this folder as its root and creates the orchestrator workspace
when it is first needed. To use a different folder, create
`~/.config/beeloop/loop.toml` before starting BeeLoop:

```bash
mkdir -p "$HOME/.config/beeloop"
cat > "$HOME/.config/beeloop/loop.toml" <<EOF
root = "$HOME/my-beeloop"
EOF
```

BeeLoop State stores work in `~/.beeloop_states` by default. Its repository
documents how to override that location.

<details>
<summary>Claude setup</summary>

```sh
claude plugin marketplace add git@github.com:realAsma/BeeLoop-State.git
claude plugin install beeloop-state@beeloop-state
claude plugin marketplace add .
claude plugin install beeloop-tools@beeloop-tools
```

</details>
