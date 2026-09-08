"""Deterministic, conservative reply matching — no thread/message-ID linkage
exists anywhere in the archive (design §2), so this is built entirely from
customer identity + bounded chronology + normalized-subject overlap, exactly
the fallback path the spec anticipated. Reuses the auto-reply and bounce
detectors already proven in warm_case_sender_rules.py / cases_review_queue.py
instead of reimplementing them.

Sent-folder exclusion uses `candidates.is_sent_folder` rather than an
exact-literal `SENT_FOLDERS` check: the legacy mbox archive stores its Sent
folder as a full absolute path baked in at ingestion time (matched by
trailing-segment suffix, not exact literal — see candidates.py's module
docstring), so a `folder NOT IN (SENT_FOLDERS)` SQL clause would silently
let legacy-archive Sent rows leak into the reply candidate set. `is_sent_folder`
was added to candidates.py specifically for this purpose during Task 2's fix
round (see task-2-report.md), so it is applied as a post-fetch filter here
instead of reconstructing the suffix-match logic in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import sqlite3
from typing import Literal

from origenlab_email_pipeline.cases_review_queue import looks_like_obvious_noise
from origenlab_email_pipeline.historical_quote_register.candidates import is_sent_folder
from origenlab_email_pipeline.warm_case_sender_rules import looks_like_auto_reply_text

ResponseState = Literal["no_reply", "replied", "auto_reply", "bounced", "unknown"]

_REPLY_PREFIX_RE = re.compile(r"^(re|rv|fwd|aw)\s*:\s*", re.IGNORECASE)


def _normalize_subject_core(subject: str | None) -> str:
    s = (subject or "").strip().lower()
    while True:
        stripped = _REPLY_PREFIX_RE.sub("", s)
        if stripped == s:
            break
        s = stripped
    return re.sub(r"[^a-z0-9]+", "", s)


@dataclass(frozen=True)
class ReplyCandidateRow:
    email_id: int
    sender: str | None
    subject: str | None
    date_iso: str | None
    body_snippet: str | None


@dataclass(frozen=True)
class ResponseMatchResult:
    response_state: ResponseState
    first_reply_email_id: int | None
    first_reply_at: str | None
    latest_reply_email_id: int | None
    latest_reply_at: str | None
    latest_reply_subject: str | None
    review_required: bool
    review_reason: str | None


def find_candidate_replies(
    conn: sqlite3.Connection,
    *,
    customer_contact_email: str,
    sent_at_iso: str,
    window_end_iso: str | None,
) -> list[ReplyCandidateRow]:
    """SQL-bounded (indexed `date_iso`) scan for one customer address' mail
    after `sent_at_iso` and before `window_end_iso` — never a full-mailbox
    scan. Sent folders (both Gmail and legacy-mbox) are excluded via
    `is_sent_folder` after the bounded fetch (see module docstring)."""
    like_pattern = f"%{customer_contact_email.strip().lower()}%"
    params: list[object] = [like_pattern, sent_at_iso]
    window_clause = ""
    if window_end_iso is not None:
        window_clause = "AND date_iso < ?"
        params.append(window_end_iso)
    rows = conn.execute(
        f"""
        SELECT id, folder, sender, subject, date_iso, top_reply_clean
        FROM emails
        WHERE lower(sender) LIKE ?
          AND date_iso > ?
          {window_clause}
        ORDER BY date_iso ASC
        """,
        params,
    ).fetchall()
    return [
        ReplyCandidateRow(email_id=r[0], sender=r[2], subject=r[3], date_iso=r[4], body_snippet=r[5])
        for r in rows
        if not is_sent_folder(r[1])
    ]


def classify_response(
    candidates: list[ReplyCandidateRow],
    *,
    quote_number: str | None,
    normalized_quote_subject_core: str | None,
) -> ResponseMatchResult:
    if not candidates:
        return ResponseMatchResult(
            response_state="no_reply", first_reply_email_id=None, first_reply_at=None,
            latest_reply_email_id=None, latest_reply_at=None, latest_reply_subject=None,
            review_required=False, review_reason=None,
        )

    # Classify every candidate first — never return on the first noise/
    # auto-reply hit. Candidates arrive in ascending-date order (see
    # find_candidate_replies), so an early out-of-office auto-reply followed
    # later by a genuine human reply must still surface as "replied": a
    # premature return here would report "auto_reply"/"bounced" and silently
    # discard a real reply that comes later in the same candidate list.
    real_candidates: list[ReplyCandidateRow] = []
    non_real_candidates: list[tuple[ReplyCandidateRow, ResponseState]] = []
    for c in candidates:
        if looks_like_obvious_noise(c.sender, c.subject):
            non_real_candidates.append((c, "bounced"))
            continue
        if looks_like_auto_reply_text(c.subject, c.body_snippet):
            non_real_candidates.append((c, "auto_reply"))
            continue
        real_candidates.append(c)

    if not real_candidates:
        if non_real_candidates:
            # No genuine reply anywhere in the window — report the LATEST
            # noise/auto-reply candidate, since it's most representative of
            # the final state of the thread.
            c, state = non_real_candidates[-1]
            return ResponseMatchResult(
                response_state=state, first_reply_email_id=c.email_id, first_reply_at=c.date_iso,
                latest_reply_email_id=c.email_id, latest_reply_at=c.date_iso, latest_reply_subject=c.subject,
                review_required=False, review_reason=None,
            )
        return ResponseMatchResult(
            response_state="no_reply", first_reply_email_id=None, first_reply_at=None,
            latest_reply_email_id=None, latest_reply_at=None, latest_reply_subject=None,
            review_required=False, review_reason=None,
        )

    matched = [
        c for c in real_candidates
        if (quote_number and quote_number.lower() in (c.subject or "").lower())
        or (
            normalized_quote_subject_core
            and normalized_quote_subject_core in _normalize_subject_core(c.subject)
        )
    ]

    if len(matched) >= 1:
        first, last = matched[0], matched[-1]
        return ResponseMatchResult(
            response_state="replied", first_reply_email_id=first.email_id, first_reply_at=first.date_iso,
            latest_reply_email_id=last.email_id, latest_reply_at=last.date_iso, latest_reply_subject=last.subject,
            review_required=False, review_reason=None,
        )

    if len(real_candidates) == 1:
        c = real_candidates[0]
        return ResponseMatchResult(
            response_state="replied", first_reply_email_id=c.email_id, first_reply_at=c.date_iso,
            latest_reply_email_id=c.email_id, latest_reply_at=c.date_iso, latest_reply_subject=c.subject,
            review_required=True, review_reason="single_candidate_no_subject_overlap",
        )

    last = real_candidates[-1]
    return ResponseMatchResult(
        response_state="unknown", first_reply_email_id=None, first_reply_at=None,
        latest_reply_email_id=last.email_id, latest_reply_at=last.date_iso, latest_reply_subject=last.subject,
        review_required=True, review_reason="multiple_conflicting_candidates",
    )
