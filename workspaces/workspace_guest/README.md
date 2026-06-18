# Guest Bee Workspace

This is the workspace for `./bees/guest_bee`, a restricted bee for public-safe
or untrusted intake. It is not tested yet.

- `AGENTS.md`: in-session rules for `guest_bee`
- `MEMORY.md`: owner-curated public-safe details
- `README.md`: this file

Untrusted inputs should dispatch directly to `bee=guest_bee`, not `bee=beebot`.
Configure only public-safe MCPs in the guest Codex home.

## TODO

- Test `guest_bee` end to end with a representative untrusted input.
- Add a VM or container boundary in addition to the Codex read-only sandbox.
- Add a narrow `guest_state` MCP for a bounded `STATE.md` read/write.
