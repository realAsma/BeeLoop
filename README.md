# BeeLoop

BeeLoop runs long-lived, directory-backed agents that receive events and message one another.

## Quick Start

```sh
git clone git@github.com:realAsma/BeeLoop-State.git state_store
python3 -m pip install -e .
ln -s "$PWD/state_store/beeloop-state" ~/.local/bin/beeloop-state
beeloop setup --root "$PWD"
codex plugin marketplace add ./state_store
codex plugin add beeloop-state@beeloop-state
codex plugin marketplace add .
codex plugin add beeloop-tools@beeloop-tools
mkdir -p workspaces/orchestrator
cp -a templates/orchestrator/. workspaces/orchestrator/
```

Setup records machine-local absolute paths in
`~/.config/beeloop/loop.toml` and `~/.config/beeloop/state.toml`. Pass
`--state-dir PATH` to select a state directory other than `~/.beeloop_states`.
Re-running setup without a path preserves that component's existing config;
supplying a path replaces it.

<details>
<summary>Claude setup</summary>

```sh
claude plugin marketplace add ./state_store
claude plugin install beeloop-state@beeloop-state
claude plugin marketplace add .
claude plugin install beeloop-tools@beeloop-tools
```

</details>
