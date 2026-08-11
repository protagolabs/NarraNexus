"""
@file_name: system_messages.py
@author: NarraNexus
@date: 2026-08-11
@description: The room lines the PLATFORM writes about itself, in one place.

A team room carries two kinds of message: agents and people talking, and the
platform narrating what it just did — a run was stopped, the bulletin changed, a
patrol swept the board. Almost every consumer that counts, samples or summarises
room activity has to exclude the second kind, and each one had been deciding
that for itself by retyping string literals.

That went wrong exactly the way it always does. `system_stop` and
`system_bulletin` were listed in the summary worker; `patrol` arrived later from
a different feature and nothing told the worker about it, so a filter whose whole
purpose was to stop the platform triggering itself was reopened from the side it
did not know about. The cascade-depth query had the mirror-image gap: it excluded
`patrol` and not the other two.

So the tuple lives here and the consumers import it. Adding a fourth platform
message type is then one edit, and the compiler-free languages of SQL and string
literals stop being the place where the knowledge is kept.

Definitions stay in their own features (`patrol.py`, `team_bulletin.py`); this
module only assembles them, so adding a type does not mean moving it.
"""

from __future__ import annotations

from xyz_agent_context.message_bus.patrol import PATROL_MSG_TYPE
from xyz_agent_context.message_bus.team_bulletin import (
    BULLETIN_NOTICE_MSG_TYPE,
    STOP_NOTICE_MSG_TYPE,
)

# Every msg_type the platform writes about itself. NOT team activity: none of
# them is an agent taking a turn or a person speaking.
PLATFORM_MSG_TYPES = (
    BULLETIN_NOTICE_MSG_TYPE,
    PATROL_MSG_TYPE,
    STOP_NOTICE_MSG_TYPE,
)


def placeholders(ph: str = "%s") -> str:
    """`%s, %s, %s` sized to the tuple.

    So a query says ``NOT IN ({placeholders()})`` instead of hard-coding a
    count. Three call sites previously hard-coded ``(%s, %s)``, which is one
    more thing to remember to change and one more place to get it wrong.

    ``ph`` is a parameter because one caller reaches the backend's own
    placeholder style rather than the client's ``%s`` normalisation.
    """
    return ", ".join([ph] * len(PLATFORM_MSG_TYPES))


__all__ = ["PLATFORM_MSG_TYPES", "placeholders"]
