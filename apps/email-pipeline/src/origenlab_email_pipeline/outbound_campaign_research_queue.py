"""Fresh-public organization/contact research queue for outbound campaigns.

**Purpose:** when a campaign's known-contact universe (``contact_master`` /
``lead_master`` rows that already carry a usable email) is exhausted, find
*organizations* worth researching for a fresh public contact (a lab manager,
researcher, QA/QC lead, or technical laboratory contact) using only
structured ``lead_master`` evidence — never the presence of the word "lab"
in an email or domain string alone.

**Read-only.** This module only ``SELECT``s. It never writes to
``lead_master``, ``lead_contact_research``, ``outbound_campaign_recipient``,
or any suppression table. The caller (CLI) is responsible for writing the
CSV artifact; nothing here touches disk either.

Reused, not duplicated:
- ``lead_export_queries.sql_upstream_active_lead_master`` — same upstream-active
  predicate as every other lead export.
- ``marketing_supplier_domains`` (``supplier_master``-backed) for supplier-domain
  exclusion.
- ``contact_domain_suppression`` for domain-suppression exclusion.
- ``candidate_export_gate.normalize_export_email`` for email validity.
- ``marketing_contact_noise.marketing_outreach_noise_organization_guess`` for
  noise-organization exclusion.

Relevance is decided from structured columns only (``fit_bucket``,
``priority_score``, ``lab_context_score``, ``lab_context_tags``,
``equipment_match_tags``) — there is no ``sonicador``/``ultrasonido`` tag in
the current ``equipment_match_tags`` taxonomy (verified against production:
see ``docs/RUNBOOK.md`` campaign notes), so a lab/research-context organization
that already buys adjacent lab equipment (centrifuges, autoclaves,
spectrophotometers, HPLC, etc.) is the best available public-research target,
not a literal sonicator-tag match.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from origenlab_email_pipeline.candidate_export_gate import (
    email_domain_under_operator_domain_suppression,
    normalize_export_email,
)
from origenlab_email_pipeline.contact_domain_suppression import (
    load_suppressed_contact_domain_norms,
)
from origenlab_email_pipeline.lead_export_queries import sql_upstream_active_lead_master
from origenlab_email_pipeline.marketing_contact_noise import (
    marketing_outreach_noise_organization_guess,
)
from origenlab_email_pipeline.marketing_supplier_domains import (
    is_supplier_email_domain,
    supplier_email_domains,
)

DEFAULT_FIT_BUCKETS: tuple[str, ...] = ("high_fit", "medium_fit")
_RESEARCH_CONTACTABLE_STATUSES = ("contacto_encontrado", "listo_para_contacto")
_RESEARCH_DISCARDED_STATUS = "descartado"

_FIT_RANK = {"high_fit": 0, "medium_fit": 1, "low_fit": 2}


def _fit_rank(fit_bucket: str | None) -> int:
    return _FIT_RANK.get((fit_bucket or "").strip().lower(), 2)


# Ranking tiebreaker only -- never an exclusion filter. Chilecompra's public-tender
# pool is dominated by hospital sterilization procurement (autoclave-only tenders);
# that is real "laboratorio" evidence but a weak proxy for a lab *sonicator* buyer
# compared to a university/research org or a tender for extraction/analysis gear
# (centrifuge, HPLC, spectrophotometer, pH meter, freeze-dryer, microbiology).
# Tier 0 orgs surface first in the ranked CSV; tier 2 orgs still appear -- just later.
_RESEARCH_CONTEXT_BUYER_KINDS = frozenset({"universidad"})
_RESEARCH_CONTEXT_ORG_TYPES = frozenset({"education"})
_RESEARCH_CONTEXT_LAB_TAGS = frozenset({
    "investigacion_docencia", "microbiologia", "ambiental_agua_residuos", "calibracion_metrologia",
})
_EXTRACTION_ANALYSIS_EQUIPMENT_TAGS = frozenset({
    "centrifuga", "cromatografia_hplc", "espectrofotometro", "phmetro", "liofilizador", "pipetas",
})


def _research_quality_tier(org: "ResearchOrg") -> int:
    buyer_kind = (org.buyer_kind or "").strip().lower()
    org_type = (org.organization_type_guess or "").strip().lower()
    lab_tags = {t.strip() for t in (org.lab_context_tags or "").split(",") if t.strip()}
    equip_tags = {t.strip() for t in (org.equipment_match_tags or "").split(",") if t.strip()}
    if (
        buyer_kind in _RESEARCH_CONTEXT_BUYER_KINDS
        or org_type in _RESEARCH_CONTEXT_ORG_TYPES
        or lab_tags & _RESEARCH_CONTEXT_LAB_TAGS
    ):
        return 0
    if equip_tags & _EXTRACTION_ANALYSIS_EQUIPMENT_TAGS:
        return 1
    return 2


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (name,)
    ).fetchone()
    return bool(row)


@dataclass
class ResearchOrg:
    org_key: str
    org_name: str
    domain: str
    website: str
    region: str
    city: str
    buyer_kind: str
    organization_type_guess: str
    lead_type: str
    source_name: str
    fit_bucket: str
    priority_score: float
    lab_context_score: float
    lab_context_tags: str
    equipment_match_tags: str
    evidence_summary: str
    lead_ids: list[int] = field(default_factory=list)
    contact_research_status: str = ""
    block_reason: str = ""


@dataclass(frozen=True)
class ResearchQueueStats:
    leads_scanned: int
    orgs_scanned: int
    blocked_supplier: int
    blocked_suppression: int
    blocked_already_has_contact: int
    blocked_too_low_relevance_leads: int
    blocked_noise: int
    blocked_discarded: int
    final_queue_count: int


def _row_is_relevant(
    *, fit_bucket: str | None, fit_buckets: tuple[str, ...],
    lab_context_tags: str | None, equipment_match_tags: str | None,
) -> bool:
    """Structured-evidence relevance gate: fit bucket + at least one real tag.

    Never inspects email/domain text for the word "lab" — only the
    ``lead_master`` classification columns already computed by the pipeline.
    """
    fb = (fit_bucket or "").strip().lower()
    if fit_buckets and fb not in fit_buckets:
        return False
    has_lab_tag = bool((lab_context_tags or "").strip())
    has_equip_tag = bool((equipment_match_tags or "").strip())
    return has_lab_tag or has_equip_tag


def compute_research_queue(
    conn: sqlite3.Connection,
    *,
    fit_buckets: tuple[str, ...] = DEFAULT_FIT_BUCKETS,
    limit: int = 200,
    include_discarded: bool = False,
) -> tuple[list[ResearchOrg], ResearchQueueStats]:
    """Read-only: rank organizations that need fresh public contact research.

    Groups ``lead_master`` rows by organization (``domain_norm`` when present,
    else ``org_name_norm``), then excludes, in order:
      1. rows with no structured lab-relevance signal (fit bucket / tags),
      2. organizations that already have a usable contact (a valid
         ``lead_master`` email, or a ``lead_contact_research`` row resolved to
         a valid email with a contactable status),
      3. organizations on a known supplier domain (``supplier_master``),
      4. organizations on an operator-suppressed domain
         (``contact_domain_suppression``),
      5. organizations matching known noise-source names,
      6. organizations the operator already marked ``descartado`` in
         ``lead_contact_research`` (unless ``include_discarded``).
    """
    has_research = _table_exists(conn, "lead_contact_research")
    research_join = "LEFT JOIN lead_contact_research lcr ON lcr.lead_id = lm.id" if has_research else ""
    research_cols = (
        "lcr.contact_research_status, lcr.resolved_contact_email"
        if has_research
        else "NULL AS contact_research_status, NULL AS resolved_contact_email"
    )
    where_active = sql_upstream_active_lead_master("lm")

    rows = conn.execute(
        f"""
        SELECT
          lm.id, lm.org_name, lm.org_name_norm, lm.domain, lm.domain_norm, lm.website,
          lm.region, lm.city, lm.lead_type, lm.organization_type_guess, lm.buyer_kind,
          lm.equipment_match_tags, lm.lab_context_score, lm.lab_context_tags,
          lm.fit_bucket, lm.priority_score, lm.evidence_summary, lm.source_name,
          lm.email, lm.email_norm,
          {research_cols}
        FROM lead_master lm
        {research_join}
        WHERE {where_active}
        ORDER BY lm.id
        """
    ).fetchall()
    cols = [
        "id", "org_name", "org_name_norm", "domain", "domain_norm", "website",
        "region", "city", "lead_type", "organization_type_guess", "buyer_kind",
        "equipment_match_tags", "lab_context_score", "lab_context_tags",
        "fit_bucket", "priority_score", "evidence_summary", "source_name",
        "email", "email_norm", "contact_research_status", "resolved_contact_email",
    ]

    leads_scanned = len(rows)
    blocked_low_relevance = 0
    orgs: dict[str, ResearchOrg] = {}
    org_has_valid_lead_email: dict[str, bool] = {}
    org_has_researched_contact: dict[str, bool] = {}
    org_statuses: dict[str, set[str]] = {}

    for raw in rows:
        d = dict(zip(cols, raw))
        if not _row_is_relevant(
            fit_bucket=d["fit_bucket"], fit_buckets=fit_buckets,
            lab_context_tags=d["lab_context_tags"], equipment_match_tags=d["equipment_match_tags"],
        ):
            blocked_low_relevance += 1
            continue

        domain_norm = (d["domain_norm"] or "").strip().lower()
        org_name_norm = (d["org_name_norm"] or "").strip().lower()
        org_key = domain_norm or org_name_norm or f"lead:{d['id']}"

        lead_email_valid = normalize_export_email(d["email_norm"] or d["email"] or "") is not None
        org_has_valid_lead_email[org_key] = org_has_valid_lead_email.get(org_key, False) or lead_email_valid

        research_status = (d["contact_research_status"] or "").strip().lower()
        research_email_valid = normalize_export_email(d["resolved_contact_email"] or "") is not None
        contactable = research_status in _RESEARCH_CONTACTABLE_STATUSES and research_email_valid
        org_has_researched_contact[org_key] = org_has_researched_contact.get(org_key, False) or contactable
        if research_status:
            org_statuses.setdefault(org_key, set()).add(research_status)

        existing = orgs.get(org_key)
        priority_score = float(d["priority_score"] or 0.0)
        if existing is None or (
            _fit_rank(d["fit_bucket"]) < _fit_rank(existing.fit_bucket)
            or (
                _fit_rank(d["fit_bucket"]) == _fit_rank(existing.fit_bucket)
                and priority_score > existing.priority_score
            )
        ):
            org = ResearchOrg(
                org_key=org_key,
                org_name=(d["org_name"] or "").strip(),
                domain=domain_norm,
                website=(d["website"] or "").strip(),
                region=(d["region"] or "").strip(),
                city=(d["city"] or "").strip(),
                buyer_kind=(d["buyer_kind"] or "").strip(),
                organization_type_guess=(d["organization_type_guess"] or "").strip(),
                lead_type=(d["lead_type"] or "").strip(),
                source_name=(d["source_name"] or "").strip(),
                fit_bucket=(d["fit_bucket"] or "low_fit").strip(),
                priority_score=priority_score,
                lab_context_score=float(d["lab_context_score"] or 0.0),
                lab_context_tags=(d["lab_context_tags"] or "").strip(),
                equipment_match_tags=(d["equipment_match_tags"] or "").strip(),
                evidence_summary=(d["evidence_summary"] or "").strip(),
                lead_ids=(existing.lead_ids if existing else []),
            )
            if existing is not None:
                for tag in existing.equipment_match_tags.split(","):
                    t = tag.strip()
                    if t and t not in org.equipment_match_tags.split(","):
                        org.equipment_match_tags = (org.equipment_match_tags + "," + t).strip(",")
                for tag in existing.lab_context_tags.split(","):
                    t = tag.strip()
                    if t and t not in org.lab_context_tags.split(","):
                        org.lab_context_tags = (org.lab_context_tags + "," + t).strip(",")
            orgs[org_key] = org
        else:
            org = existing
            # Union structured evidence across this org's other tender rows.
            for tag in (d["equipment_match_tags"] or "").split(","):
                t = tag.strip()
                if t and t not in org.equipment_match_tags.split(","):
                    org.equipment_match_tags = (org.equipment_match_tags + "," + t).strip(",")
            for tag in (d["lab_context_tags"] or "").split(","):
                t = tag.strip()
                if t and t not in org.lab_context_tags.split(","):
                    org.lab_context_tags = (org.lab_context_tags + "," + t).strip(",")
        orgs[org_key].lead_ids.append(int(d["id"]))

    supplier_domains = supplier_email_domains(conn)
    suppressed_domains = load_suppressed_contact_domain_norms(conn)

    blocked_supplier = 0
    blocked_suppression = 0
    blocked_already_has_contact = 0
    blocked_noise = 0
    blocked_discarded = 0
    accepted: list[ResearchOrg] = []

    for org_key, org in orgs.items():
        if org_has_valid_lead_email.get(org_key) or org_has_researched_contact.get(org_key):
            blocked_already_has_contact += 1
            continue
        if org.domain and is_supplier_email_domain(f"x@{org.domain}", supplier_domains):
            blocked_supplier += 1
            continue
        if org.domain and email_domain_under_operator_domain_suppression(org.domain, suppressed_domains):
            blocked_suppression += 1
            continue
        if marketing_outreach_noise_organization_guess(org.org_name):
            blocked_noise += 1
            continue
        statuses = org_statuses.get(org_key, set())
        if not include_discarded and statuses and statuses == {_RESEARCH_DISCARDED_STATUS}:
            blocked_discarded += 1
            continue
        org.contact_research_status = ",".join(sorted(statuses))
        accepted.append(org)

    accepted.sort(
        key=lambda o: (_fit_rank(o.fit_bucket), _research_quality_tier(o), -o.priority_score, -o.lab_context_score)
    )
    final_queue = accepted[: max(0, int(limit))]

    stats = ResearchQueueStats(
        leads_scanned=leads_scanned,
        orgs_scanned=len(orgs),
        blocked_supplier=blocked_supplier,
        blocked_suppression=blocked_suppression,
        blocked_already_has_contact=blocked_already_has_contact,
        blocked_too_low_relevance_leads=blocked_low_relevance,
        blocked_noise=blocked_noise,
        blocked_discarded=blocked_discarded,
        final_queue_count=len(final_queue),
    )
    return final_queue, stats


def suggested_research_queries(org_name: str, domain: str) -> tuple[str, str, str, str]:
    """Search queries to find a laboratory/QA/QC/research contact at ``org_name``."""
    org = (org_name or "").strip()
    dom = (domain or "").strip().lower()
    q1 = f"{org} jefe de laboratorio contacto".strip()
    q2 = f"{org} laboratorio adquisiciones compras correo".strip()
    q3 = f"{org} investigador QA QC laboratorio equipo".strip()
    q4 = f"site:{dom} laboratorio contacto" if dom else ""
    return q1, q2, q3, q4


RESEARCH_QUEUE_FIELDNAMES: tuple[str, ...] = (
    "organization_name",
    "organization_domain",
    "website",
    "region",
    "city",
    "buyer_kind",
    "organization_type_guess",
    "fit_bucket",
    "priority_score",
    "lab_context_score",
    "lab_context_tags",
    "equipment_match_tags",
    "evidence_summary",
    "source_name",
    "lead_ids",
    "contact_research_status",
    "research_query_1",
    "research_query_2",
    "research_query_3",
    "research_query_4",
    "notes",
)


def research_org_to_row(org: ResearchOrg) -> dict[str, object]:
    q1, q2, q3, q4 = suggested_research_queries(org.org_name, org.domain)
    return {
        "organization_name": org.org_name,
        "organization_domain": org.domain,
        "website": org.website,
        "region": org.region,
        "city": org.city,
        "buyer_kind": org.buyer_kind,
        "organization_type_guess": org.organization_type_guess,
        "fit_bucket": org.fit_bucket,
        "priority_score": org.priority_score,
        "lab_context_score": org.lab_context_score,
        "lab_context_tags": org.lab_context_tags,
        "equipment_match_tags": org.equipment_match_tags,
        "evidence_summary": org.evidence_summary,
        "source_name": org.source_name,
        "lead_ids": ",".join(str(i) for i in org.lead_ids),
        "contact_research_status": org.contact_research_status,
        "research_query_1": q1,
        "research_query_2": q2,
        "research_query_3": q3,
        "research_query_4": q4,
        "notes": "",
    }
