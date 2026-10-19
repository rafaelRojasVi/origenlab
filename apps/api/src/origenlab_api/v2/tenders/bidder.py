"""Bidder data for tenders (spec §6.2–§6.3): who bids in Mercado Público.

Stub created with `open-tender-case` (Task 4, ruling R8) so the tender reads have one place for
the bidder; Task 5 adds the profile and document commands and reads, and the 403
`role_may_not_view_bidder` that keeps every bidder field from a `viewer` (§6.3).
"""

from __future__ import annotations

from typing import Any


def current_bidder_profile(cur: Any) -> dict[str, Any] | None:
    """The current bidder profile — the newest `crm.bidder_profile` row — or None. Task 5 fills
    in the reads built on it."""
    cur.execute(
        """
        select id::text as id, bidder_kind, legal_name, rut, giro, address, comuna, region, phone, email,
               legal_rep_name, legal_rep_rut, bank_name, bank_account_type, bank_account_number,
               chileproveedores_status, chileproveedores_checked_on, is_emt, is_local_supplier,
               set_by_operator_id::text as set_by_operator_id, reason, created_at
          from crm.bidder_profile
         order by created_at desc, id desc
         limit 1
        """
    )
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([d[0] for d in cur.description], row, strict=True))
