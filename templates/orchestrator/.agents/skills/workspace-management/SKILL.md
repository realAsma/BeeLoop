---
name: workspace-management
description: Create, find, or organize project workspaces for orchestrated work.
---

# Workspace management

Follow the user's workspace preferences. Update this skill when the user
provides workspace feedback or preferences.

Within your current working directory:

```text
./
├── <agent runtime files, such as AGENTS.md and skills>
├── .gitignore
├── secrets.env  # User tokens.
└── workspaces/
    ├── feature-dev/<repo-name>-<short-work-name>/
    ├── experiments/<repo-name>-<short-work-name>/
    └── simple-chat/
```

Keep `secrets.env` and `workspaces/` out of version control.

- Use `feature-dev/` for repository development.
- Use `experiments/` for exploratory work.
- Use `simple-chat/` for temporary files, downloads, and repository clones.
- Create only needed directories and reuse matching workspaces.
