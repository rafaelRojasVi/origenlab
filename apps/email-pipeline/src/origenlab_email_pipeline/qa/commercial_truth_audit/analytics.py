"""Classification, bounce/batch, product-interest, Labdelivery, tender, and sample analytics."""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from typing import Any

from origenlab_email_pipeline.qa.commercial_truth_audit.constants import (
    ALREADY_CONTACTED_BREAKDOWN,
    PRODUCT_CATEGORY_PATTERNS,
)
from origenlab_email_pipeline.qa.commercial_truth_audit.emails import (
    duplicate_email_stats,
    normalize_valid_email,
)
from origenlab_email_pipeline.qa.commercial_truth_audit.readonly import table_exists
from origenlab_email_pipeline.qa.commercial_truth_audit.redaction import (
    email_domain,
    redact_email,
)


def _pct(n: int, d: int) -> float:
    if d <= 0:
        return 0.0
    return round(100.0 * n / d, 2)


def build_classification_distribution(prospects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_class = Counter(str(p.get("classification") or "") for p in prospects)
    by_bucket = Counter(str(p.get("commercial_action_bucket") or "") for p in prospects)
    by_stage = Counter(str(p.get("audit_commercial_stage") or "") for p in prospects)
    by_rel = Counter(str(p.get("audit_relationship_state") or "") for p in prospects)
    by_safety = Counter(str(p.get("audit_safety_state") or "") for p in prospects)
    rows: list[dict[str, Any]] = []
    total = len(prospects) or 1
    for dim, counter in (
        ("classification", by_class),
        ("commercial_action_bucket", by_bucket),
        ("audit_commercial_stage", by_stage),
        ("audit_relationship_state", by_rel),
        ("audit_safety_state", by_safety),
    ):
        for value, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
            rows.append(
                {
                    "dimension": dim,
                    "value": value,
                    "count": count,
                    "pct": _pct(count, total),
                }
            )
    return rows


def build_classification_conflicts(prospects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows where existing bucket conflicts with audit-only commercial stage evidence."""
    rows: list[dict[str, Any]] = []
    for p in prospects:
        bucket = str(p.get("commercial_action_bucket") or "")
        stage = str(p.get("audit_commercial_stage") or "")
        breakdown = str(p.get("audit_already_contacted_breakdown") or "")
        conflict = ""
        if bucket == "already_contacted" and stage in {
            "quote_sent",
            "quote_requested",
            "purchase_pending",
            "fulfillment",
            "won",
            "technical_review",
            "qualifying",
        } and int(p.get("stage_is_current") or 0) == 1:
            conflict = "active_commercial_hidden_in_already_contacted"
        elif bucket == "already_contacted" and breakdown == "campaign_recipient_only":
            conflict = "campaign_recipient_labeled_already_contacted"
        elif bucket == "review_history" and stage in {"purchase_pending", "quote_sent", "fulfillment"} and int(p.get("stage_is_current") or 0) == 1:
            conflict = "active_commercial_hidden_in_review_history"
        elif bucket == "ready_to_contact" and p.get("audit_safety_state") in {"bounced", "suppressed", "blocked"}:
            conflict = "ready_to_contact_but_unsafe"
        elif bucket == "tender_opportunity" and normalize_valid_email(p.get("email")) is None:
            conflict = "tender_without_contact_email"
        if not conflict:
            continue
        rows.append(
            {
                "prospect_key": p.get("prospect_key") or "",
                "organization_name": p.get("organization_name") or "",
                "email": redact_email(p.get("email")),
                "classification": p.get("classification") or "",
                "commercial_action_bucket": bucket,
                "audit_commercial_stage": stage,
                "audit_relationship_state": p.get("audit_relationship_state") or "",
                "audit_safety_state": p.get("audit_safety_state") or "",
                "conflict_code": conflict,
                "notes": p.get("audit_commercial_stage_reason") or "",
            }
        )
    rows.sort(key=lambda r: (r["conflict_code"], r["prospect_key"], r["email"]))
    return rows


def build_already_contacted_breakdown(prospects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contacted = [p for p in prospects if str(p.get("commercial_action_bucket") or "") == "already_contacted"]
    counts = Counter(str(p.get("audit_already_contacted_breakdown") or "undetermined") for p in contacted)
    total = len(contacted)
    rows = []
    for key in ALREADY_CONTACTED_BREAKDOWN:
        c = counts.get(key, 0)
        rows.append(
            {
                "breakdown": key,
                "count": c,
                "pct_of_already_contacted": _pct(c, total),
                "already_contacted_total": total,
            }
        )
    # Include any unexpected keys.
    for key, c in sorted(counts.items()):
        if key in ALREADY_CONTACTED_BREAKDOWN:
            continue
        rows.append(
            {
                "breakdown": key,
                "count": c,
                "pct_of_already_contacted": _pct(c, total),
                "already_contacted_total": total,
            }
        )
    return rows


def build_opportunity_stage_candidates(prospects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for p in prospects:
        stage = str(p.get("audit_commercial_stage") or "")
        if stage in {"unknown", "database_only", "research_needed", "contact_planned"}:
            continue
        rows.append(
            {
                "prospect_key": p.get("prospect_key") or "",
                "organization_name": p.get("organization_name") or "",
                "email": redact_email(p.get("email")),
                "commercial_action_bucket": p.get("commercial_action_bucket") or "",
                "classification": p.get("classification") or "",
                "audit_commercial_stage": stage,
                "audit_commercial_stage_reason": p.get("audit_commercial_stage_reason") or "",
                "stage_evidence_type": p.get("stage_evidence_type") or "",
                "stage_evidence_at": p.get("stage_evidence_at") or "",
                "stage_evidence_source": p.get("stage_evidence_source") or "",
                "stage_confidence": p.get("stage_confidence") or "",
                "stage_is_current": p.get("stage_is_current") or 0,
                "audit_relationship_state": p.get("audit_relationship_state") or "",
                "audit_procurement_context": p.get("audit_procurement_context") or "",
                "commercial_deal_count": p.get("commercial_deal_count") or 0,
                "selected_commercial_deal_stage": p.get("selected_commercial_deal_stage") or "",
                "commercial_deal_stage_ambiguous": p.get("commercial_deal_stage_ambiguous") or 0,
                "quote_email_count_historical": int(p.get("quote_email_count") or 0),
                "purchase_email_count_historical": int(p.get("purchase_email_count") or 0),
                "invoice_email_count_historical": int(p.get("invoice_email_count") or 0),
                "gmail_sent_count": int(p.get("gmail_sent_count") or 0),
                "gmail_received_count": int(p.get("gmail_received_count") or 0),
            }
        )
    rows.sort(key=lambda r: (r["audit_commercial_stage"], r["prospect_key"], r["email"]))
    return rows


def build_open_thread_without_next_action(prospects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for p in prospects:
        sent = int(p.get("gmail_sent_count") or 0)
        received = int(p.get("gmail_received_count") or 0)
        if received <= 0:
            continue
        next_action = str(p.get("operational_next_action") or p.get("recommended_next_action") or "").strip()
        useful = next_action and next_action.lower() not in {
            "inspeccionar historial gmail/dominio antes de cualquier acción",
            "esperar respuesta; revisar follow-up después (no reenviar)",
            "",
        }
        # Open inbound thread buried in generic contacted/review without actionable next step.
        bucket = str(p.get("commercial_action_bucket") or "")
        if bucket in {"already_contacted", "review_history"} and not useful:
            rows.append(
                {
                    "prospect_key": p.get("prospect_key") or "",
                    "organization_name": p.get("organization_name") or "",
                    "email": redact_email(p.get("email")),
                    "commercial_action_bucket": bucket,
                    "gmail_sent_count": sent,
                    "gmail_received_count": received,
                    "operational_next_action": next_action,
                    "audit_commercial_stage": p.get("audit_commercial_stage") or "",
                    "notes": "Replied/inbound evidence without useful next-action guidance.",
                }
            )
    rows.sort(key=lambda r: (r["prospect_key"], r["email"]))
    return rows


def build_bounce_leakage(prospects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for p in prospects:
        safety = str(p.get("audit_safety_state") or "")
        bucket = str(p.get("commercial_action_bucket") or "")
        source_type = str(p.get("source_type") or "")
        leaked = safety in {"bounced", "suppressed"} and bucket in {
            "ready_to_contact",
            "needs_email_enrichment",
            "tender_opportunity",
            "already_contacted",
            "review_history",
        }
        campaignish = "campaign" in source_type or "manual_outreach" in str(p.get("classification") or "")
        if not (leaked or (campaignish and safety in {"bounced", "suppressed"})):
            # Still emit bounced rows for inventory of bounce state among prospects.
            if safety != "bounced":
                continue
        rows.append(
            {
                "prospect_key": p.get("prospect_key") or "",
                "email": redact_email(p.get("email")),
                "domain": p.get("domain") or email_domain(p.get("email")) or "",
                "commercial_action_bucket": bucket,
                "classification": p.get("classification") or "",
                "audit_safety_state": safety,
                "suppression_reason_code": p.get("suppression_reason_code") or "",
                "leakage_flag": int(leaked),
                "notes": "Audit-only; does not apply NDR or mutate suppression.",
            }
        )
    rows.sort(key=lambda r: (-r["leakage_flag"], r["prospect_key"], r["email"]))
    return rows


def build_prospect_source_batch_quality(prospects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate *prospect-source cohort* quality (NOT actual Gmail campaign recipients).

    Groups ``lead_research_prospect`` rows by ``dataset_label`` / ``source_type``.
    Missing emails are never counted as duplicates.
    """
    by_batch: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in prospects:
        label = str(p.get("dataset_label") or p.get("source_type") or "unknown_cohort")
        by_batch[label].append(p)
    rows = []
    for label, items in sorted(by_batch.items()):
        stats = duplicate_email_stats([p.get("email") for p in items])
        n = stats["row_count"]
        valid_n = stats["valid_email_row_count"]
        bounced = sum(1 for p in items if p.get("audit_safety_state") == "bounced")
        suppressed = sum(1 for p in items if p.get("audit_safety_state") == "suppressed")
        no_product = sum(
            1
            for p in items
            if not str(p.get("product_angle") or p.get("likely_need") or p.get("top_equipment_tags") or "").strip()
        )
        previously = sum(1 for p in items if p.get("audit_safety_state") == "previously_contacted")
        rows.append(
            {
                "cohort_label": label,
                "metric_scope": "prospect_source_cohort_not_gmail_campaign",
                "row_count": n,
                "valid_email_row_count": valid_n,
                "missing_email_count": stats["missing_email_count"],
                "invalid_email_count": stats["invalid_email_count"],
                "unique_valid_email_count": stats["unique_valid_email_count"],
                "duplicate_occurrence_count": stats["duplicate_occurrence_count"],
                "duplicate_rate_of_valid_email_rows": stats["duplicate_rate_of_valid_email_rows"],
                "hard_bounce_count_among_cohort_rows": bounced,
                "hard_bounce_rate_of_cohort_rows": _pct(bounced, n),
                "current_suppressed_count_among_cohort_rows": suppressed,
                "current_suppressed_rate_of_cohort_rows": _pct(suppressed, n),
                "missing_product_interest_count": no_product,
                "missing_product_interest_pct": _pct(no_product, n),
                "previously_contacted_count": previously,
                "missing_provenance_count": sum(
                    1 for p in items if not str(p.get("source") or p.get("source_type") or "").strip()
                ),
                "notes": "Cohort membership only; not proof of actual Sent-folder campaign recipients.",
            }
        )
    return rows


# Backward-compatible alias (deprecated name).
build_campaign_batch_quality = build_prospect_source_batch_quality


def infer_product_categories(text: str) -> list[str]:
    low = (text or "").lower()
    if not low.strip():
        return []
    hits = []
    for cat, patterns in PRODUCT_CATEGORY_PATTERNS.items():
        if any(p in low for p in patterns):
            hits.append(cat)
    return hits


def _has_explicit_product_interest(p: dict[str, Any]) -> bool:
    return bool(str(p.get("product_angle") or p.get("likely_need") or "").strip())


def _has_provenance(p: dict[str, Any]) -> bool:
    return bool(str(p.get("source") or p.get("source_type") or p.get("dataset_label") or "").strip())


def build_product_interest_inventory(prospects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for p in prospects:
        blob = " ".join(
            str(p.get(k) or "")
            for k in (
                "product_angle",
                "likely_need",
                "top_equipment_tags",
                "sector",
                "buyer_type",
                "campaign_bucket",
                "evidence_note",
            )
        )
        cats = infer_product_categories(blob)
        valid = normalize_valid_email(p.get("email"))
        rows.append(
            {
                "prospect_key": p.get("prospect_key") or "",
                "organization_name": p.get("organization_name") or "",
                "email": redact_email(p.get("email")),
                "role_title": p.get("role_title") or "",
                "product_families": "|".join(cats) if cats else "unknown",
                "evidence_text_present": int(bool(blob.strip())),
                "explicit_interest_reason": int(_has_explicit_product_interest(p)),
                "evidence_source": "prospect_fields+mart_tags",
                "confidence": "medium" if cats else "unknown",
                "previous_quotation_history": int(
                    int(p.get("quote_email_count") or 0) + int(p.get("quote_signal_count") or 0) > 0
                ),
                "previous_purchase_history": int(
                    int(p.get("purchase_email_count") or 0) + int(p.get("invoice_email_count") or 0) > 0
                ),
                "email_quality": "consumer"
                if p.get("is_consumer_email")
                else ("valid" if valid else ("invalid_or_missing")),
                "outreach_safety": p.get("audit_safety_state") or "unknown",
                "audit_commercial_stage": p.get("audit_commercial_stage") or "unknown",
                "stage_is_current": p.get("stage_is_current") or 0,
                "audit_procurement_context": p.get("audit_procurement_context") or "none",
            }
        )
    rows.sort(key=lambda r: (r["product_families"], r["prospect_key"], r["email"]))
    return rows


def build_batch_readiness(prospects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Product-category readiness using the intersection of safety + explicit interest.

    ``provisional_batch_ready_flag`` requires ``safe_ready_with_explicit_interest >= 3``
    (same contacts must satisfy all gates — disjoint groups do not pass).
    """
    rows = []
    for cat in sorted(PRODUCT_CATEGORY_PATTERNS):
        matched = []
        for p in prospects:
            blob = " ".join(
                str(p.get(k) or "")
                for k in ("product_angle", "likely_need", "top_equipment_tags", "sector", "buyer_type")
            )
            if cat in infer_product_categories(blob):
                matched.append(p)

        valid_email_contacts = [p for p in matched if normalize_valid_email(p.get("email"))]
        # Deduplicate by valid email within category candidate set.
        seen_emails: set[str] = set()
        unique_valid: list[dict[str, Any]] = []
        for p in valid_email_contacts:
            em = normalize_valid_email(p.get("email"))
            if em and em not in seen_emails:
                seen_emails.add(em)
                unique_valid.append(p)

        safe_ready = [
            p
            for p in unique_valid
            if p.get("audit_safety_state") == "eligible"
            and str(p.get("commercial_action_bucket") or "") == "ready_to_contact"
        ]
        with_explicit = [p for p in unique_valid if _has_explicit_product_interest(p)]
        intersection = [
            p
            for p in unique_valid
            if p.get("audit_safety_state") == "eligible"
            and str(p.get("commercial_action_bucket") or "") == "ready_to_contact"
            and _has_explicit_product_interest(p)
            and _has_provenance(p)
        ]
        threshold = 3
        rows.append(
            {
                "product_category": cat,
                "matched_contacts": len(matched),
                "valid_email_contacts": len(unique_valid),
                "safe_ready_contacts": len(safe_ready),
                "contacts_with_explicit_interest": len(with_explicit),
                "safe_ready_with_explicit_interest": len(intersection),
                "unique_ready_organizations": len(
                    {
                        str(p.get("organization_name") or "").lower()
                        for p in intersection
                        if p.get("organization_name")
                    }
                ),
                "missing_role_count": sum(1 for p in unique_valid if not str(p.get("role_title") or "").strip()),
                "missing_provenance_count": sum(1 for p in unique_valid if not _has_provenance(p)),
                "provisional_batch_ready_threshold": threshold,
                "provisional_batch_ready_flag": int(len(intersection) >= threshold),
                "provisional_batch_ready_definition": (
                    "safe_ready_with_explicit_interest >= threshold; "
                    "requires valid email + eligible safety + ready_to_contact + "
                    "explicit product/application reason + provenance; unknown interest excluded"
                ),
                "notes": "Intersection required; disjoint safe vs evidence groups do not pass.",
            }
        )
    rows.sort(key=lambda r: r["product_category"])
    return rows


def build_labdelivery_relationships(
    conn: sqlite3.Connection,
    prospects: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    # From prospect evidence first.
    for p in prospects:
        if not p.get("has_labdelivery_evidence") and "labdelivery" not in str(p.get("source_type") or "").lower():
            # Include historic gmail_historico with quote/purchase as recoverable relationships.
            if not (
                str(p.get("source_type") or "") in {"gmail_historico", "followup_antiguo", "caso_activo"}
                and (
                    int(p.get("quote_email_count") or 0)
                    + int(p.get("purchase_email_count") or 0)
                    + int(p.get("invoice_email_count") or 0)
                    > 0
                )
            ):
                continue
        rows.append(
            {
                "organization_name": p.get("organization_name") or "",
                "email": redact_email(p.get("email")),
                "domain": p.get("domain") or "",
                "relationship_kind": p.get("audit_relationship_state") or "known_labdelivery",
                "quote_evidence": int(int(p.get("quote_email_count") or 0) > 0),
                "purchase_invoice_evidence": int(
                    int(p.get("purchase_email_count") or 0) + int(p.get("invoice_email_count") or 0) > 0
                ),
                "active_in_origenlab_gmail": int(bool(p.get("has_origenlab_gmail_evidence"))),
                "days_since_last_seen": p.get("days_since_last_seen") if p.get("days_since_last_seen") is not None else "",
                "product_families": "|".join(
                    infer_product_categories(
                        " ".join(str(p.get(k) or "") for k in ("product_angle", "likely_need", "top_equipment_tags"))
                    )
                )
                or "unknown",
                "bounced_or_suppressed": int(p.get("audit_safety_state") in {"bounced", "suppressed"}),
                "notes": "Historical relationship candidate; not auto-outreach.",
            }
        )

    # Supplement from contact_master if emails table flags are sparse.
    if table_exists(conn, "contact_master") and not rows:
        for r in conn.execute(
            """
            SELECT email, domain, organization_name_guess, last_seen_at,
                   quote_email_count, invoice_email_count, purchase_email_count, top_equipment_tags
            FROM contact_master
            WHERE COALESCE(quote_email_count,0)+COALESCE(invoice_email_count,0)+COALESCE(purchase_email_count,0) > 0
            """
        ):
            rows.append(
                {
                    "organization_name": r[2] or "",
                    "email": redact_email(r[0]),
                    "domain": r[1] or "",
                    "relationship_kind": "unknown",
                    "quote_evidence": int(int(r[4] or 0) > 0),
                    "purchase_invoice_evidence": int(int(r[5] or 0) + int(r[6] or 0) > 0),
                    "active_in_origenlab_gmail": 0,
                    "days_since_last_seen": "",
                    "product_families": "|".join(infer_product_categories(str(r[7] or ""))) or "unknown",
                    "bounced_or_suppressed": 0,
                    "notes": "From contact_master commercial doc counts (Labdelivery vs OrigenLab not distinguished).",
                }
            )

    rows.sort(key=lambda r: (r["organization_name"], r["email"]))
    return rows


def build_tender_account_links(prospects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for p in prospects:
        if not (p.get("has_tender_evidence") or str(p.get("classification") or "") == "public_tender_review"):
            continue
        known = bool(
            p.get("has_origenlab_gmail_evidence")
            or p.get("has_labdelivery_evidence")
            or int(p.get("gmail_sent_count") or 0)
            + int(p.get("gmail_received_count") or 0)
            > 0
        )
        has_email = bool(normalize_valid_email(p.get("email")))
        conf = "high" if known and has_email else ("medium" if known or has_email else "low")
        rows.append(
            {
                "organization_name": p.get("organization_name") or "",
                "email": redact_email(p.get("email")),
                "domain": p.get("domain") or "",
                "buyer_type": p.get("buyer_type") or "",
                "product_or_application": p.get("likely_need") or p.get("product_angle") or "unknown",
                "known_in_gmail_or_labdelivery": int(known),
                "has_contact_email": int(has_email),
                "enrichment_required": int(not has_email),
                "audit_relationship_state": p.get("audit_relationship_state") or "",
                "audit_procurement_context": p.get("audit_procurement_context") or "none",
                "audit_commercial_stage": p.get("audit_commercial_stage") or "",
                "stage_is_current": p.get("stage_is_current") or 0,
                "active_opportunity_hint": int(
                    str(p.get("audit_commercial_stage") or "")
                    in {"quote_sent", "purchase_pending", "qualifying", "technical_review", "fulfillment"}
                    and int(p.get("stage_is_current") or 0) == 1
                ),
                "link_confidence": conf,
                "reason_code": p.get("audit_relationship_reason") or "tender_prospect_row",
                "notes": "Do not auto-classify as ready_to_contact. Procurement context is independent of relationship.",
            }
        )
    rows.sort(key=lambda r: (r["link_confidence"], r["organization_name"], r["email"]))
    return rows


_SAMPLE_CASES: tuple[tuple[str, Any], ...] = (
    ("dormant_labdelivery", lambda p: p.get("audit_relationship_state") in {"dormant_customer", "known_labdelivery"}),
    ("order_fulfillment", lambda p: p.get("audit_commercial_stage") == "fulfillment" and int(p.get("stage_is_current") or 0) == 1),
    ("customer_history", lambda p: p.get("audit_commercial_stage") == "customer_history"),
    ("existing_customer", lambda p: p.get("audit_relationship_state") == "existing_customer"),
    ("purchase_pending", lambda p: p.get("audit_commercial_stage") == "purchase_pending" and int(p.get("stage_is_current") or 0) == 1),
    ("quotation_sent", lambda p: p.get("audit_commercial_stage") == "quote_sent" and int(p.get("stage_is_current") or 0) == 1),
    ("quotation_requested", lambda p: p.get("audit_commercial_stage") == "quote_requested"),
    ("technical_review", lambda p: p.get("audit_commercial_stage") == "technical_review"),
    ("qualified_inbound_inquiry", lambda p: p.get("audit_commercial_stage") == "qualifying" or p.get("audit_already_contacted_breakdown") == "active_inquiry"),
    ("generic_campaign_recipient", lambda p: p.get("audit_already_contacted_breakdown") == "campaign_recipient_only"),
    ("sent_no_response", lambda p: p.get("audit_commercial_stage") == "contacted_no_reply"),
    ("net_new_verified_prospect", lambda p: p.get("audit_relationship_state") == "net_new" and p.get("audit_safety_state") == "eligible"),
    ("net_new_needs_enrichment", lambda p: str(p.get("commercial_action_bucket") or "") == "needs_email_enrichment"),
    ("tender_opportunity", lambda p: str(p.get("commercial_action_bucket") or "") == "tender_opportunity"),
    ("bounced_address", lambda p: p.get("audit_safety_state") == "bounced"),
    ("suppressed_or_blocked", lambda p: p.get("audit_safety_state") in {"suppressed", "blocked"}),
    ("conflicting_account_identity", lambda p: bool(p.get("_identity_conflict"))),
)


def build_operator_review_sample(
    prospects: list[dict[str, Any]],
    *,
    account_conflicts: list[dict[str, Any]],
    per_case: int = 3,
) -> list[dict[str, Any]]:
    conflict_domains = set()
    for c in account_conflicts:
        if c.get("conflict_type") == "domain_multiple_org_names":
            conflict_domains.add(str(c.get("domain") or "").lower())
    enriched = []
    for p in prospects:
        q = dict(p)
        q["_identity_conflict"] = str(q.get("domain") or "").lower() in conflict_domains
        enriched.append(q)

    rows: list[dict[str, Any]] = []
    used_keys: set[str] = set()
    for case_name, predicate in _SAMPLE_CASES:
        picked = 0
        for p in enriched:
            if picked >= per_case:
                break
            if not predicate(p):
                continue
            key = f"{p.get('prospect_key')}|{normalize_valid_email(p.get('email'))}"
            if key in used_keys:
                continue
            used_keys.add(key)
            picked += 1
            rows.append(
                {
                    "sample_case": case_name,
                    "prospect_key": p.get("prospect_key") or "",
                    "organization_name": p.get("organization_name") or "",
                    "email": redact_email(p.get("email")),
                    "classification": p.get("classification") or "",
                    "commercial_action_bucket": p.get("commercial_action_bucket") or "",
                    "operational_next_action": p.get("operational_next_action") or "",
                    "audit_relationship_state": p.get("audit_relationship_state") or "",
                    "audit_procurement_context": p.get("audit_procurement_context") or "",
                    "audit_commercial_stage": p.get("audit_commercial_stage") or "",
                    "stage_is_current": p.get("stage_is_current") or 0,
                    "stage_confidence": p.get("stage_confidence") or "",
                    "audit_safety_state": p.get("audit_safety_state") or "",
                    "audit_already_contacted_breakdown": p.get("audit_already_contacted_breakdown") or "",
                    "reason_codes": "|".join(
                        str(p.get(k) or "")
                        for k in (
                            "audit_relationship_reason",
                            "audit_procurement_reason",
                            "audit_commercial_stage_reason",
                            "audit_safety_reason",
                            "audit_already_contacted_breakdown_reason",
                        )
                        if p.get(k)
                    ),
                }
            )
    rows.sort(key=lambda r: (r["sample_case"], r["prospect_key"], r["email"]))
    return rows


def headline_metrics(
    prospects: list[dict[str, Any]],
    *,
    already_contacted_rows: list[dict[str, Any]],
    open_threads: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    bounce_rows: list[dict[str, Any]],
    cohort_rows: list[dict[str, Any]],
    labdelivery_rows: list[dict[str, Any]],
    tender_rows: list[dict[str, Any]],
    batch_readiness: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build headline metrics with confidence labels and corrected denominators.

    Prospect-source cohort duplicate/bounce rates are **not** Gmail campaign recipient rates.
    """
    contacted = [p for p in prospects if str(p.get("commercial_action_bucket") or "") == "already_contacted"]
    total_c = len(contacted)
    breakdown = {r["breakdown"]: r for r in already_contacted_rows}
    sent_only_as_opp = sum(
        1
        for p in contacted
        if p.get("audit_already_contacted_breakdown") == "campaign_recipient_only"
    )
    hidden_active = sum(1 for c in conflicts if c["conflict_code"] == "active_commercial_hidden_in_already_contacted")
    hidden_active += sum(1 for c in conflicts if c["conflict_code"] == "active_commercial_hidden_in_review_history")

    # Corrected cohort-level duplicate stats (valid emails only).
    cohort_rows_total = sum(int(r.get("row_count") or 0) for r in cohort_rows)
    cohort_valid = sum(int(r.get("valid_email_row_count") or 0) for r in cohort_rows)
    cohort_dup_occ = sum(int(r.get("duplicate_occurrence_count") or 0) for r in cohort_rows)
    cohort_missing = sum(int(r.get("missing_email_count") or 0) for r in cohort_rows)
    cohort_invalid = sum(int(r.get("invalid_email_count") or 0) for r in cohort_rows)
    cohort_bounce = sum(int(r.get("hard_bounce_count_among_cohort_rows") or 0) for r in cohort_rows)
    cohort_suppressed = sum(int(r.get("current_suppressed_count_among_cohort_rows") or 0) for r in cohort_rows)
    cohort_dup_rate = _pct(cohort_dup_occ, cohort_valid)
    cohort_bounce_rate = _pct(cohort_bounce, cohort_rows_total)

    current_fulfillment = sum(
        1
        for p in contacted
        if p.get("audit_already_contacted_breakdown") == "current_fulfillment_or_post_sale"
    )
    history_like = sum(
        1
        for p in contacted
        if p.get("audit_already_contacted_breakdown") == "customer_or_commercial_history"
    )

    def bp(key: str) -> float:
        return float(breakdown.get(key, {}).get("pct_of_already_contacted") or 0.0)

    def metric(value: Any, *, confidence: str, definition: str, denominator: Any = None) -> dict[str, Any]:
        out = {
            "value": value,
            "confidence": confidence,
            "definition": definition,
        }
        if denominator is not None:
            out["denominator"] = denominator
        return out

    return {
        "prospect_rows": metric(len(prospects), confidence="observed", definition="Count of lead_research_prospect rows after overlay."),
        "already_contacted_count": metric(
            total_c,
            confidence="observed",
            definition="Rows with commercial_action_bucket=already_contacted (existing overloaded bucket).",
        ),
        "already_contacted_campaign_recipient_only_pct": metric(
            bp("campaign_recipient_only"),
            confidence="heuristic",
            definition="Share of already_contacted with sent-only / no commercial-depth breakdown.",
            denominator=total_c,
        ),
        "already_contacted_active_inquiry_pct": metric(bp("active_inquiry"), confidence="heuristic", definition="Share classified active_inquiry.", denominator=total_c),
        "already_contacted_quotation_related_pct": metric(bp("quotation_related"), confidence="heuristic", definition="Share with current quotation-related stage.", denominator=total_c),
        "already_contacted_purchase_pending_pct": metric(bp("purchase_pending"), confidence="derived_medium_confidence", definition="Share with current purchase_pending stage evidence.", denominator=total_c),
        "already_contacted_existing_customer_pct": metric(bp("existing_customer"), confidence="derived_medium_confidence", definition="Share with existing_customer relationship.", denominator=total_c),
        "already_contacted_current_fulfillment_or_post_sale_pct": metric(
            bp("current_fulfillment_or_post_sale"),
            confidence="derived_high_confidence",
            definition="Share with current fulfilment/post-sale stage (dated deal or explicit logistics). Not lifetime invoice counts.",
            denominator=total_c,
        ),
        "already_contacted_customer_or_commercial_history_pct": metric(
            bp("customer_or_commercial_history"),
            confidence="derived_medium_confidence",
            definition="Share with historical customer/commercial evidence without proving current operational stage.",
            denominator=total_c,
        ),
        "already_contacted_dormant_pct": metric(bp("dormant"), confidence="heuristic", definition="Share dormant relationship breakdown.", denominator=total_c),
        "already_contacted_undetermined_pct": metric(bp("undetermined"), confidence="heuristic", definition="Share undetermined.", denominator=total_c),
        "current_fulfillment_count_among_already_contacted": metric(
            current_fulfillment,
            confidence="derived_high_confidence",
            definition="Count of already_contacted with current_fulfillment_or_post_sale breakdown.",
        ),
        "customer_or_commercial_history_count_among_already_contacted": metric(
            history_like,
            confidence="derived_medium_confidence",
            definition="Count of already_contacted with history-only commercial evidence.",
        ),
        "open_thread_without_next_action_count": metric(len(open_threads), confidence="heuristic", definition="Inbound evidence without useful next-action text."),
        "sent_only_treated_as_opportunity_count": metric(sent_only_as_opp, confidence="heuristic", definition="already_contacted rows that look campaign-recipient-only."),
        "active_cases_hidden_in_generic_buckets_count": metric(
            hidden_active,
            confidence="derived_medium_confidence",
            definition="Conflicts where current stage evidence is hidden under already_contacted/review_history.",
        ),
        "prospect_cohort_duplicate_rate_of_valid_email_rows": metric(
            cohort_dup_rate,
            confidence="derived_high_confidence",
            definition=(
                "duplicate_occurrence_count / valid_email_row_count across prospect-source cohorts "
                "(dataset_label/source_type). NOT an actual Gmail campaign recipient duplicate rate. "
                "Missing emails excluded."
            ),
            denominator=cohort_valid,
        ),
        "prospect_cohort_duplicate_occurrence_count": metric(cohort_dup_occ, confidence="derived_high_confidence", definition="sum(max(count-1,0)) over valid emails in cohorts."),
        "prospect_cohort_valid_email_row_count": metric(cohort_valid, confidence="observed", definition="Valid email rows across cohorts."),
        "prospect_cohort_missing_email_count": metric(cohort_missing, confidence="observed", definition="Missing-email cohort rows (not duplicates)."),
        "prospect_cohort_invalid_email_count": metric(cohort_invalid, confidence="observed", definition="Invalid nonempty email strings in cohorts."),
        "prospect_cohort_hard_bounce_rate_of_cohort_rows": metric(
            cohort_bounce_rate,
            confidence="derived_medium_confidence",
            definition="Share of cohort rows currently safety-state bounced (not proven pre-send leakage).",
            denominator=cohort_rows_total,
        ),
        "prospect_cohort_current_suppressed_count": metric(
            cohort_suppressed,
            confidence="observed",
            definition="Cohort rows with current suppression state. Not 'suppressed before send' unless temporal evidence exists.",
        ),
        "actual_gmail_campaign_duplicate_rate": metric(
            None,
            confidence="unavailable",
            definition=(
                "Actual Sent-folder campaign duplicate rate is not claimed from prospect cohorts. "
                "See sent_campaign_quality.csv when generated; otherwise unresolved limitation."
            ),
        ),
        "bounce_leakage_row_count": metric(
            sum(int(r.get("leakage_flag") or 0) for r in bounce_rows),
            confidence="heuristic",
            definition="Prospect rows where bounced/suppressed safety coexists with non-blocked buckets.",
        ),
        "labdelivery_unique_contacts": metric(
            len({r["email"] for r in labdelivery_rows if r.get("email")}),
            confidence="heuristic",
            definition="Distinct redacted contact keys in labdelivery relationship candidates.",
        ),
        "labdelivery_unique_orgs": metric(
            len({r["organization_name"].lower() for r in labdelivery_rows if r.get("organization_name")}),
            confidence="heuristic",
            definition="Distinct orgs in labdelivery relationship candidates.",
        ),
        "tender_rows_linked": metric(len(tender_rows), confidence="heuristic", definition="Tender prospect rows with account-link attempt."),
        "batch_readiness_categories_with_evidence": metric(
            sum(1 for r in batch_readiness if int(r["matched_contacts"]) > 0),
            confidence="heuristic",
            definition="Product categories with any keyword/evidence match.",
        ),
        "provisional_batch_ready_categories": metric(
            sum(1 for r in batch_readiness if int(r.get("provisional_batch_ready_flag") or 0) == 1),
            confidence="heuristic",
            definition="Categories where safe_ready_with_explicit_interest meets explicit threshold (intersection).",
        ),
        # Flat aliases for CLI headline printing (values only).
        "_flat": {
            "prospect_rows": len(prospects),
            "already_contacted_count": total_c,
            "already_contacted_campaign_recipient_only_pct": bp("campaign_recipient_only"),
            "already_contacted_current_fulfillment_or_post_sale_pct": bp("current_fulfillment_or_post_sale"),
            "already_contacted_customer_or_commercial_history_pct": bp("customer_or_commercial_history"),
            "active_cases_hidden_in_generic_buckets_count": hidden_active,
            "prospect_cohort_duplicate_rate_of_valid_email_rows": cohort_dup_rate,
            "prospect_cohort_current_suppressed_count": cohort_suppressed,
            "provisional_batch_ready_categories": sum(
                1 for r in batch_readiness if int(r.get("provisional_batch_ready_flag") or 0) == 1
            ),
        },
    }

