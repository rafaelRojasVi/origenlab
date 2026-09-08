#!/usr/bin/env python3
"""Historical customer quote register — read-only reconstruction exporter.

Never mutates the canonical archive. Never sends, deletes, or relabels mail.
--fetch-attachments is the only path that touches the network at all, and it
only ever does a read-only IMAP re-fetch (see attachment_fetch.py).
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import imaplib
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.config import load_settings
from origenlab_email_pipeline.historical_quote_register.attachment_enrichment import enrich_attachment
from origenlab_email_pipeline.historical_quote_register.attachment_fetch import (
    build_message_id_uid_map,
    recover_gmail_attachment_bytes,
    recover_mbox_attachment_bytes,
)
from origenlab_email_pipeline.historical_quote_register.candidates import SENT_FOLDERS, fetch_send_candidates
from origenlab_email_pipeline.historical_quote_register.db_readonly import open_readonly
from origenlab_email_pipeline.historical_quote_register.document_bundle import (
    BundleManifestRow,
    copy_into_bundle,
    slugify,
    write_drive_upload_manifest,
)
from origenlab_email_pipeline.historical_quote_register.manifest import (
    RunManifest,
    hash_output_files,
    write_manifest,
)
from origenlab_email_pipeline.historical_quote_register.outcome_signals import classify_outcome
from origenlab_email_pipeline.historical_quote_register.quote_number import (
    build_historical_quote_key,
    extract_quote_number,
)
from origenlab_email_pipeline.historical_quote_register.quote_signal import classify_send_direction
from origenlab_email_pipeline.historical_quote_register.register_builder import (
    RegisterRow,
    write_register_csvs,
)
from origenlab_email_pipeline.historical_quote_register.reply_matching import (
    classify_response,
    find_candidate_replies,
)
from origenlab_email_pipeline.historical_quote_register.revision_linking import (
    PriorSend,
    link_revision,
)
from origenlab_email_pipeline.historical_quote_register.sidecar_db import create_sidecar
from origenlab_email_pipeline.historical_quote_register.source_fingerprint import fingerprint_source_db
from origenlab_email_pipeline.warm_case_sender_rules import email_domain

EXPORTER_VERSION = "historical_quote_register/v1"


def _connect_gmail_readonly() -> imaplib.IMAP4_SSL:
    """Auth exactly like scripts/ingest/05_workspace_gmail_imap_to_sqlite.py —
    same settings, same XOAUTH2 flow. Every folder select downstream of this
    connection MUST pass readonly=True; this function performs no writes."""
    from origenlab_email_pipeline.gmail_workspace_oauth import (
        load_credentials_for_gmail_imap,
        xoauth2_authenticate,
    )

    settings = load_settings()
    client_json = (settings.gmail_oauth_client_json or "").strip()
    user = (settings.gmail_workspace_user or "").strip()
    if not client_json or not user:
        raise SystemExit(
            "Set ORIGENLAB_GMAIL_OAUTH_CLIENT_JSON and ORIGENLAB_GMAIL_WORKSPACE_USER "
            "to use --fetch-attachments."
        )
    default_token = settings.data_root / "secrets" / "gmail_workspace_token.json"
    token_path = Path((settings.gmail_token_json or "").strip() or default_token)
    creds = load_credentials_for_gmail_imap(
        client_secrets_json=Path(client_json), token_json=token_path,
        open_browser=settings.gmail_oauth_open_browser,
    )
    if not creds.token:
        raise SystemExit("No access token after OAuth for --fetch-attachments.")
    mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    xoauth2_authenticate(mail, user, creds.token)
    return mail


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument("--fetch-attachments", action="store_true")
    ap.add_argument("--max-candidates", type=int, default=0)
    args = ap.parse_args()

    settings = load_settings()
    db_path = args.db or settings.resolved_sqlite_path()
    started_at = datetime.now(timezone.utc).isoformat()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out_dir or (
        _ROOT / "reports" / "out" / "active" / "current" / f"historical_customer_quotes_{timestamp}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = open_readonly(db_path)
    fp_before = fingerprint_source_db(db_path, conn)
    sidecar = create_sidecar(out_dir)

    candidates = fetch_send_candidates(conn)
    if args.max_candidates:
        candidates = candidates[: args.max_candidates]

    # --fetch-attachments: one read-only IMAP connection + one batch
    # message_id->UID preflight for the whole Gmail-era Sent folder (design
    # §3.4) — never a persisted UID, never per-candidate SEARCH calls.
    mail: imaplib.IMAP4_SSL | None = None
    gmail_uid_map: dict[str, bytes] = {}
    if args.fetch_attachments:
        mail = _connect_gmail_readonly()
        gmail_uid_map = build_message_id_uid_map(mail, folder=SENT_FOLDERS[0])

    prior_sends: dict[str, PriorSend] = {}
    rows: list[RegisterRow] = []
    bundle_rows: list[BundleManifestRow] = []
    skipped_ambiguous = 0
    skipped_supplier = 0

    for c in candidates:
        direction, contact = classify_send_direction(recipients=c.recipients, subject=c.subject)
        if direction == "internal_only":
            continue
        if direction == "supplier_rfq":
            skipped_supplier += 1
            continue
        if direction == "ambiguous":
            skipped_ambiguous += 1
            continue

        domain = email_domain(contact)
        att_row = conn.execute(
            "SELECT id, filename, content_type, sha256, saved_path FROM attachments "
            "WHERE email_id = ? ORDER BY part_index ASC LIMIT 1",
            (c.email_id,),
        ).fetchone()
        attachment_id, filename, content_type, sha256, saved_path = (
            att_row if att_row else (None, None, None, None, None)
        )

        recovered_bytes: bytes | None = None
        if args.fetch_attachments and attachment_id is not None and sha256 and not saved_path:
            try:
                if c.folder == SENT_FOLDERS[0]:
                    uid = gmail_uid_map.get((c.message_id or "").strip().lower())
                    if uid is not None and mail is not None:
                        recovered_bytes = recover_gmail_attachment_bytes(
                            mail, uid=uid, expected_sha256=sha256,
                        )
                else:
                    recovered_bytes = recover_mbox_attachment_bytes(
                        mbox_source_file=c.source_file, message_id=c.message_id or "",
                        expected_sha256=sha256,
                    )
            except Exception as exc:  # noqa: BLE001 — one bad recovery must not abort the run
                print(f"[warn] attachment recovery failed for email_id={c.email_id}: {exc}", file=sys.stderr)
                recovered_bytes = None

        document_text = None
        if attachment_id is not None:
            enrichment = enrich_attachment(
                conn, sidecar, attachment_id=attachment_id, filename=filename,
                content_type=content_type, recovered_bytes=recovered_bytes,
            )
            document_text = enrichment.text_preview

        qn_match = extract_quote_number(subject=c.subject, filename=filename, document_text=document_text)
        hqk = build_historical_quote_key(
            customer_domain=domain, quote_number_match=qn_match, send_email_id=c.email_id,
        )

        prior = prior_sends.get(f"{domain}|{qn_match.normalized_quote_number}") if qn_match else None
        evidence_text = " ".join(filter(None, [c.subject, filename, document_text]))
        revision = link_revision(
            prior_send=prior, current_attachment_sha256=sha256,
            current_historical_quote_key=hqk, evidence_text=evidence_text,
        )
        if qn_match:
            prior_sends[f"{domain}|{qn_match.normalized_quote_number}"] = PriorSend(
                historical_quote_key=hqk, attachment_sha256=sha256,
            )

        attachment_saved_path_for_row = saved_path
        if recovered_bytes is not None and sha256:
            year = (c.date_iso or "unknown")[:4] or "unknown"
            local_path = copy_into_bundle(
                run_dir=out_dir, payload=recovered_bytes, sha256=sha256,
                original_filename=filename or "attachment.bin", year=year,
                client_slug=slugify(domain), quote_number_or_key=(qn_match.raw_quote_number if qn_match else hqk),
                revision_index=1,
            )
            attachment_saved_path_for_row = str(local_path)
            bundle_rows.append(
                BundleManifestRow(
                    local_export_path=str(local_path.relative_to(out_dir)),
                    original_path=c.source_file, sha256=sha256, historical_quote_key=hqk,
                    quote_number=qn_match.raw_quote_number if qn_match else None,
                    client_name=None, sent_at=c.date_iso,
                )
            )

        replies = find_candidate_replies(
            conn, customer_contact_email=contact, sent_at_iso=c.date_iso or "", window_end_iso=None,
        )
        reply_bodies = [
            (conn.execute("SELECT top_reply_clean FROM emails WHERE id = ?", (r.email_id,)).fetchone() or [""])[0]
            for r in replies
        ]
        match = classify_response(
            replies, quote_number=qn_match.raw_quote_number if qn_match else None,
            normalized_quote_subject_core=None,
        )
        outcome = classify_outcome(
            response_state=match.response_state, reply_bodies=reply_bodies,
            revision_relationship=revision.relationship,
        )

        days_without_reply = None
        if match.response_state == "no_reply" and c.date_iso:
            sent_dt = datetime.fromisoformat(c.date_iso)
            days_without_reply = (datetime.now(timezone.utc).replace(tzinfo=sent_dt.tzinfo) - sent_dt).days

        review_required = (
            direction == "ambiguous" or revision.relationship == "possible_revision_needs_review"
            or match.review_required or (qn_match is None)
        )
        review_reason = revision.relationship if revision.relationship == "possible_revision_needs_review" else (
            match.review_reason or ("no_quote_number_extracted" if qn_match is None else None)
        )

        rows.append(
            RegisterRow(
                historical_quote_key=hqk, quote_number=qn_match.raw_quote_number if qn_match else None,
                quote_number_confidence=qn_match.confidence if qn_match else "none",
                client_name=None, client_email=contact, client_domain=domain,
                sent_email_id=c.email_id, sent_message_id=c.message_id, sent_at=c.date_iso, sent_subject=c.subject,
                attachment_id=attachment_id, attachment_filename=filename, attachment_sha256=sha256,
                attachment_saved_path=attachment_saved_path_for_row,
                response_state=match.response_state, first_client_reply_at=match.first_reply_at,
                latest_client_reply_at=match.latest_reply_at, latest_reply_email_id=match.latest_reply_email_id,
                latest_reply_subject=match.latest_reply_subject,
                outcome_state=outcome,
                supersedes_historical_quote_key=revision.supersedes_historical_quote_key,
                superseded_by_historical_quote_key=None,
                days_without_reply=days_without_reply,
                source_quote_signal=direction, matching_method="identity_subject_chronology",
                confidence=qn_match.confidence if qn_match else "low",
                review_required=review_required, review_reason=review_reason,
            )
        )

    if mail is not None:
        try:
            mail.logout()
        except Exception:
            pass

    main_csv, review_csv = write_register_csvs(rows, out_dir)
    write_drive_upload_manifest(bundle_rows, out_dir)

    fp_after = fingerprint_source_db(db_path, open_readonly(db_path))
    finished_at = datetime.now(timezone.utc).isoformat()
    output_hashes = hash_output_files(
        out_dir, [main_csv.name, review_csv.name, "drive_upload_manifest.csv"]
    )
    manifest = RunManifest(
        run_started_at=started_at, run_finished_at=finished_at, source_db_path=str(db_path),
        source_fingerprint_before=fp_before.__dict__, source_fingerprint_after=fp_after.__dict__,
        sent_coverage={"folders_scanned": list({c.folder for c in candidates})},
        inbox_coverage_note="Inbox not scanned in Stage 1; only used bounded per-candidate in reply matching",
        counts={
            "candidates": len(candidates), "register_rows": len(rows),
            "skipped_supplier_rfq": skipped_supplier, "skipped_ambiguous": skipped_ambiguous,
            "review_required": sum(1 for r in rows if r.review_required),
        },
        classifier_version=EXPORTER_VERSION, exporter_version=EXPORTER_VERSION,
        no_mutation_declaration="canonical archive opened read-only (mode=ro + PRAGMA query_only) throughout; zero writes issued against emails/attachments/attachment_extracts/document_master",
        output_file_hashes=output_hashes,
    )
    write_manifest(manifest, out_dir)

    print(f"candidates={len(candidates)} register_rows={len(rows)} review_required={manifest.counts['review_required']}")
    print(f"output_dir={out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
