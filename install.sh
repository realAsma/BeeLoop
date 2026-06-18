#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

mkdir -p \
  "$ROOT/inputs.d" \
  "$ROOT/logs" \
  "$ROOT/states/inputs" \
  "$ROOT/states/tasks" \
  "$ROOT/states/workspaces" \
  "$ROOT/tools" \
  "$ROOT/workspaces/workspace_guest"

cp -n "$ROOT/templates/SOUL.md" "$ROOT/SOUL.md"
cp -n "$ROOT/templates/states/index.md" "$ROOT/states/index.md"
touch "$ROOT/MEMORY.md" "$ROOT/workspaces/workspace_guest/MEMORY.md"

if [[ ! -e "$ROOT/secrets.sh" ]]; then
  printf '# Local BeeBot secrets. Do not commit.\n' > "$ROOT/secrets.sh"
fi

chmod -R go-rwx "$ROOT"

printf 'BeeBot setup complete.\n'
