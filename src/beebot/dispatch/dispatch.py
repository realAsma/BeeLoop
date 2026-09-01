"""The dispatcher: envelope in, agent found, batch delivered.

The entry point. It never calls a model, never speaks a provider protocol, and
never branches on delivery mode -- it calls `spin` and the adapter decides
whether that returns after delivering or after parking behind a turn already in
flight. Parsing lives in `envelope`, the table in `routes`; neither imports this.

The envelope stops here: `spin` gets `InputItem(source, msg)`, because
addressing is spent once a recipient exists and a turn is a batch that may mix
envelopes.

Nothing durable is held here. The process exits after every envelope and every
object is rehydrated from disk on the next one.

Run as `python -m beebot.dispatch.dispatch`, never as a path, so imports resolve
through the installed package rather than the script's directory.
"""

from __future__ import annotations

import sys

from beebot import agents as ag
from beebot.agents.backends import InputItem
from beebot.dispatch.envelope import Envelope, parse
from beebot.dispatch.routes import Route, agent_for


def dispatch(envelope: Envelope) -> str:
    """Find the recipient, hand it the input, and say what happened."""
    if envelope.agent_id:
        recipient = ag.restore(envelope.agent_id)  # fail loudly if unknown
    else:
        recipient = agent_for(Route.of(envelope))

    delivery = recipient.spin([InputItem(envelope.source, envelope.msg)])
    if delivery.parked:
        return (
            f"parked {recipient.agent_id} <- {envelope.source} "
            f"(a turn is in flight)"
        )
    record = ag.read(recipient.agent_id)
    return (
        f"delivered {recipient.agent_id} <- {envelope.source} "
        f"turn {record['turns']}: {delivery.text.strip()[:160]}"
    )


def main() -> int:
    try:
        print(dispatch(parse(sys.stdin.read())), flush=True)
    except Exception as exc:  # one bad envelope must not look like success
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
