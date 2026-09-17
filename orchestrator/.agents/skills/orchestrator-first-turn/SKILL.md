---
name: orchestrator-first-turn
description: On an orchestrator's first turn, own new work or relay related work to its existing primary.
---

# Orchestrator first turn

Call `get_agent_id()` first. If it returns `None`, follow the
`outside-orchestrator` skill and do not continue below.

## Determine whether you are primary or secondary

Read the input packet and infer the work it represents. Use
`beeloop-state:state-ask` with the current cwd and concise keywords or a short
summary of that work to find related existing work. If none exists, you are the
primary orchestrator. If related work exists, its assigned orchestrator is the
primary and you are a secondary.

## Do this if you are primary

Save a BeeLoop State record with the work details and this artifact:
`{"item":"agent:<your-agent-id>","note":"Primary orchestrator."}`. Then handle
the input normally.

## Do this if you are secondary

Send the primary orchestrator the complete new input packet, preserving its
sources, messages, and order. Then call `route_source(source,
receiver_agent_id)` for each persistent input source you own so future messages
go to the primary. Disable your heartbeat timer. Do not claim or perform the
work.
