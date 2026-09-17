---
name: workspace-management
description: Create, find, or organize project workspaces for orchestrated work.
---

# Workspace management

Follow the user's workspace preferences. Update this skill when the user
provides workspace feedback or preferences.

Within your current working directory, create only the directories you need and
reuse matching workspaces:

```text
./
├── <agent runtime files>  # AGENTS.md, skills, and related configuration.
├── .gitignore
├── secrets.env            # User tokens; keep out of version control.
├── scripts/               # Broadly reusable orchestrator scripts.
└── workspaces/            # Project work and scripts; keep out of version control.
    ├── feature-dev/<repo-name>-<short-work-name>/  # Repository development.
    ├── experiments/<repo-name>-<short-work-name>/  # Exploratory work.
    └── simple-chat/                              # Temporary files, downloads, and clones.
```
