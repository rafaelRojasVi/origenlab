"""Message-level directional opportunity-signal bridge (upstream of PR3).

Real-message-inspired cases mirror ``tests/test_warm_case_role_direction_regressions.py``
so the bridge's allowlist stays aligned with the merged warm-case role classifier
(PR #507). See ``core/mart/directional_opportunity_signal_builder.py`` for scope.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter

from origenlab_email_pipeline.commercial_identity.models import IdentityResolution
from origenlab_email_pipeline.commercial_opportunity.resolve import resolve_opportunities
from origenlab_email_pipeline.commercial_opportunity.sources import load_opportunity_signals
from origenlab_email_pipeline.core.mart.directional_opportunity_signal_builder import (
    DIRECTIONAL_SIGNAL_SOURCE_TAG,
    compute_directional_opportunity_signal_rows,
)
from origenlab_email_pipeline.core.mart.opportunity_signal_builder import rebuild_opportunity_signals

_EMAILS_DDL = """
CREATE TABLE emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT NOT NULL,
    subject TEXT,
    sender TEXT,
    recipients TEXT,
    date_iso TEXT,
    top_reply_clean TEXT
);
"""

# Mirrors commercial_intel_schema.py's commercial_email_signal_fact DDL exactly.
_FACT_DDL = """
CREATE TABLE commercial_email_signal_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email_id INTEGER NOT NULL,
  source_file TEXT NOT NULL,
  sent_at TEXT,
  sender_email TEXT,
  sender_domain TEXT,
  contact_email TEXT,
  contact_domain TEXT,
  org_domain TEXT,
  signal_code TEXT NOT NULL,
  signal_kind TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  reason_text TEXT NOT NULL,
  confidence_score REAL NOT NULL,
  strength_score REAL NOT NULL,
  rationale_json TEXT NOT NULL,
  run_id INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE(email_id, signal_code, reason_code, contact_email, org_domain),
  FOREIGN KEY(email_id) REFERENCES emails(id) ON DELETE CASCADE
);
"""

# Mirrors production opportunity_signals DDL (matches REQUIRED_OPPORTUNITY_SIGNAL_COLS).
_OPPORTUNITY_SIGNALS_DDL = """
CREATE TABLE opportunity_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_type TEXT NOT NULL,
    entity_kind TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    email_id INTEGER,
    attachment_id INTEGER,
    score REAL,
    details_json TEXT,
    created_at TEXT
);
"""

_INBOX = "gmail:contacto@origenlab.cl/INBOX"
_SENT = "gmail:contacto@origenlab.cl/[Gmail]/Enviados"


def _db_with_emails_and_facts() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_EMAILS_DDL + _FACT_DDL)
    return conn


def _insert_email(
    conn: sqlite3.Connection,
    *,
    email_id: int,
    source_file: str,
    subject: str,
    sender: str,
    recipients: str = "",
    date_iso: str,
    body: str,
) -> None:
    conn.execute(
        """
        INSERT INTO emails (id, source_file, subject, sender, recipients, date_iso, top_reply_clean)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (email_id, source_file, subject, sender, recipients, date_iso, body),
    )
    conn.commit()


def _insert_fact(
    conn: sqlite3.Connection,
    *,
    email_id: int,
    source_file: str,
    contact_email: str,
    org_domain: str,
    signal_kind: str = "positive",
    signal_code: str = "commercial_signal",
    reason_code: str = "commercial_signal_detected",
    strength_score: float = 0.6,
    confidence_score: float = 0.8,
) -> None:
    conn.execute(
        """
        INSERT INTO commercial_email_signal_fact (
          email_id, source_file, sent_at, sender_email, sender_domain,
          contact_email, contact_domain, org_domain,
          signal_code, signal_kind, reason_code, reason_text,
          confidence_score, strength_score, rationale_json, run_id, created_at
        ) VALUES (?, ?, '', '', '', ?, ?, ?, ?, ?, ?, '', ?, ?, '{}', NULL, '2026-01-01T00:00:00+00:00')
        """,
        (
            email_id,
            source_file,
            contact_email,
            org_domain,
            org_domain,
            signal_code,
            signal_kind,
            reason_code,
            confidence_score,
            strength_score,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 1. UC SERVA inquiry -> client_opportunity, exact email_id retained
# ---------------------------------------------------------------------------


def test_uc_serva_inquiry_emits_client_opportunity_signal() -> None:
    conn = _db_with_emails_and_facts()
    _insert_email(
        conn,
        email_id=101,
        source_file=_INBOX,
        subject="Consulta cotización",
        sender='"Calidad Agua.ing" <calidadagua.ing@uc.cl>',
        date_iso="2026-02-10T09:00:00+00:00",
        body=(
            "Estimados, junto con saludar, me comunico para consultar "
            "si comercializan la solución de silicona SERVA en isopropanol."
        ),
    )
    _insert_fact(
        conn,
        email_id=101,
        source_file=_INBOX,
        contact_email="calidadagua.ing@uc.cl",
        org_domain="uc.cl",
        strength_score=0.65,
    )

    rows = compute_directional_opportunity_signal_rows(conn)
    assert len(rows) == 1
    signal_type, entity_kind, entity_key, email_id, attachment_id, score, details_json, _created_at = rows[0]
    assert signal_type == "client_opportunity"
    assert entity_kind == "contact"
    assert entity_key == "calidadagua.ing@uc.cl"
    assert email_id == 101
    assert attachment_id is None
    assert 0.0 <= score <= 1.0
    details = json.loads(details_json)
    assert details == {"source": DIRECTIONAL_SIGNAL_SOURCE_TAG, "role_category": "client_opportunity"}


# ---------------------------------------------------------------------------
# 2. Carozzi (no immediate need, future 2-3yr tender) -> client_opportunity allowed,
#    no invented urgency/currentness
# ---------------------------------------------------------------------------


def test_carozzi_future_tender_allows_client_opportunity_without_urgency() -> None:
    conn = _db_with_emails_and_facts()
    _insert_email(
        conn,
        email_id=202,
        source_file=_INBOX,
        subject="RE: [EXTERNO]: Balanzas y Soluciones ADAM Equipment | OrigenLab",
        sender="Daniela Sepulveda Rojas <daniela.sepulveda@carozzi.cl>",
        date_iso="2026-03-05T15:30:00+00:00",
        body=(
            "Gracias por tu pronta respuesta, en lo inmediato no necesito "
            "ningún servicio, pero sí contarte que nos encontramos en proceso "
            "de licitación para el contrato que gestionamos por 2 o 3 años."
        ),
    )
    _insert_fact(
        conn,
        email_id=202,
        source_file=_INBOX,
        contact_email="daniela.sepulveda@carozzi.cl",
        org_domain="carozzi.cl",
        signal_code="future_tender_mention",
        strength_score=0.55,
    )

    rows = compute_directional_opportunity_signal_rows(conn)
    assert len(rows) == 1
    signal_type, entity_kind, entity_key, email_id, _attachment_id, _score, details_json, _created_at = rows[0]
    assert signal_type == "client_opportunity"
    assert entity_key == "daniela.sepulveda@carozzi.cl"
    assert email_id == 202
    details = json.loads(details_json)
    assert details["role_category"] == "client_opportunity"
    # No fabricated urgency/currentness fields — details_json stays small and safe.
    assert set(details) == {"source", "role_category"}


# ---------------------------------------------------------------------------
# 3. Kalstein promotional discount blast -> NO directional signal
# ---------------------------------------------------------------------------


def test_kalstein_discount_blast_emits_no_directional_signal() -> None:
    conn = _db_with_emails_and_facts()
    _insert_email(
        conn,
        email_id=303,
        source_file=_INBOX,
        subject="Kalstein Plus: disfrute de descuentos exclusivos de entre el 22 % y el 36 %.",
        sender="Diana Lopez <dianalopez@kalstein.net>",
        date_iso="2026-01-15T08:00:00+00:00",
        body=(
            "Una de las principales ventajas de formar parte de Kalstein Plus "
            "es acceder a condiciones comerciales diseñadas para aumentar..."
        ),
    )
    _insert_fact(
        conn,
        email_id=303,
        source_file=_INBOX,
        contact_email="dianalopez@kalstein.net",
        org_domain="kalstein.net",
        strength_score=0.6,
    )

    assert compute_directional_opportunity_signal_rows(conn) == []


# ---------------------------------------------------------------------------
# 4. SERVA supplier attached offer -> NO client directional signal
# ---------------------------------------------------------------------------


def test_serva_supplier_offer_emits_no_client_directional_signal() -> None:
    conn = _db_with_emails_and_facts()
    _insert_email(
        conn,
        email_id=404,
        source_file=_INBOX,
        subject="AW: Quotation Request / New adress created for your compagny 310471",
        sender="Serva_Order <order@serva.de>",
        date_iso="2026-02-20T11:00:00+00:00",
        body=(
            "Dear Tatiana, please find attached our additional offer N260733 "
            "for the positions listed below from your Tender request."
        ),
    )
    _insert_fact(
        conn,
        email_id=404,
        source_file=_INBOX,
        contact_email="order@serva.de",
        org_domain="serva.de",
        strength_score=0.7,
    )

    assert compute_directional_opportunity_signal_rows(conn) == []


# ---------------------------------------------------------------------------
# 5. AP Data application worksheet / configure-and-quote response
#    -> NO client directional signal (supplier_followup)
# ---------------------------------------------------------------------------


def test_apdata_worksheet_reply_emits_no_client_directional_signal() -> None:
    conn = _db_with_emails_and_facts()
    _insert_email(
        conn,
        email_id=505,
        source_file=_INBOX,
        subject="Re: Request for quotation - Dynamic Checkweigher",
        sender="Ron Debiaso <rdebiaso@apdataweigh.com>",
        date_iso="2026-02-22T14:00:00+00:00",
        body=(
            "Please use the attached worksheet to provide as much information "
            "as possible about the product you want to weigh and your process. "
            "I will be happy to configure and quote a system for you."
        ),
    )
    _insert_fact(
        conn,
        email_id=505,
        source_file=_INBOX,
        contact_email="rdebiaso@apdataweigh.com",
        org_domain="apdataweigh.com",
        strength_score=0.6,
    )

    assert compute_directional_opportunity_signal_rows(conn) == []


# ---------------------------------------------------------------------------
# 6. outbound AP Data RFQ -> NO client directional signal (waiting_supplier)
# ---------------------------------------------------------------------------


def test_outbound_apdata_rfq_emits_no_client_directional_signal() -> None:
    conn = _db_with_emails_and_facts()
    _insert_email(
        conn,
        email_id=606,
        source_file=_SENT,
        subject="Request for quotation - Dynamic Checkweigher",
        sender="Tatiana Vivanco | OrigenLab <contacto@origenlab.cl>",
        recipients="sales@apdataweigh.com",
        date_iso="2026-02-18T10:00:00+00:00",
        body="Please quote a dynamic checkweigher for our customer.",
    )
    _insert_fact(
        conn,
        email_id=606,
        source_file=_SENT,
        contact_email="sales@apdataweigh.com",
        org_domain="apdataweigh.com",
        strength_score=0.5,
    )

    assert compute_directional_opportunity_signal_rows(conn) == []


# ---------------------------------------------------------------------------
# 7. quote-like outbound thread to a known supplier -> NO PR3 directional signal
# ---------------------------------------------------------------------------


def test_outbound_quote_like_subject_to_supplier_emits_no_directional_signal() -> None:
    conn = _db_with_emails_and_facts()
    _insert_email(
        conn,
        email_id=700,
        source_file=_SENT,
        subject="Re: Cotización",
        sender="Tatiana Vivanco | OrigenLab <contacto@origenlab.cl>",
        recipients="Carmen Llorente <carmen.llorente@ortoalresa.com>",
        date_iso="2026-02-25T15:00:00+00:00",
        body="Gracias, quedo atenta.",
    )
    _insert_fact(
        conn,
        email_id=700,
        source_file=_SENT,
        # Deliberately stale production-like mart identity.
        contact_email="contacto@origenlab.cl",
        org_domain="origenlab.cl",
        strength_score=0.6,
    )

    assert compute_directional_opportunity_signal_rows(conn) == []


# ---------------------------------------------------------------------------
# 8. full recipient field is used for counterparty provenance
# ---------------------------------------------------------------------------


def test_outbound_counterparty_resolution_does_not_truncate_recipients() -> None:
    conn = _db_with_emails_and_facts()

    recipients = "; ".join(
        ["contacto@origenlab.cl"] * 12
        + ["compras@hospital.cl"]
    )
    assert recipients.index("compras@hospital.cl") > 200

    _insert_email(
        conn,
        email_id=701,
        source_file=_SENT,
        subject="Cotización Balanzas ADAM Equipment",
        sender="Tatiana Vivanco | OrigenLab <contacto@origenlab.cl>",
        recipients=recipients,
        date_iso="2026-02-25T15:30:00+00:00",
        body="Adjunto cotización solicitada.",
    )
    _insert_fact(
        conn,
        email_id=701,
        source_file=_SENT,
        contact_email="contacto@origenlab.cl",
        org_domain="origenlab.cl",
        strength_score=0.6,
    )

    rows = compute_directional_opportunity_signal_rows(conn)

    assert len(rows) == 1
    assert rows[0][0] == "quote_sent"
    assert rows[0][1] == "contact"
    assert rows[0][2] == "compras@hospital.cl"
    assert rows[0][3] == 701



# ---------------------------------------------------------------------------
# 9. outbound OrigenLab quotation to a real client -> quote_sent, email_id retained
# ---------------------------------------------------------------------------


def test_outbound_quotation_to_client_emits_quote_sent_signal() -> None:
    conn = _db_with_emails_and_facts()
    _insert_email(
        conn,
        email_id=707,
        source_file=_SENT,
        subject="Cotización Balanzas ADAM Equipment",
        sender="Tatiana Vivanco | OrigenLab <contacto@origenlab.cl>",
        recipients="compras@hospital.cl",
        date_iso="2026-02-25T16:00:00+00:00",
        body="Adjunto cotización solicitada para balanza analítica ADAM Equipment.",
    )
    _insert_fact(
        conn,
        email_id=707,
        source_file=_SENT,
        contact_email="contacto@origenlab.cl",
        org_domain="origenlab.cl",
        strength_score=0.6,
    )

    rows = compute_directional_opportunity_signal_rows(conn)
    assert len(rows) == 1
    signal_type, entity_kind, entity_key, email_id, _attachment_id, _score, details_json, _created_at = rows[0]
    assert signal_type == "quote_sent"
    assert entity_kind == "contact"
    assert entity_key == "compras@hospital.cl"
    assert email_id == 707
    assert json.loads(details_json)["role_category"] == "quote_sent"


# ---------------------------------------------------------------------------
# 10. missing commercial_email_signal_fact -> existing aggregate behavior stays
#    functional, no crash, zero directional signals
# ---------------------------------------------------------------------------


def test_missing_commercial_email_signal_fact_table_does_not_crash() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_EMAILS_DDL + _OPPORTUNITY_SIGNALS_DDL)

    assert compute_directional_opportunity_signal_rows(conn) == []

    contact = {
        "legacy@hospital.cl": {
            "quote_email": 2,
            "quote_doc": 1,
            "total": 5,
            "last_seen_at": "2024-06-01",
            "equip": Counter(),
        }
    }
    rebuild_opportunity_signals(conn, contact, {})

    rows = conn.execute("SELECT signal_type, email_id FROM opportunity_signals").fetchall()
    assert rows == [("quote_email_plus_quote_doc", None)]


# ---------------------------------------------------------------------------
# 11. duplicate commercial_email_signal_fact rows for one email -> one signal only
# ---------------------------------------------------------------------------


def test_duplicate_fact_rows_for_one_email_dedupe_to_one_signal() -> None:
    conn = _db_with_emails_and_facts()
    _insert_email(
        conn,
        email_id=909,
        source_file=_INBOX,
        subject="Consulta cotización",
        sender='"Calidad Agua.ing" <calidadagua.ing@uc.cl>',
        date_iso="2026-02-10T09:00:00+00:00",
        body=(
            "Estimados, junto con saludar, me comunico para consultar "
            "si comercializan la solución de silicona SERVA en isopropanol."
        ),
    )
    _insert_fact(
        conn,
        email_id=909,
        source_file=_INBOX,
        contact_email="calidadagua.ing@uc.cl",
        org_domain="uc.cl",
        signal_code="client_quote_request",
        reason_code="quote_terms_detected",
        strength_score=0.6,
    )
    _insert_fact(
        conn,
        email_id=909,
        source_file=_INBOX,
        contact_email="calidadagua.ing@uc.cl",
        org_domain="uc.cl",
        signal_code="client_price_interest",
        reason_code="price_terms_detected",
        strength_score=0.7,
    )

    rows = compute_directional_opportunity_signal_rows(conn)
    assert len(rows) == 1
    assert rows[0][0] == "client_opportunity"
    assert rows[0][3] == 909


# ---------------------------------------------------------------------------
# Integration: full rebuild + PR3 signal loader + resolve_opportunities
# ---------------------------------------------------------------------------


def _seed_integration_fixture(conn: sqlite3.Connection) -> None:
    conn.executescript(_EMAILS_DDL + _FACT_DDL + _OPPORTUNITY_SIGNALS_DDL)

    # Allowlisted: emits client_opportunity.
    _insert_email(
        conn,
        email_id=1,
        source_file=_INBOX,
        subject="Consulta cotización",
        sender='"Calidad Agua.ing" <calidadagua.ing@uc.cl>',
        date_iso="2026-02-10T09:00:00+00:00",
        body=(
            "Estimados, junto con saludar, me comunico para consultar "
            "si comercializan la solución de silicona SERVA en isopropanol."
        ),
    )
    _insert_fact(
        conn,
        email_id=1,
        source_file=_INBOX,
        contact_email="calidadagua.ing@uc.cl",
        org_domain="uc.cl",
        strength_score=0.65,
    )

    # Allowlisted: emits quote_sent.
    _insert_email(
        conn,
        email_id=2,
        source_file=_SENT,
        subject="Cotización Balanzas ADAM Equipment",
        sender="Tatiana Vivanco | OrigenLab <contacto@origenlab.cl>",
        recipients="compras@hospital.cl",
        date_iso="2026-02-25T16:00:00+00:00",
        body="Adjunto cotización solicitada para balanza analítica ADAM Equipment.",
    )
    _insert_fact(
        conn,
        email_id=2,
        source_file=_SENT,
        contact_email="compras@hospital.cl",
        org_domain="hospital.cl",
        strength_score=0.6,
    )

    # Not allowlisted: system_noise (marketing blast).
    _insert_email(
        conn,
        email_id=3,
        source_file=_INBOX,
        subject="Kalstein Plus: disfrute de descuentos exclusivos de entre el 22 % y el 36 %.",
        sender="Diana Lopez <dianalopez@kalstein.net>",
        date_iso="2026-01-15T08:00:00+00:00",
        body="Una de las principales ventajas de formar parte de Kalstein Plus es acceder...",
    )
    _insert_fact(
        conn,
        email_id=3,
        source_file=_INBOX,
        contact_email="dianalopez@kalstein.net",
        org_domain="kalstein.net",
        strength_score=0.6,
    )

    # Not allowlisted: supplier_quote_received.
    _insert_email(
        conn,
        email_id=4,
        source_file=_INBOX,
        subject="AW: Quotation Request / New adress created for your compagny 310471",
        sender="Serva_Order <order@serva.de>",
        date_iso="2026-02-20T11:00:00+00:00",
        body="Dear Tatiana, please find attached our additional offer N260733...",
    )
    _insert_fact(
        conn,
        email_id=4,
        source_file=_INBOX,
        contact_email="order@serva.de",
        org_domain="serva.de",
        strength_score=0.7,
    )

    # Not allowlisted: waiting_supplier (outbound RFQ).
    _insert_email(
        conn,
        email_id=5,
        source_file=_SENT,
        subject="Request for quotation - Dynamic Checkweigher",
        sender="Tatiana Vivanco | OrigenLab <contacto@origenlab.cl>",
        recipients="sales@apdataweigh.com",
        date_iso="2026-02-18T10:00:00+00:00",
        body="Please quote a dynamic checkweigher for our customer.",
    )
    _insert_fact(
        conn,
        email_id=5,
        source_file=_SENT,
        contact_email="sales@apdataweigh.com",
        org_domain="apdataweigh.com",
        strength_score=0.5,
    )

    conn.commit()


def test_integration_rebuild_preserves_legacy_and_adds_directional_rows() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _seed_integration_fixture(conn)

    contact = {
        "legacy@hospital.cl": {
            "quote_email": 2,
            "quote_doc": 1,
            "total": 5,
            "last_seen_at": "2024-06-01",
            "equip": Counter(),
        }
    }
    rebuild_opportunity_signals(conn, contact, {})

    all_rows = conn.execute(
        "SELECT signal_type, entity_kind, entity_key, email_id, details_json FROM opportunity_signals"
    ).fetchall()

    legacy_rows = [r for r in all_rows if r["signal_type"] == "quote_email_plus_quote_doc"]
    assert len(legacy_rows) == 1
    assert legacy_rows[0]["email_id"] is None  # legacy aggregate rows are preserved unchanged

    directional_rows = [
        r
        for r in all_rows
        if r["details_json"] and DIRECTIONAL_SIGNAL_SOURCE_TAG in r["details_json"]
    ]
    assert {r["signal_type"] for r in directional_rows} == {"client_opportunity", "quote_sent"}
    assert {r["email_id"] for r in directional_rows} == {1, 2}
    for r in directional_rows:
        assert r["email_id"] is not None

    # Supplier/admin/noise rows are absent — only the two allowlisted rows exist.
    assert len(directional_rows) == 2
    assert len(all_rows) == 3  # 1 legacy + 2 directional

    # PR3's existing loader recovers emails.date_iso for the directional rows.
    loaded = load_opportunity_signals(conn)
    by_email_id = {s.email_id: s for s in loaded if s.email_id is not None}
    assert by_email_id[1].email_date == "2026-02-10T09:00:00+00:00"
    assert by_email_id[1].signal_type == "client_opportunity"
    assert by_email_id[2].email_date == "2026-02-25T16:00:00+00:00"
    assert by_email_id[2].signal_type == "quote_sent"

    # Feed into PR3's resolver and confirm dated evidence_candidate behavior.
    identity = IdentityResolution(accounts=[], contacts=[], evidence=[], conflicts=[], metrics={})
    res = resolve_opportunities(
        identity=identity,
        deals=[],
        events=[],
        documents=[],
        payments=[],
        signals=loaded,
    )

    candidates_by_source_key = {
        o.source_key: o for o in res.opportunities if o.record_kind == "evidence_candidate"
    }
    client_opp_signal_id = by_email_id[1].signal_id
    quote_sent_signal_id = by_email_id[2].signal_id

    client_opp = candidates_by_source_key[client_opp_signal_id]
    assert client_opp.canonical_stage == "qualifying"
    assert client_opp.stage_evidence_at == "2026-02-10T09:00:00+00:00"
    assert client_opp.stage_is_current is False
    assert client_opp.review_status == "needs_review"

    quote_sent = candidates_by_source_key[quote_sent_signal_id]
    assert quote_sent.canonical_stage == "quote_sent"
    assert quote_sent.stage_evidence_at == "2026-02-25T16:00:00+00:00"
    assert quote_sent.stage_is_current is False
