---
name: assign-beebot
description: Assign work to a new BeeLoop orchestrator from a direct CLI or desktop conversation, or follow up with the orchestrator already created for this conversation.
---

# Assign BeeBot work

When the user assigns work to BeeBot, call `create_agent(role="orchestrator")`
and message the returned agent ID with the complete assignment, relevant
context, project workspace, and requested deliverables. Retain the agent ID
for this conversation and send subsequent follow-ups directly to it. If the
user names an existing orchestrator, use that ID instead.

Ask the orchestrator to put progress and results in its turn responses. Use
`read_agent(receiver_agent_id=...)` to retrieve its status and latest response;
`message` acceptance confirms dispatch, not completion. Report the assignment
and agent ID promptly. Check responses when the user follows up; do not block
waiting for the work or invent a callback to an unbound CLI session.

The orchestrator maintains its overall BeeLoop State record and delegates
execution. The CLI remains the user's conversation partner and passes their
follow-ups to that orchestrator.
