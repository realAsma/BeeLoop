# Input adapters

BeeLoop polls executable files in `inputs.d/`; each emits an envelope.

Example envelope:

```text
role=orchestrator
backend=codex
cwd=workspaces/example-project
instance=default
source=review:example/project:123
msg=Pull request 123 got a new review comment.
See https://example.com/example/project/pull/123 for the thread.
```

BeeLoop parses each envelope and dispatches to an agent.

## Fields

- `role`: Dispatch/creation role; defaults to `orchestrator`.
- `backend`: Optional creation backend; defaults to the role's backend.
- `agent_id`: ID of an existing restorable agent; bypasses route lookup.
- `cwd`: Working directory; relative paths resolve under the configured BeeLoop root. Uses the role default or is required.
- `instance`: Persistence name; missing, empty, or `fresh` always creates; named
  instances reuse the latest restorable agent or create one.
- `source`: Required external session and optional reply handle.
- `msg`: Non-empty event description and location.

## Contract

- `<beeloop-root>/inputs.d/<source-name>` is a thin executable launcher file
  invoking the substantive implementation.
- Keep each source's runtime under
  `<beeloop-root>/runtime/sources/<source-name>/`.
- Keep source runtimes minimal: adapter code, configuration, credentials, and
  transport state only. Store work and artifacts in the input's `cwd` or a
  user-designated project directory.
- Find `<beeloop-root>` with `beeloop root` and enable an input with
  `chmod +x <beeloop-root>/inputs.d/<source-name>`.
- Emit one envelope or nothing to stdout; diagnostics go to stderr.
- Headers use only fields above as single-line `key=value`; unknown fields are
  rejected. `msg` is last; everything after `msg=` is its body.
- `agent_id` selects an exact agent and cannot accompany `role`, `cwd`,
  `instance`, or `backend`. Otherwise BeeLoop routes by
  `(source, cwd, role, backend, instance)`.
- Format `source` as `<source-name>:<session-key>` with a lowercase, hyphenated
  name and a stable identifier for the smallest external conversation that
  shares a reply destination.
- Each poll, inputs validate dependencies, re-emit pending events, and own
  supported external replies; BeeLoop has no queue or automatic replies.
- Store source credentials as `KEY=value` in
  `<beeloop-root>/runtime/sources/<source-name>/secrets.env`; the implementation
  loads it and reports missing required credentials without exposing values.
  Never expose credentials in envelopes, output, logs, state, or commits.
- Reply helpers accept complete `source` and content, validate its namespace,
  own destination lookup/authentication, and report failure.
- If agent-side handling or reply guidance is needed, put it in
  `<beeloop-root>/runtime/sources/<source-name>/SKILL.md` with valid frontmatter.
