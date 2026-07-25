"""Build read-only NDR review queues and suggested allowlists (no apply)."""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from origenlab_email_pipeline.contact_email_suppression import fetch_contact_email_suppression_row
from origenlab_email_pipeline.mart_core_postgres_migrate import connect_sqlite_readonly
from origenlab_email_pipeline.ndr_contacto_scan import (
    compute_scan_start_date_utc,
    scan_ndr_planned_recipients,
    validate_since_date_utc,
)

BatchName = Literal["A", "B", "C", "D", "E"]
NDR_SCAN_LIMIT = 50_000
MAX_NDR_REVIEW_QUEUE_AGE_DAYS = 7

# Targeted apply codes per human-review batch (exact-email only; never domain suppression).
APPLY_ONLY_CODE_BATCH_A = "bounce_no_such_user"
APPLY_ONLY_CODE_BATCH_B = "bounce_other"


def apply_only_code_for_batch(batch: BatchName) -> str:
    """Return ``--only-code`` value for targeted NDR apply on batch A or B."""
    if batch == "A":
        return APPLY_ONLY_CODE_BATCH_A
    if batch == "B":
        return APPLY_ONLY_CODE_BATCH_B
    raise ValueError(f"batch {batch!r} has no apply allowlist (held batches C–E)")


def default_date_label(when: date | None = None) -> str:
    d = when or date.today()
    return d.strftime("%Y_%m_%d")


def default_out_dir(repo_root: Path, date_label: str) -> Path:
    return (
        repo_root
        / "reports"
        / "out"
        / "active"
        / "current"
        / f"ndr_review_queue_{date_label}"
    )


def _utc_now_iso(as_of_utc: datetime | None = None) -> str:
    now = (as_of_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return now.replace(microsecond=0).isoformat()


def parse_queue_generated_at_utc(value: object) -> datetime:
    """Parse queue ``generated_at`` / ``generated_at_utc`` into an aware UTC datetime."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("queue generated_at is missing")
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"queue generated_at is malformed: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def derive_legacy_scan_start_date_utc(
    *,
    generated_at: object,
    since_days: object,
) -> str:
    """Derive the original UTC calendar lower bound for queues lacking ``scan_start_date_utc``."""
    if since_days is None or since_days == "":
        raise ValueError("queue since_days is missing")
    try:
        days = int(since_days)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"queue since_days is malformed: {since_days!r}") from exc
    if days < 0:
        raise ValueError(f"queue since_days must be >= 0, got {since_days!r}")
    generated = parse_queue_generated_at_utc(generated_at)
    return compute_scan_start_date_utc(since_days=days, as_of_utc=generated)


def resolve_reviewed_ndr_scan_start_date_utc(
    summary: dict[str, Any],
    *,
    now_utc: datetime | None = None,
    max_age_days: int = MAX_NDR_REVIEW_QUEUE_AGE_DAYS,
) -> tuple[str | None, str | None]:
    """Resolve the frozen evidence lower bound for a reviewed NDR queue.

    Returns ``(scan_start_date_utc, error_reason)``. On success ``error_reason`` is None.
    """
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    generated_raw = summary.get("generated_at_utc", summary.get("generated_at"))
    try:
        generated = parse_queue_generated_at_utc(generated_raw)
    except ValueError:
        return None, "malformed_queue_generated_at"

    age = now - generated
    if age > timedelta(days=int(max_age_days)):
        return None, "queue_too_old_rebuild_required"

    explicit = summary.get("scan_start_date_utc")
    if explicit is not None and str(explicit).strip():
        try:
            return validate_since_date_utc(str(explicit)), None
        except ValueError:
            return None, "malformed_scan_start_date_utc"

    try:
        return (
            derive_legacy_scan_start_date_utc(
                generated_at=generated_raw,
                since_days=summary.get("since_days"),
            ),
            None,
        )
    except ValueError as exc:
        message = str(exc)
        if "since_days is missing" in message:
            return None, "missing_queue_since_days"
        if "since_days is malformed" in message or "since_days must be" in message:
            return None, "malformed_queue_since_days"
        return None, "malformed_queue_generated_at"


def _body_blob(row: tuple[Any, ...]) -> str:
    return (
        str(row[0] or "")
        or str(row[1] or "")
        or str(row[2] or "")
    )


def _load_blob_by_email_id(conn: sqlite3.Connection, email_id: int) -> str:
    row = conn.execute(
        """
        SELECT full_body_clean, body_text_clean, body
        FROM emails
        WHERE id = ?
        LIMIT 1
        """,
        (email_id,),
    ).fetchone()
    if not row:
        return ""
    return _body_blob(row)


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(n in text for n in needles)


def classify_ndr_candidate(
    *,
    proposed_code: str,
    subject: str | None,
    body_blob: str,
    multi_recipient_uncertain: bool,
) -> tuple[BatchName, str]:
    """Classify a candidate into human-review queues A/B/C/D/E."""
    subj = (subject or "").lower()
    blob = body_blob.lower()
    combined = f"{subj}\n{blob}"

    if "notification (delay)" in subj or subj.strip().endswith("(delay)"):
        return "E", "delay_dsn_excluded"
    if multi_recipient_uncertain:
        return "E", "multi_recipient_uncertain"
    if _contains_any(
        combined,
        (
            "mailbox full",
            "mailbox is full",
            "quota exceeded",
            "over quota",
            "over capacity",
            "storage full",
            "insufficient system storage",
            "552 5.2.2",
            "452 4.2.2",
        ),
    ):
        return "C", "mailbox_full_or_quota"
    if _contains_any(
        combined,
        (
            "nxdomain",
            "badrcptdomain",
            "domain not found",
            "dns error",
            "no encontramos el dominio",
            "lookup of",
        ),
    ):
        return "B", "nxdomain_or_domain_not_found"
    if _contains_any(
        combined,
        (
            "access denied",
            "relay denied",
            "message rejected",
            "policy",
            "blocked using",
            "spam",
            "blacklist",
            "denied by policy",
            "5.7.1",
        ),
    ):
        return "D", "policy_or_access_denied"

    if proposed_code == "bounce_no_such_user":
        if _contains_any(
            combined,
            (
                "user unknown",
                "no such user",
                "no such recipient",
                "mailbox unavailable",
                "address not found",
                "does not exist",
                "no se encontró la dirección",
                "no se encuentra",
                "unknown user",
                "550 5.1.1",
                "550 5.1.10",
            ),
        ):
            return "A", "no_such_user_final"
        return "E", "no_such_user_without_clear_evidence"
    if proposed_code == "bounce_access_denied":
        return "D", "access_denied_code"
    if proposed_code == "bounce_other":
        return "E", "bounce_other_uncertain"
    return "E", "unmapped_code_uncertain"


@dataclass(frozen=True)
class NdrCandidate:
    email: str
    proposed_code: str
    date_iso: str | None
    email_id: int
    subject_snippet: str | None
    batch: BatchName
    batch_reason: str
    already_suppressed: bool
    suppression_reason_code: str | None

    def to_row(self) -> dict[str, Any]:
        return {
            "email": self.email,
            "proposed_code": self.proposed_code,
            "date_iso": self.date_iso,
            "email_id": self.email_id,
            "subject_snippet": self.subject_snippet,
            "batch": self.batch,
            "batch_reason": self.batch_reason,
            "already_suppressed": int(self.already_suppressed),
            "suppression_reason_code": self.suppression_reason_code or "",
        }


@dataclass(frozen=True)
class NdrReviewQueueResult:
    generated_at: str
    since_days: int
    scan_start_date_utc: str
    sqlite_path: str
    date_label: str
    out_dir: Path
    scanned_rows: int
    skipped_no_recipient: int
    planned_distinct: int
    delay_excluded_count: int
    candidates: list[NdrCandidate]

    @property
    def allowlist_batch_a(self) -> list[str]:
        return sorted(
            c.email
            for c in self.candidates
            if c.batch == "A" and not c.already_suppressed
        )

    @property
    def allowlist_batch_b(self) -> list[str]:
        return sorted(
            c.email
            for c in self.candidates
            if c.batch == "B" and not c.already_suppressed
        )

    def summary_json(self) -> dict[str, Any]:
        batch_counts = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0}
        suppressed = 0
        for c in self.candidates:
            batch_counts[c.batch] += 1
            if c.already_suppressed:
                suppressed += 1
        unsuppressed = len(self.candidates) - suppressed
        return {
            "generated_at": self.generated_at,
            "since_days": self.since_days,
            "scan_start_date_utc": self.scan_start_date_utc,
            "sqlite_path": self.sqlite_path,
            "date_label": self.date_label,
            "scanned_rows": self.scanned_rows,
            "skipped_no_recipient": self.skipped_no_recipient,
            "planned_distinct": self.planned_distinct,
            "delay_excluded_count": self.delay_excluded_count,
            "candidates_total": len(self.candidates),
            "candidates_already_suppressed": suppressed,
            "candidates_unsuppressed": unsuppressed,
            "batch_counts": batch_counts,
            "allowlist_batch_a_count": len(self.allowlist_batch_a),
            "allowlist_batch_b_count": len(self.allowlist_batch_b),
        }


def build_ndr_review_queue(
    *,
    sqlite_path: Path,
    out_dir: Path,
    since_days: int,
    date_label: str,
    as_of_utc: datetime | None = None,
) -> NdrReviewQueueResult:
    as_of = (as_of_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    generated_at = _utc_now_iso(as_of)
    scan_start_date_utc = compute_scan_start_date_utc(
        since_days=since_days,
        as_of_utc=as_of,
    )
    conn = connect_sqlite_readonly(sqlite_path)
    try:
        planned, scanned, skipped_no_recpt = scan_ndr_planned_recipients(
            conn,
            since_date_utc=scan_start_date_utc,
            limit=NDR_SCAN_LIMIT,
        )
        recipient_count_by_email_id: dict[int, int] = {}
        for _email, (_code, _date_iso, email_id, _subj) in planned.items():
            recipient_count_by_email_id[email_id] = recipient_count_by_email_id.get(email_id, 0) + 1

        candidates: list[NdrCandidate] = []
        delay_excluded_count = 0
        for email, (proposed_code, date_iso, email_id, subject_snip) in sorted(planned.items()):
            body_blob = _load_blob_by_email_id(conn, email_id)
            batch, reason = classify_ndr_candidate(
                proposed_code=proposed_code,
                subject=subject_snip,
                body_blob=body_blob,
                multi_recipient_uncertain=recipient_count_by_email_id.get(email_id, 0) > 1,
            )
            if reason == "delay_dsn_excluded":
                delay_excluded_count += 1
                continue
            existing = fetch_contact_email_suppression_row(conn, email)
            candidates.append(
                NdrCandidate(
                    email=email,
                    proposed_code=proposed_code,
                    date_iso=date_iso,
                    email_id=email_id,
                    subject_snippet=subject_snip,
                    batch=batch,
                    batch_reason=reason,
                    already_suppressed=bool(existing),
                    suppression_reason_code=(
                        str(existing.get("suppression_reason_code") or "")
                        if existing
                        else None
                    ),
                )
            )
    finally:
        conn.close()

    result = NdrReviewQueueResult(
        generated_at=generated_at,
        since_days=since_days,
        scan_start_date_utc=scan_start_date_utc,
        sqlite_path=str(sqlite_path.resolve()),
        date_label=date_label,
        out_dir=out_dir,
        scanned_rows=scanned,
        skipped_no_recipient=skipped_no_recpt,
        planned_distinct=len(planned),
        delay_excluded_count=delay_excluded_count,
        candidates=candidates,
    )
    _write_outputs(result)
    return result


def _write_outputs(result: NdrReviewQueueResult) -> None:
    out_dir = result.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = result.summary_json()
    (out_dir / "ndr_review_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (out_dir / "NDR_REVIEW_SUMMARY.md").write_text(_format_summary_md(result), encoding="utf-8")
    (out_dir / "DO_NOT_APPLY_WITHOUT_APPROVAL.md").write_text(_format_no_apply_md(), encoding="utf-8")

    rows = [c.to_row() for c in result.candidates]
    _write_csv(out_dir / "ndr_candidates_all.csv", rows)
    _write_csv(out_dir / "batch_a_clear_no_such_user.csv", [r for r in rows if r["batch"] == "A"])
    _write_csv(
        out_dir / "batch_b_clear_nxdomain_or_no_mailbox.csv",
        [r for r in rows if r["batch"] == "B"],
    )
    _write_csv(
        out_dir / "batch_c_hold_quota_or_mailbox_full.csv",
        [r for r in rows if r["batch"] == "C"],
    )
    _write_csv(
        out_dir / "batch_d_hold_policy_or_access.csv",
        [r for r in rows if r["batch"] == "D"],
    )
    _write_csv(
        out_dir / "batch_e_parser_uncertain.csv",
        [r for r in rows if r["batch"] == "E"],
    )
    _write_allowlist(
        out_dir / "apply_allowlist_batch_a.txt",
        result.allowlist_batch_a,
        APPLY_ONLY_CODE_BATCH_A,
    )
    _write_allowlist(
        out_dir / "apply_allowlist_batch_b.txt",
        result.allowlist_batch_b,
        APPLY_ONLY_CODE_BATCH_B,
    )


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "email",
        "proposed_code",
        "date_iso",
        "email_id",
        "subject_snippet",
        "batch",
        "batch_reason",
        "already_suppressed",
        "suppression_reason_code",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_allowlist(path: Path, emails: list[str], only_code: str) -> None:
    path.write_text(_allowlist_file_text(only_code=only_code, emails=emails, approved=False), encoding="utf-8")


def write_approved_allowlist_template(
    path: Path,
    batch: BatchName,
    emails: list[str],
) -> None:
    """Write post-approval allowlist for manual review packs (batch A or B only)."""
    if batch not in ("A", "B"):
        raise ValueError(f"approved allowlist only for batch A or B, not {batch!r}")
    only_code = apply_only_code_for_batch(batch)
    path.write_text(
        _allowlist_file_text(only_code=only_code, emails=emails, approved=True, batch=batch),
        encoding="utf-8",
    )


def _allowlist_file_text(
    *,
    only_code: str,
    emails: list[str],
    approved: bool,
    batch: BatchName | None = None,
) -> str:
    if approved:
        assert batch in ("A", "B")
        header = [
            f"# Only use after operator approves Batch {batch}.",
            f"# Targeted apply only: --emails-file <this-file> --only-code {only_code} --apply",
            "# Never run broad --apply. Exact-email only — never domain suppression.",
            "",
        ]
    else:
        header = [
            "# SUGGESTED allowlist only — DO NOT APPLY WITHOUT OPERATOR APPROVAL",
            f"# Use with: --emails-file <this-file> --only-code {only_code} --apply",
            "# Never run broad --apply.",
            "",
        ]
    return "\n".join(header + emails) + "\n"


def _format_no_apply_md() -> str:
    return (
        "# DO NOT APPLY WITHOUT APPROVAL\n\n"
        "- Do not run `--apply` until operator explicitly approves.\n"
        "- Use targeted mode only: `--emails-file` + `--only-code`.\n"
        "- Never run broad `--apply`.\n"
        "- Exact-email only. Never domain suppression from NDR.\n"
    )


def _format_summary_md(result: NdrReviewQueueResult) -> str:
    summary = result.summary_json()
    counts = summary["batch_counts"]
    return "\n".join(
        [
            f"# NDR review queue — {result.date_label}",
            "",
            f"- Generated (UTC): {result.generated_at}",
            f"- SQLite: `{result.sqlite_path}`",
            f"- since_days: {result.since_days}",
            f"- scan_start_date_utc: {result.scan_start_date_utc}",
            "",
            "## Scan summary",
            "",
            "| Metric | Value |",
            "|---|---:|",
            f"| Scanned rows | {summary['scanned_rows']} |",
            f"| Planned distinct | {summary['planned_distinct']} |",
            f"| Delay excluded | {summary['delay_excluded_count']} |",
            f"| Candidates total | {summary['candidates_total']} |",
            f"| Already suppressed | {summary['candidates_already_suppressed']} |",
            f"| Unsuppressed | {summary['candidates_unsuppressed']} |",
            "",
            "## Batch counts",
            "",
            "| Batch | Count |",
            "|---|---:|",
            f"| A clear no-such-user | {counts['A']} |",
            f"| B clear NXDOMAIN/domain-not-found | {counts['B']} |",
            f"| C hold quota/mailbox-full | {counts['C']} |",
            f"| D hold policy/access | {counts['D']} |",
            f"| E parser/uncertain | {counts['E']} |",
            "",
            "## Suggested allowlists (unsuppressed only)",
            "",
            f"- `apply_allowlist_batch_a.txt`: {summary['allowlist_batch_a_count']} "
            f"(`--only-code {APPLY_ONLY_CODE_BATCH_A}`)",
            f"- `apply_allowlist_batch_b.txt`: {summary['allowlist_batch_b_count']} "
            f"(`--only-code {APPLY_ONLY_CODE_BATCH_B}`)",
            "",
            "No suppressions are applied by this report.",
            "",
        ]
    )
