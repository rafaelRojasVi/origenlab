"""DB-backed inputs for cold marketing export eligibility (shared ``GateContext``).

**Responsibility:** load Sent recipients, suppression list, outreach sidecar state,
active-commercial hold projection, supplier domains, and assemble
:class:`~origenlab_email_pipeline.candidate_export_gate.GateContext`.

**Not here:** ranked ``lead_master`` selection — that lives in ``next_marketing_queue``.

Export CLIs and read modules should import from this module when they only need gate context;
``next_marketing_queue`` re-exports the same symbols for backward compatibility.
"""

from __future__ import annotations

import sqlite3

from origenlab_email_pipeline.business_mart import emails_in
from origenlab_email_pipeline.candidate_export_gate import GateContext
from origenlab_email_pipeline.contact_domain_suppression import load_suppressed_contact_domain_norms

DEFAULT_SENT_FOLDERS: tuple[str, ...] = ("[Gmail]/Enviados", "[Gmail]/Sent Mail")
DEFAULT_EXCLUDE_DOMAINS: tuple[str, ...] = ("origenlab.cl", "labdelivery.cl")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return bool(row)


def norm_lead_email(email_norm: str | None, email: str | None) -> str | None:
    raw = (email_norm or "").strip() or (email or "").strip()
    if not raw:
        return None
    found = emails_in(raw)
    if not found:
        return None
    return found[0]


def load_sent_recipient_norms(
    conn: sqlite3.Connection,
    *,
    gmail_user: str,
    sent_folders: tuple[str, ...],
) -> set[str]:
    if not _table_exists(conn, "emails"):
        return set()
    user = gmail_user.strip()
    folders = tuple(f.strip() for f in sent_folders if f.strip())
    if not user or not folders:
        return set()
    like_pat = f"gmail:{user}/%".lower()
    out: set[str] = set()
    ph = ",".join("?" * len(folders))
    cur = conn.execute(
        f"""
        SELECT recipients FROM emails
        WHERE lower(source_file) LIKE ?
          AND folder IN ({ph})
        """,
        (like_pat, *folders),
    )
    for (recipients,) in cur:
        if not recipients:
            continue
        for e in emails_in(recipients):
            out.add(e)
    return out


def load_suppressed_norms(conn: sqlite3.Connection) -> set[str]:
    if not _table_exists(conn, "contact_email_suppression"):
        return set()
    rows = conn.execute(
        "SELECT lower(trim(email)) AS e FROM contact_email_suppression WHERE e != ''"
    ).fetchall()
    return {str(r[0]) for r in rows if r[0]}


def load_suppressed_contact_domains(conn: sqlite3.Connection) -> frozenset[str]:
    """Registrable domains blocked for cold export (``contact_domain_suppression`` table)."""
    return load_suppressed_contact_domain_norms(conn)


def load_outreach_state_map(conn: sqlite3.Connection) -> dict[str, str]:
    """email_norm -> state for rows that can affect cold-export eligibility."""
    if not _table_exists(conn, "outreach_contact_state"):
        return {}
    rows = conn.execute(
        """
        SELECT lower(trim(contact_email_norm)) AS e, lower(trim(state)) AS s
        FROM outreach_contact_state
        WHERE state IN ('contacted', 'replied', 'snoozed')
          AND length(trim(contact_email_norm)) > 0
        """
    ).fetchall()
    out: dict[str, str] = {}
    for e, s in rows:
        if e and s:
            out[str(e)] = str(s)
    return out


def load_outreach_contacted_norms(conn: sqlite3.Connection) -> frozenset[str]:
    """Set of emails present in outreach state (contacted, replied, snoozed)."""
    return frozenset(load_outreach_state_map(conn).keys())


def load_active_commercial_hold_norms(conn: sqlite3.Connection) -> frozenset[str]:
    """Current CRM engagement holds mirrored into SQLite by the refresh command.

    The table is a derived projection, not CRM authority. Absence of the table means
    no mirrored holds are available; explicit manual hold/inactive and suppressions
    remain independently enforced by their existing paths.
    """
    if not _table_exists(conn, "outbound_commercial_hold"):
        return frozenset()
    rows = conn.execute(
        """
        SELECT lower(trim(email_norm)) AS e
        FROM outbound_commercial_hold
        WHERE length(trim(email_norm)) > 0
        """
    ).fetchall()
    return frozenset(str(r[0]) for r in rows if r[0])


def build_marketing_export_gate_context(
    conn: sqlite3.Connection,
    *,
    gmail_user: str,
    sent_folders: tuple[str, ...],
    extra_exclude_domains: tuple[str, ...] = (),
    skip_noise_filter: bool = False,
    skip_supplier_domain_filter: bool = False,
    strict_contact_graph_noise: bool = False,
    allow_prior_outreach_history: bool = False,
) -> GateContext:
    """Load DB-backed sets once per export.

    Use ``strict_contact_graph_noise=True`` for ``contact_master`` exports (noisier pool).
    ``allow_prior_outreach_history=True`` keeps Sent/contacted/replied as history rather
    than permanent campaign blockers; suppression, active-commercial holds and snoozed
    state still block.
    """
    from origenlab_email_pipeline.marketing_supplier_domains import supplier_email_domains

    supplier_dom = frozenset() if skip_supplier_domain_filter else supplier_email_domains(conn)
    blocked = frozenset(
        d.strip().lower()
        for d in (list(DEFAULT_EXCLUDE_DOMAINS) + list(extra_exclude_domains))
        if d.strip()
    )
    return GateContext(
        sent_recipient_norms=frozenset(
            load_sent_recipient_norms(conn, gmail_user=gmail_user, sent_folders=sent_folders)
        ),
        suppressed_norms=frozenset(load_suppressed_norms(conn)),
        outreach_state_by_email=load_outreach_state_map(conn),
        supplier_domains=supplier_dom,
        blocked_domains=blocked,
        suppressed_contact_domains=load_suppressed_contact_domains(conn),
        commercial_hold_norms=load_active_commercial_hold_norms(conn),
        skip_noise_filter=skip_noise_filter,
        skip_supplier_domain_filter=skip_supplier_domain_filter,
        strict_contact_graph_noise=strict_contact_graph_noise,
        allow_prior_outreach_history=bool(allow_prior_outreach_history),
    )
