"""Agents: the durable thing, and the backends that give it a voice.

The package is the public surface. `_agent` holds the implementation and is not
imported directly -- callers say `import agents as ag` and get `ag.read(...)`,
`ag.create(...)`, `ag.Agent`, because the collection is what owns the word and
no single filename should have to claim it.

The star import is deliberate: `_agent.__all__` is the one list of what is
public, so adding a name there is the whole of publishing it. Re-listing them
here would be a second list to forget to update.

Note for tests and anything else reaching inward: patching a name through the
package (`ag.read = ...`) rebinds it here only. `_agent`'s own callers still see
their module global, so monkeypatching internals must import `agents._agent`.
"""

from __future__ import annotations

from ._agent import *  # noqa: F403
from ._agent import __all__ as _AGENT_ALL
from .backends import Delivery, InputItem, Session

__all__ = [*_AGENT_ALL, "Delivery", "InputItem", "Session"]
