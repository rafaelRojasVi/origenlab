"""Campaign HTML recovery importer.

Recovers the actual sent HTML for each V1 campaign from the V1 Sent/Trash archive by
corroborating every send attempt's email address against the Sent copies found in the archive
(recipient lineage + timestamp + sender + exact subject).  The recovered variants are loaded
into ``outbound.campaign_content`` and ``outbound.campaign_content_message``.

Policy version: ``campaign-content-attribution/2026-09-30.v1``

Precedence order per accepted live attempt
-------------------------------------------
1. The attempt's ``gmail_message_id`` matches ``emails.message_id`` (RFC Message-ID) or, when
   the stored value is purely numeric, ``emails.id``.  Method: ``gmail_message_id``, confidence
   ``exact``.  On the real archive this yields 0 matches (the V1 provider resource id is not the
   RFC Message-ID); implement and report it anyway.
2. + 3. The attempt's ``email_norm`` is in the parsed ``To`` recipients of a Sent copy **and**
   the copy's ``From`` contains the campaign sender email **and** the subject is exactly equal
   **and** |Δt| ≤ 900 s → choose the unique nearest copy.  Method:
   ``recipient_lineage_timestamp``, confidence ``corroborated``.

Ambiguous (refuse the whole campaign with ``SupplementalRefused`` / ``ambiguous_attribution``):
- An attempt has ≥ 2 candidate copies with different normalized HTML hashes.
- A Sent copy is the nearest candidate for attempts of two different campaigns.
- A matched copy's ``From`` does not contain the campaign sender.

Subject alone never matches: a campaign whose attempts have no recipient match must come out
``not_recovered`` even though same-subject Sent copies exist.

Historical-draft hook
---------------------
Campaigns with zero accepted attempts come out ``never_sent``; they never receive ``sent_html``
rows.  If a future source provides an operator-supplied draft, pass ``--draft-html
<campaign_id>=<path>`` to record it as ``content_kind historical_draft``, ``method
unsent_draft``.  The hook is wired but currently unused as an explicit code path.

CLI
---
    campaign_content_recovery --sqlite <path> [--campaign-id X]...
        [--apply --expect-contents N --expect-messages M]
        [--record-ambiguous] [--plan-out <path>] [--target-dsn-env ORIGENLAB_V2_IMPORT_DSN]
        [--draft-html <campaign_id>=<html_file>]

Print JSON only; never print email addresses or message bodies.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.migration.readonly_source import open_source_readonly, single_read_transaction
from origenlab_email_pipeline.migration.v2_import.apply import ApplyRefused, _require_psycopg, assume_import_role
from origenlab_email_pipeline.migration.v2_import.target import LocalTarget, neutralized_libpq_environment, assert_local_target

ATTRIBUTION_POLICY_VERSION = "campaign-content-attribution/2026-09-30.v1"
WINDOW_SECONDS = 900
SOURCE_DB_SENT_FOLDERS = frozenset({"[Gmail]/Enviados", "[Gmail]/Papelera", "[Gmail]/Sent Mail"})


class SupplementalRefused(Exception):
    """Recovery was refused due to ambiguity, data inconsistency, or idempotency violation."""


# ---------------------------------------------------------------------------
# HTML normalization and hashing
# ---------------------------------------------------------------------------

def _normalize_html(html: str) -> str:
    """Collapse runs of whitespace to one space and trim."""
    return re.sub(r"\s+", " ", html).strip()


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _raw_sha256(html: str) -> str:
    return _sha256_hex(html.encode("utf-8", "surrogatepass"))


def _normalized_sha256(html: str) -> str:
    return _sha256_hex(_normalize_html(html).encode("utf-8", "surrogatepass"))


# ---------------------------------------------------------------------------
# Preheader extraction
# ---------------------------------------------------------------------------

_HIDDEN_STYLE = re.compile(
    r"""display\s*:\s*none|max-height\s*:\s*0|font-size\s*:\s*0|opacity\s*:\s*0""",
    re.IGNORECASE,
)
_PREHEADER_CLASS = re.compile(r"""class\s*=\s*["'][^"']*preheader[^"']*["']""", re.IGNORECASE)
_ELEMENT_WITH_CONTENT = re.compile(
    r"""<(?:span|div|td|p|table)[^>]*>([^<]{1,500})""",
    re.IGNORECASE | re.DOTALL,
)
_STYLE_ATTR = re.compile(r"""style\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_OPEN_TAG = re.compile(r"""<[a-zA-Z][^>]{0,2000}>""", re.DOTALL)


def _extract_preheader(html: str) -> str | None:
    """Extract the preheader text from the HTML, or return None."""
    for m in _OPEN_TAG.finditer(html):
        tag = m.group(0)
        is_hidden = False
        style_m = _STYLE_ATTR.search(tag)
        if style_m and _HIDDEN_STYLE.search(style_m.group(1)):
            is_hidden = True
        if not is_hidden and not _PREHEADER_CLASS.search(tag):
            continue
        # Look for text content just after the tag
        rest_start = m.end()
        # Find the next text content (before a nested tag or closing tag)
        text_end = min(rest_start + 500, len(html))
        snippet = html[rest_start:text_end]
        # Extract up to next opening or closing tag
        next_tag = re.search(r"<", snippet)
        if next_tag:
            snippet = snippet[: next_tag.start()]
        text = re.sub(r"\s+", " ", snippet).strip()
        if text:
            return text[:255]
    return None


# ---------------------------------------------------------------------------
# UTC datetime helpers
# ---------------------------------------------------------------------------

def _to_utc(iso_str: str) -> datetime:
    """Parse an ISO datetime string (with offset or Z) to a UTC-aware datetime."""
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc)


def _utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CampaignAttempt:
    attempt_id: int
    campaign_id: str
    email_norm: str
    attempted_at_utc: datetime
    gmail_message_id: str | None


@dataclass
class SentCopy:
    email_id: int
    message_id: str  # RFC Message-ID
    subject: str
    sender: str
    recipient: str  # single parsed To
    sent_at_utc: datetime
    archive_folder: str
    body_html: str
    raw_sha256: str
    normalized_sha256: str


@dataclass
class VariantPlan:
    variant_no: int
    normalized_sha256: str
    raw_sha256: str
    body_html: str
    message_count: int
    first_sent_at: str  # UTC ISO
    last_sent_at: str  # UTC ISO
    preheader_present: bool
    html_bytes: int
    preheader: str | None
    messages: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CampaignPlan:
    campaign_id: str
    name: str
    subject: str
    sender_email: str
    decision: str  # recoverable | never_sent | ambiguous | not_recovered
    accepted_attempts: int
    matched: int
    unmatched: int
    unmatched_reasons: dict[str, int]  # no_copy | copy_outside_window
    precedence1_matches: int
    variants: list[VariantPlan] = field(default_factory=list)
    stray_copies: int = 0
    manifest_sha256: str = ""
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        variants = []
        for v in self.variants:
            variants.append({
                "variant_no": v.variant_no,
                "normalized_sha256": v.normalized_sha256,
                "raw_sha256": v.raw_sha256,
                "message_count": v.message_count,
                "first_sent_at": v.first_sent_at,
                "last_sent_at": v.last_sent_at,
                "preheader_present": v.preheader_present,
                "html_bytes": v.html_bytes,
            })
        return {
            "campaign_id": self.campaign_id,
            "name": self.name,
            "decision": self.decision,
            "accepted_attempts": self.accepted_attempts,
            "matched": self.matched,
            "unmatched": self.unmatched,
            "unmatched_reasons": self.unmatched_reasons,
            "precedence1_matches": self.precedence1_matches,
            "variants": variants,
            "stray_copies": self.stray_copies,
            "manifest_sha256": self.manifest_sha256,
        }


# ---------------------------------------------------------------------------
# Extraction from V1 SQLite
# ---------------------------------------------------------------------------

def _load_campaigns(tx: Any, campaign_ids: list[str] | None) -> list[tuple[str, str, str, str]]:
    """Return (campaign_id, name, subject, sender_email) rows."""
    if campaign_ids:
        placeholders = ",".join("?" * len(campaign_ids))
        return tx.conn.execute(
            f"SELECT campaign_id, name, subject, sender_email FROM outbound_campaign "  # noqa: S608
            f"WHERE campaign_id IN ({placeholders}) ORDER BY campaign_id",
            campaign_ids,
        ).fetchall()
    return tx.conn.execute(
        "SELECT campaign_id, name, subject, sender_email FROM outbound_campaign ORDER BY campaign_id"
    ).fetchall()


def _load_attempts(tx: Any, campaign_id: str) -> list[CampaignAttempt]:
    """Load accepted live attempts for one campaign."""
    rows = tx.conn.execute(
        "SELECT id, campaign_id, email_norm, attempted_at, gmail_message_id "
        "FROM outbound_send_attempt "
        "WHERE campaign_id = ? AND result = 'accepted' AND mode = 'live' "
        "ORDER BY attempted_at",
        (campaign_id,),
    ).fetchall()
    return [
        CampaignAttempt(
            attempt_id=r[0],
            campaign_id=r[1],
            email_norm=r[2],
            attempted_at_utc=_to_utc(r[3]),
            gmail_message_id=r[4],
        )
        for r in rows
    ]


def _load_sent_copies(tx: Any, subject: str, window_start: datetime, window_end: datetime) -> list[SentCopy]:
    """Load emails rows matching the subject within the date window."""
    # Use date_iso index; we convert to UTC for comparison
    # We need to load all rows with the subject, then filter by date_iso
    rows = tx.conn.execute(
        "SELECT id, message_id, subject, sender, recipients, date_iso, folder, body_html "
        "FROM emails "
        "WHERE folder IN ('[Gmail]/Enviados', '[Gmail]/Papelera', '[Gmail]/Sent Mail') "
        "AND subject = ? "
        "ORDER BY date_iso",
        (subject,),
    ).fetchall()
    copies = []
    for r in rows:
        eid, message_id, subj, sender, recipient, date_iso, folder, body_html = r
        if not message_id or not date_iso or not body_html or not recipient:
            continue
        try:
            sent_at = _to_utc(date_iso)
        except (ValueError, TypeError):
            continue
        if sent_at < window_start or sent_at > window_end:
            continue
        copies.append(SentCopy(
            email_id=eid,
            message_id=message_id.strip(),
            subject=subj or "",
            sender=sender or "",
            recipient=recipient.strip().lower(),
            sent_at_utc=sent_at,
            archive_folder=folder,
            body_html=body_html,
            raw_sha256=_raw_sha256(body_html),
            normalized_sha256=_normalized_sha256(body_html),
        ))
    return copies


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------

def _sender_matches(sender: str, campaign_sender: str) -> bool:
    """Check whether the copy's From field contains the campaign sender email."""
    return campaign_sender.lower() in sender.lower()


def _attempt_window(attempt: CampaignAttempt) -> tuple[datetime, datetime]:
    delta = timedelta(seconds=WINDOW_SECONDS)
    return attempt.attempted_at_utc - delta, attempt.attempted_at_utc + delta


def _attribute_campaign(
    campaign_id: str,
    campaign_sender: str,
    attempts: list[CampaignAttempt],
    copies: list[SentCopy],
    copies_by_message_id: dict[str, SentCopy],
    copies_by_int_id: dict[int, SentCopy],
) -> tuple[str, list[VariantPlan], int, int, dict[str, int], int, int]:
    """Run attribution policy for one campaign.

    Returns (decision, variants, matched, unmatched, unmatched_reasons, precedence1_matches, stray_count)
    or raises SupplementalRefused(code=ambiguous_attribution, ...).
    """
    # Map attempt → chosen copy
    chosen: dict[int, SentCopy | None] = {}  # attempt_id → copy or None
    precedence1_matches = 0
    unmatched_reasons: dict[str, int] = {}

    # Build an index of copies by normalized hash for ambiguity check
    # copies_for_attempt: attempt_id → list of candidates in window with recipient match
    candidates_per_attempt: dict[int, list[SentCopy]] = {}

    for attempt in attempts:
        # Precedence 1: gmail_message_id
        gid = attempt.gmail_message_id
        p1_copy: SentCopy | None = None
        if gid:
            gid_stripped = gid.strip()
            if gid_stripped in copies_by_message_id:
                p1_copy = copies_by_message_id[gid_stripped]
            else:
                # Try numeric match
                try:
                    int_id = int(gid_stripped)
                    if int_id in copies_by_int_id:
                        p1_copy = copies_by_int_id[int_id]
                except ValueError:
                    pass
        if p1_copy is not None:
            if not _sender_matches(p1_copy.sender, campaign_sender):
                raise SupplementalRefused(
                    f"campaign {campaign_id!r}: attempt {attempt.attempt_id} matched by precedence-1 "
                    f"but the copy's From does not contain the campaign sender; "
                    f"code=ambiguous_attribution"
                )
            chosen[attempt.attempt_id] = p1_copy
            precedence1_matches += 1
            candidates_per_attempt[attempt.attempt_id] = [p1_copy]
            continue

        # Precedence 2+3: recipient + sender + subject + window
        t_start, t_end = _attempt_window(attempt)
        window_candidates = [
            c for c in copies
            if t_start <= c.sent_at_utc <= t_end
            and attempt.email_norm == c.recipient
            and _sender_matches(c.sender, campaign_sender)
        ]
        # Check ambiguity: ≥ 2 candidates with different normalized hashes
        if len(window_candidates) >= 2:
            hashes = {c.normalized_sha256 for c in window_candidates}
            if len(hashes) > 1:
                raise SupplementalRefused(
                    f"campaign {campaign_id!r}: attempt {attempt.attempt_id} has "
                    f"{len(window_candidates)} candidate copies with {len(hashes)} different "
                    f"normalized HTML hashes; code=ambiguous_attribution"
                )
        candidates_per_attempt[attempt.attempt_id] = window_candidates
        if not window_candidates:
            # Check whether a copy with the right recipient exists outside the window
            outside = [c for c in copies if attempt.email_norm == c.recipient]
            if outside:
                unmatched_reasons["copy_outside_window"] = unmatched_reasons.get("copy_outside_window", 0) + 1
            else:
                unmatched_reasons["no_copy"] = unmatched_reasons.get("no_copy", 0) + 1
            chosen[attempt.attempt_id] = None
        else:
            # Choose nearest
            best = min(window_candidates, key=lambda c: abs((c.sent_at_utc - attempt.attempted_at_utc).total_seconds()))
            chosen[attempt.attempt_id] = best

    # Cross-campaign ambiguity: check that no copy is the nearest candidate for two different campaigns
    # (this check is global; here we just track which copies were chosen)
    matched_copy_ids = {c.email_id for c in chosen.values() if c is not None}

    # Check: if no recipient match at all (all unmatched because no_copy or copy_outside_window),
    # the campaign is not_recovered (not ambiguous)
    matched_count = sum(1 for c in chosen.values() if c is not None)
    unmatched_count = sum(1 for c in chosen.values() if c is None)

    if matched_count == 0:
        # never_sent was already handled upstream; this is not_recovered
        return "not_recovered", [], 0, unmatched_count, unmatched_reasons, precedence1_matches, 0

    # Build variants: group by normalized_sha256, ordered by first sent time
    variant_map: dict[str, list[tuple[CampaignAttempt, SentCopy]]] = {}
    for attempt in attempts:
        copy = chosen.get(attempt.attempt_id)
        if copy is None:
            continue
        h = copy.normalized_sha256
        if h not in variant_map:
            variant_map[h] = []
        variant_map[h].append((attempt, copy))

    # Order variants by earliest first_sent_at
    variant_order = sorted(
        variant_map.keys(),
        key=lambda h: min(c.sent_at_utc for _, c in variant_map[h]),
    )

    variants = []
    all_chosen_copy_ids: set[int] = set()
    for vno, h in enumerate(variant_order, start=1):
        pairs = variant_map[h]
        copy_example = pairs[0][1]
        sent_times = sorted(c.sent_at_utc for _, c in pairs)
        preheader = _extract_preheader(copy_example.body_html)
        # Build per-message records
        messages = []
        for attempt, copy in pairs:
            delta_s = int(round((copy.sent_at_utc - attempt.attempted_at_utc).total_seconds()))
            messages.append({
                "rfc822_message_id": copy.message_id,
                "v1_gmail_message_id": attempt.gmail_message_id,
                "v1_attempt_id": attempt.attempt_id,
                "sent_at": _utc_iso(copy.sent_at_utc),
                "matched_delta_seconds": delta_s,
                "archive_folder": copy.archive_folder,
                "email_id": copy.email_id,  # internal ref, not stored in PG
                "email_norm": attempt.email_norm,  # internal ref, not stored in PG
                "attempted_at": _utc_iso(attempt.attempted_at_utc),  # internal ref
            })
            all_chosen_copy_ids.add(copy.email_id)
        vp = VariantPlan(
            variant_no=vno,
            normalized_sha256=h,
            raw_sha256=copy_example.raw_sha256,
            body_html=copy_example.body_html,
            message_count=len(pairs),
            first_sent_at=_utc_iso(sent_times[0]),
            last_sent_at=_utc_iso(sent_times[-1]),
            preheader_present=preheader is not None,
            html_bytes=len(copy_example.body_html.encode("utf-8", "surrogatepass")),
            preheader=preheader,
            messages=messages,
        )
        variants.append(vp)

    # Stray copies: copies with this subject in the window that were not chosen by any attempt
    stray_count = sum(1 for c in copies if c.email_id not in all_chosen_copy_ids)

    return "recoverable", variants, matched_count, unmatched_count, unmatched_reasons, precedence1_matches, stray_count


# ---------------------------------------------------------------------------
# Plan computation
# ---------------------------------------------------------------------------

def _manifest_for_plan(plan: CampaignPlan) -> dict[str, Any]:
    """Build the manifest payload for one recoverable campaign."""
    return {
        "manifest_version": ATTRIBUTION_POLICY_VERSION,
        "v1_campaign_id": plan.campaign_id,
        "name": plan.name,
        "subject": plan.subject,
        "decision": plan.decision,
        "accepted_attempts": plan.accepted_attempts,
        "matched": plan.matched,
        "unmatched": plan.unmatched,
        "variants": len(plan.variants),
        "variant_normalized_hashes": [v.normalized_sha256 for v in plan.variants],
    }


def _plan_sha256(manifest: dict[str, Any]) -> str:
    return _sha256_hex(
        json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode("utf-8")
    )


def _dedupe_key(campaign_id: str, manifest_sha256: str) -> str:
    return f"migration_manifest:campaign-content-recovery:{campaign_id}:{manifest_sha256[:32]}"


def build_plan(
    sqlite_path: Path,
    campaign_ids: list[str] | None = None,
    draft_html: dict[str, Path] | None = None,
) -> list[CampaignPlan]:
    """Read the V1 SQLite (read-only) and produce one plan per campaign.

    No database connection is required; this is a pure dry-run plan.
    No addresses are included in the returned structures.

    Stray copies are computed globally: a copy is stray for a campaign when it falls within
    that campaign's window but was not chosen by any campaign (including campaigns that share
    the same subject and send window).
    """
    conn = open_source_readonly(sqlite_path)
    try:
        with single_read_transaction(conn) as tx:
            campaigns = _load_campaigns(tx, campaign_ids)
            plans: list[CampaignPlan] = []
            # Cross-campaign tracking: email_id → campaign_id that claimed it.
            global_chosen_copy_ids: dict[int, str] = {}
            # Per-campaign copies list for the second-pass stray computation.
            plan_copies: list[tuple[CampaignPlan, list[SentCopy]]] = []

            for campaign_id, name, subject, sender_email in campaigns:
                attempts = _load_attempts(tx, campaign_id)
                if not attempts:
                    # Check for historical draft
                    if draft_html and campaign_id in draft_html:
                        plan = _build_draft_plan(campaign_id, name, subject, sender_email, draft_html[campaign_id])
                    else:
                        plan = CampaignPlan(
                            campaign_id=campaign_id, name=name, subject=subject, sender_email=sender_email,
                            decision="never_sent", accepted_attempts=0, matched=0, unmatched=0,
                            unmatched_reasons={}, precedence1_matches=0,
                        )
                    plans.append(plan)
                    continue

                # Compute time window for email query
                all_times = [a.attempted_at_utc for a in attempts]
                window_start = min(all_times) - timedelta(days=1)
                window_end = max(all_times) + timedelta(days=1)

                copies = _load_sent_copies(tx, subject, window_start, window_end)
                # Build lookup indexes
                copies_by_message_id = {c.message_id: c for c in copies}
                copies_by_int_id: dict[int, SentCopy] = {}
                for c in copies:
                    try:
                        copies_by_int_id[int(c.message_id)] = c
                    except (ValueError, TypeError):
                        pass

                try:
                    decision, variants, matched, unmatched, unmatched_reasons, p1_matches, _ = _attribute_campaign(
                        campaign_id, sender_email, attempts, copies,
                        copies_by_message_id, copies_by_int_id,
                    )
                except SupplementalRefused as exc:
                    plan = CampaignPlan(
                        campaign_id=campaign_id, name=name, subject=subject, sender_email=sender_email,
                        decision="ambiguous", accepted_attempts=len(attempts), matched=0, unmatched=0,
                        unmatched_reasons={}, precedence1_matches=0, error=str(exc),
                    )
                    plans.append(plan)
                    continue

                # Cross-campaign ambiguity: check that no copy is claimed by two campaigns
                for v in variants:
                    for msg in v.messages:
                        cid_prev = global_chosen_copy_ids.get(msg["email_id"])
                        if cid_prev is not None and cid_prev != campaign_id:
                            plan = CampaignPlan(
                                campaign_id=campaign_id, name=name, subject=subject, sender_email=sender_email,
                                decision="ambiguous", accepted_attempts=len(attempts), matched=0, unmatched=0,
                                unmatched_reasons={}, precedence1_matches=p1_matches,
                                error=(
                                    f"copy (email_id={msg['email_id']}) is already claimed by campaign {cid_prev!r}; "
                                    "code=ambiguous_attribution"
                                ),
                            )
                            plans.append(plan)
                            break
                    else:
                        continue
                    break  # inner loop broke → campaign is ambiguous
                else:
                    # Register all chosen copies for this campaign
                    for v in variants:
                        for msg in v.messages:
                            global_chosen_copy_ids[msg["email_id"]] = campaign_id

                    plan = CampaignPlan(
                        campaign_id=campaign_id, name=name, subject=subject, sender_email=sender_email,
                        decision=decision,
                        accepted_attempts=len(attempts), matched=matched, unmatched=unmatched,
                        unmatched_reasons=unmatched_reasons, precedence1_matches=p1_matches,
                        variants=variants, stray_copies=0,  # computed in second pass
                    )
                    if decision == "recoverable":
                        manifest = _manifest_for_plan(plan)
                        plan.manifest_sha256 = _plan_sha256(manifest)
                    plans.append(plan)
                    plan_copies.append((plan, copies))

    finally:
        conn.close()

    # Second pass: compute stray globally — a copy is stray when it was not chosen by ANY campaign.
    for plan, copies in plan_copies:
        plan.stray_copies = sum(1 for c in copies if c.email_id not in global_chosen_copy_ids)

    return plans


def _build_draft_plan(
    campaign_id: str, name: str, subject: str, sender_email: str, draft_path: Path
) -> CampaignPlan:
    """Build a plan for a never-sent campaign with an operator-provided draft HTML file.

    This hook exists for future use; it is not invoked automatically.
    """
    html = draft_path.read_text(encoding="utf-8", errors="surrogatepass")
    preheader = _extract_preheader(html)
    variant = VariantPlan(
        variant_no=1,
        normalized_sha256=_normalized_sha256(html),
        raw_sha256=_raw_sha256(html),
        body_html=html,
        message_count=0,
        first_sent_at="",  # never sent
        last_sent_at="",
        preheader_present=preheader is not None,
        html_bytes=len(html.encode("utf-8", "surrogatepass")),
        preheader=preheader,
        messages=[],
    )
    plan = CampaignPlan(
        campaign_id=campaign_id, name=name, subject=subject, sender_email=sender_email,
        decision="recoverable",  # will be stored as historical_draft
        accepted_attempts=0, matched=0, unmatched=0, unmatched_reasons={},
        precedence1_matches=0, variants=[variant],
    )
    manifest = _manifest_for_plan(plan)
    plan.manifest_sha256 = _plan_sha256(manifest)
    return plan


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def _total_contents(plans: list[CampaignPlan]) -> int:
    return sum(len(p.variants) for p in plans if p.decision in ("recoverable",))


def _total_messages(plans: list[CampaignPlan]) -> int:
    return sum(
        sum(len(v.messages) for v in p.variants)
        for p in plans if p.decision == "recoverable"
    )


@dataclass
class RecoveryResult:
    dry_run: bool
    target: str
    plans: list[dict[str, Any]]
    inserted: dict[str, int] = field(default_factory=dict)
    already_present: list[str] = field(default_factory=list)
    attempts_unlinked: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "target": self.target,
            "inserted": self.inserted,
            "already_present": self.already_present,
            "attempts_unlinked": self.attempts_unlinked,
        }


def apply_recovery(
    plans: list[CampaignPlan],
    target: LocalTarget,
    *,
    dry_run: bool = True,
    expect_contents: int | None = None,
    expect_messages: int | None = None,
    record_ambiguous: bool = False,
) -> RecoveryResult:
    """Apply the recovery plan into ``target`` in one transaction.

    Rolls back when ``dry_run`` is True.
    """
    psycopg = _require_psycopg()

    # Refuse if any plan is ambiguous (unless record_ambiguous)
    ambiguous = [p for p in plans if p.decision == "ambiguous"]
    if ambiguous and not record_ambiguous:
        raise ApplyRefused(
            f"{len(ambiguous)} campaign(s) have ambiguous attribution: "
            + ", ".join(p.campaign_id for p in ambiguous)
            + ". Pass --record-ambiguous to write them as source_records with decision='ambiguous' and no content."
        )

    recoverable = [p for p in plans if p.decision == "recoverable"]

    # Check --expect-* counts
    if expect_contents is not None:
        actual_contents = _total_contents(recoverable)
        if actual_contents != expect_contents:
            raise ApplyRefused(
                f"--expect-contents={expect_contents} but plan has {actual_contents} content rows; refusing"
            )
    if expect_messages is not None:
        actual_messages = _total_messages(recoverable)
        if actual_messages != expect_messages:
            raise ApplyRefused(
                f"--expect-messages={expect_messages} but plan has {actual_messages} message rows; refusing"
            )

    result = RecoveryResult(dry_run=dry_run, target=target.redacted(), plans=[p.as_dict() for p in plans])
    inserted: dict[str, int] = {
        "evidence.source_record": 0,
        "outbound.campaign_content": 0,
        "outbound.campaign_content_message": 0,
    }
    already_present: list[str] = []
    attempts_unlinked = 0

    all_plans = recoverable + (ambiguous if record_ambiguous else [])

    with neutralized_libpq_environment(), psycopg.connect(target.dsn, autocommit=False) as conn:
        assume_import_role(conn)
        try:
            with conn.cursor() as cur:
                for plan in all_plans:
                    manifest = _manifest_for_plan(plan)
                    manifest["decision"] = plan.decision
                    sha = _plan_sha256(manifest)
                    dedupe_key = _dedupe_key(plan.campaign_id, sha)

                    # Check idempotency
                    cur.execute(
                        "SELECT id, payload_sha256 FROM evidence.source_record WHERE dedupe_key = %s",
                        (dedupe_key,),
                    )
                    found = cur.fetchone()

                    if found is not None:
                        stored_sha = found[1]
                        if stored_sha != sha:
                            raise ApplyRefused(
                                f"campaign {plan.campaign_id!r}: stored manifest hash {stored_sha!r} "
                                f"differs from current {sha!r}; refusing (manifest_changed)"
                            )
                        # Check that content rows already exist with the expected counts
                        src_id = found[0]
                        cur.execute(
                            "SELECT COUNT(*) FROM outbound.campaign_content WHERE origin_source_record_id = %s",
                            (src_id,),
                        )
                        existing_content = cur.fetchone()[0]
                        if existing_content != len(plan.variants):
                            raise ApplyRefused(
                                f"campaign {plan.campaign_id!r}: source_record exists but content row count "
                                f"mismatch (stored={existing_content}, plan={len(plan.variants)}); refusing"
                            )
                        already_present.append(plan.campaign_id)
                        continue

                    # Insert evidence.source_record
                    cur.execute(
                        "INSERT INTO evidence.source_record "
                        "(kind, dedupe_key, payload, payload_sha256, review_status) "
                        "VALUES ('migration_manifest', %s, %s, %s, 'pending') RETURNING id",
                        (dedupe_key, json.dumps({**manifest, "sha256": sha}), sha),
                    )
                    source_record_id = cur.fetchone()[0]
                    inserted["evidence.source_record"] += 1

                    if plan.decision == "ambiguous":
                        # Write only the source_record for ambiguous campaigns
                        continue

                    # Resolve V2 campaign_id by origin manifest payload->>'v1_campaign_id'
                    cur.execute(
                        "SELECT c.id FROM outbound.campaign c "
                        "JOIN evidence.source_record s ON s.id = c.origin_source_record_id "
                        "WHERE s.kind = 'migration_manifest' "
                        "AND s.payload->>'v1_campaign_id' = %s",
                        (plan.campaign_id,),
                    )
                    campaign_row = cur.fetchone()
                    if campaign_row is None:
                        # Fallback: match by name + sender mailbox
                        cur.execute(
                            "SELECT c.id FROM outbound.campaign c "
                            "JOIN comms.mailbox m ON m.id = c.mailbox_id "
                            "WHERE c.name = %s AND m.address_norm = %s "
                            "LIMIT 1",
                            (plan.name, plan.sender_email),
                        )
                        campaign_row = cur.fetchone()
                    v2_campaign_id = campaign_row[0] if campaign_row else None

                    is_draft = (plan.accepted_attempts == 0)
                    content_kind = "historical_draft" if is_draft else "sent_html"

                    for variant in plan.variants:
                        cur.execute(
                            "INSERT INTO outbound.campaign_content "
                            "(campaign_id, content_kind, variant_no, subject, preheader, body_html, "
                            " body_html_sha256, body_html_normalized_sha256, message_count, "
                            " first_sent_at, last_sent_at, attribution_method, attribution_confidence, "
                            " attribution_policy_version, unmatched_attempt_count, origin_source_record_id) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                            "RETURNING id",
                            (
                                v2_campaign_id,
                                content_kind,
                                variant.variant_no,
                                plan.subject,
                                variant.preheader,
                                variant.body_html,
                                variant.raw_sha256,
                                variant.normalized_sha256,
                                variant.message_count,
                                variant.first_sent_at if variant.first_sent_at else None,
                                variant.last_sent_at if variant.last_sent_at else None,
                                "gmail_message_id" if plan.precedence1_matches == plan.matched and plan.precedence1_matches > 0 else "recipient_lineage_timestamp",
                                "exact" if plan.precedence1_matches == plan.matched and plan.precedence1_matches > 0 else "corroborated",
                                ATTRIBUTION_POLICY_VERSION,
                                plan.unmatched,
                                source_record_id,
                            ),
                        )
                        content_id = cur.fetchone()[0]
                        inserted["outbound.campaign_content"] += 1

                        for msg in variant.messages:
                            # Resolve send_attempt_id in V2
                            send_attempt_id = None
                            if v2_campaign_id is not None:
                                cur.execute(
                                    "SELECT id FROM outbound.send_attempt "
                                    "WHERE campaign_id = %s AND address_norm = %s AND accepted_at = %s "
                                    "LIMIT 1",
                                    (v2_campaign_id, msg["email_norm"], msg["attempted_at"]),
                                )
                                sa_row = cur.fetchone()
                                if sa_row:
                                    send_attempt_id = sa_row[0]
                                else:
                                    attempts_unlinked += 1
                            else:
                                attempts_unlinked += 1

                            cur.execute(
                                "INSERT INTO outbound.campaign_content_message "
                                "(campaign_content_id, send_attempt_id, rfc822_message_id, "
                                " v1_gmail_message_id, v1_attempt_id, sent_at, "
                                " matched_delta_seconds, archive_folder) "
                                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                                (
                                    content_id,
                                    send_attempt_id,
                                    msg["rfc822_message_id"],
                                    msg["v1_gmail_message_id"],
                                    msg["v1_attempt_id"],
                                    msg["sent_at"],
                                    msg["matched_delta_seconds"],
                                    msg["archive_folder"],
                                ),
                            )
                            inserted["outbound.campaign_content_message"] += 1

        except Exception:
            conn.rollback()
            raise
        if dry_run:
            conn.rollback()
        else:
            conn.commit()

    result.inserted = inserted
    result.already_present = already_present
    result.attempts_unlinked = attempts_unlinked
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    import argparse
    import os

    ap = argparse.ArgumentParser(description="Recover sent HTML from V1 Sent archive into outbound.campaign_content")
    ap.add_argument("--sqlite", required=True, type=Path, help="Path to V1 emails.sqlite (opened read-only)")
    ap.add_argument("--campaign-id", action="append", dest="campaign_ids", metavar="ID",
                    help="Campaign(s) to process; default: all in outbound_campaign")
    ap.add_argument("--target-dsn-env", default="ORIGENLAB_V2_IMPORT_DSN",
                    help="Name of the env var holding the loopback target DSN")
    ap.add_argument("--apply", action="store_true", help="Commit; without it the transaction is rolled back")
    ap.add_argument("--expect-contents", type=int, metavar="N",
                    help="Refuse unless plan has exactly N campaign_content rows")
    ap.add_argument("--expect-messages", type=int, metavar="M",
                    help="Refuse unless plan has exactly M campaign_content_message rows")
    ap.add_argument("--record-ambiguous", action="store_true",
                    help="Write source_records for ambiguous campaigns (no content rows); default refuses")
    ap.add_argument("--plan-out", type=Path, metavar="PATH",
                    help="Write the plan JSON (no addresses) to this file")
    ap.add_argument("--draft-html", action="append", metavar="CAMPAIGN_ID=PATH",
                    help="Historical draft for a never-sent campaign: CAMPAIGN_ID=path/to/file.html")
    args = ap.parse_args(argv)

    draft_html: dict[str, Path] = {}
    for spec in args.draft_html or []:
        if "=" not in spec:
            ap.error(f"--draft-html must be CAMPAIGN_ID=PATH, got: {spec!r}")
        cid, _, pth = spec.partition("=")
        draft_html[cid.strip()] = Path(pth.strip())

    plans = build_plan(args.sqlite, args.campaign_ids, draft_html or None)

    plan_dicts = [p.as_dict() for p in plans]
    summary = {
        "total_recoverable": sum(1 for p in plans if p.decision == "recoverable"),
        "total_never_sent": sum(1 for p in plans if p.decision == "never_sent"),
        "total_ambiguous": sum(1 for p in plans if p.decision == "ambiguous"),
        "total_not_recovered": sum(1 for p in plans if p.decision == "not_recovered"),
        "total_content_rows": _total_contents(plans),
        "total_message_rows": _total_messages(plans),
    }

    if args.plan_out:
        args.plan_out.parent.mkdir(parents=True, exist_ok=True)
        args.plan_out.write_text(
            json.dumps({"summary": summary, "campaigns": plan_dicts}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    if args.apply or True:  # always need DSN when apply requested
        pass

    if args.apply:
        target = assert_local_target(os.environ[args.target_dsn_env])
        apply_result = apply_recovery(
            plans, target,
            dry_run=False,
            expect_contents=args.expect_contents,
            expect_messages=args.expect_messages,
            record_ambiguous=args.record_ambiguous,
        )
        output = {
            "summary": summary,
            "campaigns": plan_dicts,
            **apply_result.as_dict(),
        }
    else:
        output = {
            "summary": summary,
            "campaigns": plan_dicts,
            "dry_run": True,
            "--expect-contents": _total_contents(plans),
            "--expect-messages": _total_messages(plans),
        }

    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
