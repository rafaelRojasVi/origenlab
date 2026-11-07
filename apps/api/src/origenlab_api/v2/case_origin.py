"""The channels a commercial case can come from — `crm.opportunity.origin`, said once.

The nine values spell the `opportunity_origin_check` constraint of migration
`20261109100000_slice8_opportunity_origin.sql` and `docs/DOMAIN.md` §7.16 exactly. The column is
nullable: a case nobody classified has no origin, and no rule guesses one.

Origin is set **only when a case is created**, by the command that creates it; linking a message
to an existing case never touches it. An operator corrects it afterwards (`set-case-origin`).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal, get_args

CaseOrigin = Literal[
    "correo",
    "web",
    "whatsapp",
    "campana",
    "mercado_publico",
    "fabricante",
    "recomendacion",
    "llamada",
    "ex_labdelivery",
]
CASE_ORIGINS: tuple[str, ...] = get_args(CaseOrigin)

#: The values the mail rules and the tender command set themselves.
ORIGIN_CORREO: CaseOrigin = "correo"
ORIGIN_WEB: CaseOrigin = "web"
ORIGIN_EX_LABDELIVERY: CaseOrigin = "ex_labdelivery"
ORIGIN_MERCADO_PUBLICO: CaseOrigin = "mercado_publico"

#: `evidence.source_record.payload->>'origin'` of a forwarded request that came from the Labdelivery mailbox.
LABDELIVERY_FORWARD_ORIGIN = "labdelivery"


def forward_case_origin(forward_origins: Iterable[object]) -> CaseOrigin:
    """The origin of a case opened from forwarded requests (R11): `ex_labdelivery` when any copy of
    the request came from the Labdelivery mailbox, otherwise `correo`."""
    if any(str(o) == LABDELIVERY_FORWARD_ORIGIN for o in forward_origins):
        return ORIGIN_EX_LABDELIVERY
    return ORIGIN_CORREO
