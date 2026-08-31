"""The wire format: text arriving on stdin, and the addressing parsed out of it.

Knows nothing about agents or the routing table. Whether an envelope's claim can
be satisfied is a question for a layer that can read the table; this one only
refuses claims that contradict themselves, before anything has been created.
"""

from __future__ import annotations

from dataclasses import dataclass


class EnvelopeError(RuntimeError):
    """An envelope the dispatcher refuses to guess at."""


@dataclass
class Envelope:
    """Six fields, and only one of the first two is ever meaningful.

    Two ways to address an agent, never both at once. BY ROUTE is
    `(source, cwd, role, instance)`: derived, guessable, and creates on miss.
    BY IDENTITY is `agent_id`: exact, never creates, and usable only if you
    already hold the id.

    `source` is who CAUSED this, never where it goes. Compared for equality,
    never parsed -- the moment anything reads structure out of it, adapters stop
    being free to choose their own scheme. It is only PART of the routing key:
    one source may drive several agents, and it says so by varying `role`,
    `cwd` and `instance` rather than by encoding them into itself.

    `cwd` belongs to `role`: it is fixed when the agent is created, which is why
    naming one alongside an `agent_id` is refused.

    `instance` is a discriminator scoped to its triple, not a name: the same
    label under a different triple is an unrelated agent.
    """

    role: str | None
    agent_id: str | None
    source: str
    msg: str
    cwd: str | None = None
    instance: str | None = None


def parse(text: str) -> Envelope:
    """Header lines of `key=value`, then `msg=` and everything after it.

    `msg=` ends the header block rather than a blank line, so a body may contain
    blank lines, `=` signs and anything else without escaping.
    """
    headers: dict[str, str] = {}
    body = ""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("msg="):
            body = "\n".join([line[len("msg=") :], *lines[index + 1 :]])
            break
        if not line.strip():
            continue
        key, _, value = line.partition("=")
        headers[key.strip()] = value.strip()
    else:
        raise EnvelopeError("no `msg=` line; an envelope with no body says nothing")

    role = headers.get("role") or None
    agent_id = headers.get("agent_id") or None
    cwd = headers.get("cwd") or None
    instance = headers.get("instance") or None
    if role and agent_id:
        raise EnvelopeError(
            f"envelope carries both role={role!r} and "
            f"agent_id={agent_id!r}; one continues a conversation and the other "
            f"starts one, so resolving this by precedence would only hide the bug"
        )
    if cwd and agent_id:
        raise EnvelopeError(
            f"envelope carries both cwd={cwd!r} and agent_id={agent_id!r}; a cwd "
            f"is fixed when an agent is created, so naming one while continuing "
            f"an existing conversation can only hide a bug"
        )
    if instance and agent_id:
        raise EnvelopeError(
            f"envelope carries both instance={instance!r} and "
            f"agent_id={agent_id!r}; an agent_id names one exact agent while an "
            f"instance only selects which agent a key resolves to, so naming "
            f"both can only hide a bug"
        )
    if not (source := headers.get("source")):
        raise EnvelopeError("envelope has no `source`; nothing could be routed")
    if not body.strip():
        raise EnvelopeError("envelope has an empty `msg`")
    return Envelope(role, agent_id, source, body, cwd, instance)
