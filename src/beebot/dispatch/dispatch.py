"""Resolve an envelope, deliver its input, and report the result."""

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
    return (
        f"delivered {recipient.agent_id} <- {envelope.source} "
        f"turn {recipient.record['turns']}: {delivery.text.strip()[:160]}"
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
