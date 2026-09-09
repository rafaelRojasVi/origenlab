"""Optional Postgres outbound export audit writer.

This module is intentionally optional: normal exports keep working without Postgres.
Callers should only require success when users explicitly request audit writing.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from origenlab_email_pipeline.postgres_url import (  # noqa: F401  (re-exported)
    POSTGRES_URL_ENV_KEYS,
    PostgresUrlError,
    normalize_postgres_url,
    redact_postgres_url,
)
from origenlab_email_pipeline.postgres_url import (
    resolve_postgres_url as _resolve_postgres_url,
)

try:
    import psycopg
except ImportError as exc:  # pragma: no cover
    psycopg = None  # type: ignore[misc, assignment]
    _PSYCOPG_IMPORT_ERROR = exc
else:
    _PSYCOPG_IMPORT_ERROR = None


class OutboundAuditError(PostgresUrlError):
    """Raised when explicit audit writing fails."""


def resolve_postgres_url(
    explicit_url: str | None,
    *,
    require_when_requested: bool,
    audit_requested: bool,
) -> str | None:
    """``postgres_url.resolve_postgres_url`` with this module's error type.

    The export lanes catch ``OutboundAuditError`` around the audit write, so a resolution
    failure here must stay inside that family. ``normalize_postgres_url`` and
    ``redact_postgres_url`` are re-exported unchanged from
    ``origenlab_email_pipeline.postgres_url``.
    """
    url = _resolve_postgres_url(explicit_url)
    if url is None and require_when_requested and audit_requested:
        raise OutboundAuditError(
            "Postgres audit requested but no Postgres URL resolved. "
            "Pass --postgres-url or set " + " / ".join(POSTGRES_URL_ENV_KEYS) + "."
        )
    return url


def _require_psycopg() -> None:
    if psycopg is None:
        raise OutboundAuditError(
            "psycopg is required for Postgres audit writing. "
            f"Install postgres deps (uv sync --group postgres). ({_PSYCOPG_IMPORT_ERROR})"
        )


@dataclass(frozen=True)
class OutboundBatchPayload:
    lane: str
    created_by: str | None
    gmail_user: str
    sent_folders: list[str]
    sent_preflight_json: dict[str, Any]
    gate_version: str | None
    output_artifact_path: str | None
    notes: str | None


def build_outbound_batch_payload(
    *,
    lane: str,
    created_by: str | None,
    gmail_user: str,
    sent_folders: list[str] | tuple[str, ...],
    sent_preflight_json: dict[str, Any] | None,
    gate_version: str | None = None,
    policy_ref: str | None = None,
    output_artifact_path: str | None = None,
    notes: str | None = None,
) -> OutboundBatchPayload:
    gv = gate_version or policy_ref
    return OutboundBatchPayload(
        lane=str(lane).strip(),
        created_by=(str(created_by).strip() or None) if created_by is not None else None,
        gmail_user=str(gmail_user).strip(),
        sent_folders=[str(x) for x in sent_folders],
        sent_preflight_json=sent_preflight_json or {},
        gate_version=(str(gv).strip() or None) if gv is not None else None,
        output_artifact_path=(str(output_artifact_path).strip() or None)
        if output_artifact_path is not None
        else None,
        notes=(str(notes).strip() or None) if notes is not None else None,
    )


def build_outbound_recipient_payloads(
    rows: list[dict[str, Any]],
    *,
    default_eligibility_result: str = "eligible",
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        email_norm = str(r.get("email_norm") or r.get("contact_email") or "").strip().lower()
        if not email_norm:
            continue
        rec = {
            "email_norm": email_norm,
            "lead_id": r.get("lead_id"),
            "source_kind": r.get("source_kind"),
            "source_key": r.get("source_key"),
            "organization_name": r.get("organization_name"),
            "organization_domain": r.get("organization_domain"),
            "eligibility_result": r.get("eligibility_result") or default_eligibility_result,
            "exclusion_reason": r.get("exclusion_reason"),
            "metadata_json": r.get("metadata_json") or {},
        }
        out.append(rec)
    return out


def write_postgres_outbound_audit(
    *,
    postgres_url: str,
    batch: OutboundBatchPayload,
    recipients: list[dict[str, Any]],
) -> int:
    _require_psycopg()
    assert psycopg is not None
    safe_url = redact_postgres_url(postgres_url)
    try:
        conn = psycopg.connect(postgres_url, autocommit=False)
    except Exception as exc:  # noqa: BLE001
        raise OutboundAuditError(f"Postgres outbound audit connect failed ({safe_url}): {exc}") from exc

    try:
        with conn.cursor() as cur:
            # Hard-fail early if expected tables are missing.
            for schema, table in (
                ("outbound", "outbound_batch"),
                ("outbound", "outbound_batch_recipient"),
            ):
                cur.execute(
                    """
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema=%s AND table_name=%s
                    """,
                    (schema, table),
                )
                if cur.fetchone() is None:
                    raise OutboundAuditError(f"Postgres outbound audit table missing: {schema}.{table}")

            cur.execute(
                """
                INSERT INTO outbound.outbound_batch (
                  lane, created_by, gmail_user, sent_folders, sent_preflight_json,
                  gate_version, output_artifact_path, notes
                ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                RETURNING id
                """,
                (
                    batch.lane,
                    batch.created_by,
                    batch.gmail_user,
                    batch.sent_folders,
                    json.dumps(batch.sent_preflight_json, ensure_ascii=False),
                    batch.gate_version,
                    batch.output_artifact_path,
                    batch.notes,
                ),
            )
            batch_id = int(cur.fetchone()[0])

            if recipients:
                cur.executemany(
                    """
                    INSERT INTO outbound.outbound_batch_recipient (
                      batch_id, email_norm, lead_id, source_kind, source_key,
                      organization_name, organization_domain, eligibility_result,
                      exclusion_reason, metadata_json
                    ) VALUES (
                      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                    )
                    """,
                    [
                        (
                            batch_id,
                            str(r["email_norm"]).strip().lower(),
                            r.get("lead_id"),
                            r.get("source_kind"),
                            r.get("source_key"),
                            r.get("organization_name"),
                            r.get("organization_domain"),
                            r.get("eligibility_result") or "eligible",
                            r.get("exclusion_reason"),
                            json.dumps(r.get("metadata_json") or {}, ensure_ascii=False),
                        )
                        for r in recipients
                    ],
                )
        conn.commit()
        return batch_id
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        if isinstance(exc, OutboundAuditError):
            raise
        raise OutboundAuditError(f"Postgres outbound audit write failed ({safe_url}): {exc}") from exc
    finally:
        conn.close()


def maybe_write_postgres_outbound_audit(
    *,
    write_requested: bool,
    explicit_postgres_url: str | None,
    batch: OutboundBatchPayload,
    recipients: list[dict[str, Any]],
) -> int | None:
    url = resolve_postgres_url(
        explicit_postgres_url,
        require_when_requested=True,
        audit_requested=write_requested,
    )
    if not write_requested:
        return None
    assert url is not None
    return write_postgres_outbound_audit(postgres_url=url, batch=batch, recipients=recipients)

