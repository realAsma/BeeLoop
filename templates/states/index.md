# State Index

BeeBot maintains this discovery index to identify which state file to open.
Canonical relationships live in the input, task, and workspace state files.

Entries use one compact bullet:
- YYYY-MM-DDTHH:MM:SSZ | kind:key | status | state-file | one-line summary

`key` is the stable, filesystem-safe identifier for the state file, such as
`cli_second_full_agents_read`, `slack_dm_D0B8VPR1L9J_1781292144669949`, or
`beebot_root`. The pair `kind:key` identifies an index entry.

On every read or write of indexed state, remove any prior entry with the same
`kind:key` and insert the refreshed entry at the top.

State rows start after the divider.

---
