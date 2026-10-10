"""CRM workspace reads — the data behind the operator dashboard's ``#/crm/*`` sections.

Read only. Every query runs in a ``read only`` transaction under the API's own role, exactly like
:class:`~origenlab_api.v2.cockpit_repository.CockpitRepository`. Nothing here writes the database,
Drive or Gmail, and nothing is inferred: a card shows what the CRM holds, and says where a field
comes from when it is not a CRM row.

Two sources are joined, and kept apart in every response:

* **the CRM** (``crm.*``, ``evidence.*``, ``outbound.*``) — the durable truth;
* **the Drive archive ledgers** (``archive_links.jsonl`` written by the owner-approved case
  archive runs) — where each quotation PDF's bytes are in Drive. The CRM does not hold these links
  yet (``crm.external_identifier`` is empty), so every Drive link carries ``source =
  "archive_ledger"`` and the ledger it came from. A document is matched to a CRM revision only by
  its exact SHA-256 (``crm.quote_revision.pdf_sha256``); nothing is matched by name or number.

"No data" and "not imported" are different answers and are never collapsed: :data:`ENTITY_NOTES`
records, per entity, whether a zero means the import has not been built/run or that the entity was
imported and is genuinely empty.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import getaddresses
from pathlib import Path
from typing import TYPE_CHECKING, Any

from origenlab_api.v2.audience_freeze import SEND_TIME_REFUSAL_LABEL
from origenlab_api.v2.campaign_blocks import campaign_hold, read_campaign_holds
from origenlab_api.v2.campaign_history import (
    RecipientQuery,
    immutability_enforced,
    read_all_totals,
    read_audit,
    read_recipients,
    read_replies,
    read_totals,
    replies_state,
)
from origenlab_api.v2.mail_rules import mail_direction
from origenlab_api.v2.marketing_audience import address_ref, addresses_in
from origenlab_api.v2.person_suggestions import safe_person_suggestions
from origenlab_api.v2.unsubscribe_replies import REVIEW_SHA256_SQL

def _cross_thread_quote_code(title: str) -> str | None:
    """A single *printed* CN reference, not a fuzzy match or an invented quotation."""
    matches = {
        "CN" + match.group(1).zfill(5)
        for match in re.finditer(
            r"(?<![A-Za-z0-9])(?:CN)?(0?\d{4})[-–/](\d{2})(?!\d)",
            title, flags=re.IGNORECASE,
        )
    }
    return next(iter(matches)) if len(matches) == 1 else None


def _cross_thread_addresses(sender: str | None, recipients: str | None) -> set[str]:
    """Share a *specific external participant*, not the OrigenLab shared mailbox."""
    headers = [str(sender or ""), str(recipients or "").replace(";", ",")]
    return {
        address.strip().lower()
        for _, address in getaddresses(headers)
        if "@" in address and not address.strip().lower().endswith("@origenlab.cl")
    }


GMAIL_MESSAGE_URL = "https://mail.google.com/mail/u/0/#all/{}"
DRIVE_FOLDER_URL = "https://drive.google.com/drive/folders/{}"
DRIVE_FILE_URL = "https://drive.google.com/file/d/{}/view"

#: The one mailbox Phase 4a captures, and when its capture counts as late (OPERATIONS.md §8).
MAIL_SYNC_MAILBOX = "contacto@origenlab.cl"
MAIL_SYNC_LATE_MINUTES = 30

#: How many Gmail quote numbers `/v2/workspace/mail-quote-numbers` returns, newest first.
MAIL_QUOTE_NUMBERS_LIMIT = 500

# The CN tokens the Gmail capture (apps/worker capture.py) proposed from attachment file names,
# one row per distinct number: when it was first seen and in how many messages. A record an
# operator rejected does not count; any other review state does, because a number sent by email
# is used whether or not anyone has reviewed the message. `sent_at` is the message's Date header
# as ISO 8601 (the capture falls back to Gmail's internal date); anything else falls back to
# when the record was captured, so one odd header cannot fail the read.
MAIL_QUOTE_NUMBERS_SQL = rf"""
with seen as (
  select btrim(n.value) as quote_number,
         case when sr.payload->>'sent_at' ~ '^\d{{4}}-\d{{2}}-\d{{2}}T\d{{2}}:\d{{2}}'
              then (sr.payload->>'sent_at')::timestamptz else sr.acquired_at end as seen_at,
         sr.id
    from evidence.source_record sr
    cross join lateral jsonb_array_elements_text(
      case when jsonb_typeof(sr.payload->'proposed_quote_numbers') = 'array'
           then sr.payload->'proposed_quote_numbers' else '[]'::jsonb end
    ) as n(value)
   where sr.kind = 'gmail_message' and sr.review_status <> 'rejected'
)
select quote_number, min(seen_at) as first_seen_at, count(distinct id)::int as messages
  from seen
 where quote_number <> ''
 group by quote_number
 order by first_seen_at desc, quote_number desc
 limit {MAIL_QUOTE_NUMBERS_LIMIT}
"""


def mail_sync_state(
    authorization_state: str | None, last_synced_at: datetime | None, minutes_since_sync: int | None
) -> dict[str, Any]:
    """`ok`, `late` (authorized, no completed run for 30 min), `stopped` (revoked, or paused after
    it ran), `not_started` (before go-live) or `not_configured` (no mailbox row)."""
    if authorization_state is None:
        state = "not_configured"
    elif authorization_state == "authorized":
        late = minutes_since_sync is None or minutes_since_sync > MAIL_SYNC_LATE_MINUTES
        state = "late" if late else "ok"
    elif authorization_state == "revoked" or last_synced_at is not None:
        state = "stopped"
    else:
        state = "not_started"
    return {
        "state": state,
        "authorization_state": authorization_state,
        "last_synced_at": last_synced_at.isoformat() if last_synced_at else None,
        "minutes_since_sync": minutes_since_sync,
        "late_after_minutes": MAIL_SYNC_LATE_MINUTES,
    }

# Per-entity provenance: what a count of zero (or a partial count) means. ``imported`` = a
# migration or command has written it and the count is the real state; ``partial`` = rows exist
# but a known part is missing; ``not_imported`` = no import has been built or run, so zero says
# nothing about the business; ``no_write_path`` = V2 has no command that creates it yet.
ENTITY_NOTES: dict[str, dict[str, str]] = {
    "opportunities": {
        "provenance": "imported",
        "note": "Casos históricos y nuevas oportunidades creadas por operadores o reglas de Gmail.",
    },
    "quotes": {"provenance": "imported", "note": "Cotizaciones históricas y nuevas registradas desde PDF enviados por Gmail."},
    "quote_revisions": {
        "provenance": "imported",
        "note": "Revisiones enviadas; cada una con el SHA-256 de su PDF y su correo de Gmail.",
    },
    "organizations": {
        "provenance": "partial",
        "note": "La mayoría son nombres propuestos por máquina desde un manifiesto de migración; "
        "sólo las confirmadas son decisión del operador.",
    },
    "contact_points": {
        "provenance": "partial",
        "note": "Direcciones de correo importadas sin vínculo a institución ni persona: el "
        "manifiesto de origen no trae ese emparejamiento.",
    },
    "persons": {
        "provenance": "partial",
        "note": "Personas creadas o vinculadas en V2. No equivale a migración completa de las personas V1.",
    },
    "affiliations": {
        "provenance": "partial",
        "note": "Afiliaciones registradas en V2; las relaciones históricas V1 aún no están completas.",
    },
    "tasks": {
        "provenance": "partial",
        "note": "Tareas del flujo operativo V2. Las tareas históricas V1 aún no se migraron.",
    },
    "activities": {
        "provenance": "no_write_path",
        "note": "V2 aún no registra actividades; las actividades V1 no se migraron.",
    },
    "messages": {
        "provenance": "partial",
        "note": "Mensajes capturados por el sincronizador Gmail V2. "
        "No implica que todo el historial del buzón haya sido importado.",
    },
    "products": {
        "provenance": "imported",
        "note": "Registros del catálogo cargados en V2; su presencia no confirma disponibilidad ni precios.",
    },
    "campaigns": {
        "provenance": "imported",
        "note": "Campañas históricas importadas como registro de envío (archivadas).",
    },
    "campaign_replies": {
        "provenance": "not_imported",
        "note": "Las respuestas a campañas no se han importado; cero no significa sin respuestas.",
    },
    "drive_links_in_crm": {
        "provenance": "imported",
        "note": "Archivos vinculados mediante evidence.source_record (drive_file) en Supabase. "
        "Incluye archivos históricos sin cotización importada; crm.external_identifier es una tabla legacy.",
    },
}

_COUNT_SQL: dict[str, str] = {
    "opportunities": "select count(*) from crm.opportunity",
    "quotes": "select count(*) from crm.quote",
    "quote_revisions": "select count(*) from crm.quote_revision",
    "organizations": "select count(*) from crm.organization where merged_into_organization_id is null",
    "contact_points": "select count(*) from crm.contact_point",
    "persons": "select count(*) from crm.person",
    "affiliations": "select count(*) from crm.affiliation",
    "tasks": "select count(*) from crm.task",
    "activities": "select count(*) from crm.activity",
    "messages": "select count(*) from comms.message",
    "products": "select count(*) from catalog.product",
    "campaigns": "select count(*) from outbound.campaign",
    "campaign_replies": "select count(*) from outbound.campaign_reply",
    # Compatibility response key; its count now reflects the operational Drive evidence,
    # not the legacy crm.external_identifier table that is intentionally unused.
    "drive_links_in_crm": "select count(*) from evidence.source_record where kind = 'drive_file' "
                          "and payload ->> 'drive_file_id' is not null",
}

# Keys in insertion order, for positional mapping of the combined counts query row.
_COUNT_KEYS: list[str] = list(_COUNT_SQL)

# One SELECT that returns all 14 entity counts as a single row — 1 round trip instead of 14.
# Each column corresponds positionally to the same-position key in _COUNT_KEYS.
_OVERVIEW_COUNTS_SQL: str = "select " + ",\n       ".join(
    f"({sql})::bigint" for sql in _COUNT_SQL.values()
)

# SQL constants extracted from pipeline() so each is a named, testable unit.
_SQL_PIPELINE_OPPS = """
    select op.id::text as opportunity_id, op.title, op.stage, op.version,
           op.organization_id::text as organization_id, o.name as organization_name,
           o.confirmation as organization_confirmation, o.version as organization_version,
           op.created_at::text, op.updated_at::text, op.closed_at::text, op.close_reason
      from crm.opportunity op
      left join crm.organization o on o.id = op.organization_id
     order by op.updated_at desc, op.id
"""
_SQL_PIPELINE_CASE_ORGS = """
    select oo.id::text as opportunity_organization_id,
           oo.opportunity_id::text, oo.organization_id::text, oo.role,
           o.name, oo.confirmation, o.version as organization_version
      from crm.opportunity_organization oo
      join crm.organization o on o.id = oo.organization_id
     where oo.valid_to is null
"""
_SQL_PIPELINE_QUOTES = """
    select q.id::text as quote_id, q.opportunity_id::text, q.quote_number, q.number_origin
      from crm.quote q
"""
# `drive_record`: where the worker's Drive filing (apps/worker `drive_filing.py`) or the imported
# laptop ledger put this revision's PDF — an `evidence.source_record` of kind `drive_file`, keyed
# by the document's SHA-256.
_SQL_PIPELINE_REVISIONS = """
    select qr.id::text as revision_id, qr.quote_id::text, qr.revision_no, qr.status,
           qr.origin, qr.sent_at::text, qr.pdf_sha256, qr.superseded_by_revision_no,
           qr.origin_source_record_id::text, d.payload as drive_record
      from crm.quote_revision qr
      left join evidence.source_record d
             on d.dedupe_key = 'drive_file:' || qr.pdf_sha256 and d.kind = 'drive_file'
"""
# Open follow-ups (`crm.task`, W11) with their owner's name; `due_at` and `created_at` as ISO text.
# `created_at` tells «Hoy» whether a task was scheduled after the client's last email.
_SQL_PIPELINE_TASKS = """
    select t.id::text as task_id, t.opportunity_id::text, t.title,
           to_char(t.due_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as due_at,
           to_char(t.created_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as created_at,
           t.version, o.display_name as owner_display_name
      from crm.task t
      join platform.operator o on o.id = t.owner_operator_id
     where t.status = 'open'
"""
# The newest active note on each case (`crm.note`, subject_kind = 'opportunity') with its author.
# «Hoy» reads `created_at`: a note written after the client's last email is the operator's answer
# («Atendido» on a won case, where a task is refused). `body` is cut to its first words: the card
# shows a hint, the drawer shows the note.
_SQL_PIPELINE_NOTES = """
    select distinct on (n.subject_id)
           n.subject_id::text as opportunity_id,
           to_char(n.created_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as created_at,
           left(n.body, 160) as body,
           o.display_name as author_display_name
      from crm.note n
      join platform.operator o on o.id = n.author_operator_id
      join crm.opportunity op on op.id = n.subject_id
     where n.subject_kind = 'opportunity' and n.status = 'active'
       and (op.closed_at is null or op.stage = 'won')
     order by n.subject_id, n.created_at desc, n.id desc
"""
_SQL_PIPELINE_SOURCES = """
    select sr.id::text as source_record_id,
           sr.payload->>'gmail_message_id' as gmail_message_id,
           sr.payload->>'gmail_thread_id' as gmail_thread_id,
           sr.payload->>'recipients' as recipients,
           sr.payload->>'subject_raw' as subject_raw,
           sr.payload->'documents' as documents,
           exists (select 1 from comms.message m
                    where m.provider_message_id = sr.payload->>'gmail_message_id'
                      and m.eml_storage_path is not null) as has_eml
      from evidence.source_record sr
     where sr.id in (select origin_source_record_id from crm.quote_revision
                      where origin_source_record_id is not null)
"""
# Every captured or staged Gmail message on a thread tied to a case — the thread of an email
# linked to it (an evidence link a person or rule R1/R2 made) or of the email a quote revision
# was recorded from — with what «último contacto» needs: when, which way, the subject and the
# message id. Direction is decided in Python by `mail_rules.mail_direction`, the same rule the
# email → cases planner uses; `sent_at` stays text and is parsed there, so one malformed date
# drops one message instead of failing the read.
_SQL_PIPELINE_CONTACT = """
    with case_threads as (
        select oe.opportunity_id, sr.payload->>'gmail_thread_id' as thread_id
          from crm.opportunity_evidence oe
          join evidence.source_record sr on sr.id = oe.source_record_id
         where oe.unlinked_at is null and sr.kind = 'gmail_message'
           and sr.payload->>'gmail_thread_id' is not null
        union
        select q.opportunity_id, sr.payload->>'gmail_thread_id'
          from crm.quote_revision qr
          join crm.quote q on q.id = qr.quote_id
          join evidence.source_record sr on sr.id = qr.origin_source_record_id
         where sr.kind = 'gmail_message' and sr.payload->>'gmail_thread_id' is not null
    )
    select ct.opportunity_id::text as opportunity_id,
           sr.payload->>'sent_at' as sent_at,
           sr.payload->>'subject_raw' as subject,
           sr.payload->>'gmail_message_id' as gmail_message_id,
           sr.payload->>'sender' as sender,
           sr.payload->>'direction_hint' as direction_hint,
           (select cm.direction from comms.message cm
             where cm.provider_message_id = sr.payload->>'gmail_message_id'
             order by cm.created_at limit 1) as comms_direction
      from case_threads ct
      join evidence.source_record sr
        on sr.kind = 'gmail_message' and sr.payload->>'gmail_thread_id' = ct.thread_id
     where not sr.is_quarantined and sr.review_status <> 'rejected'
"""
_SQL_PIPELINE_PARTICIPANTS = """
    select p.opportunity_id::text, pe.display_name as name, p.role, p.is_primary
      from crm.opportunity_participant p
      left join crm.person pe on pe.id = p.person_id
     where p.valid_to is null
     order by p.is_primary desc
"""

# Codes that stop a case from moving until an operator decides something. Everything else in a
# card's ``attention`` list is informational (``pending``), never a blocker.
BLOCKING_CODES = frozenset(
    {"shared_printed_number", "canonical_undetermined", "no_requesting_institution"}
)

#: Stages after which a case waits on nothing (`crm.opportunity` terminal stages).
CLOSED_STAGES: frozenset[str] = frozenset({"won", "lost", "abandoned"})

ATTENTION_LABELS_ES: dict[str, str] = {
    "shared_printed_number": "El mismo número impreso aparece en otro caso",
    "canonical_undetermined": "Hay más de una revisión vigente: no se sabe cuál es la canónica",
    "no_requesting_institution": "Caso sin institución solicitante",
    "document_not_in_drive": "El PDF no está en Drive y no hay correo original para archivarlo: súbelo a mano",
    "document_pending_drive": "PDF pendiente de archivar en Drive (lo archiva la próxima pasada)",
    "no_gmail_evidence": "Sin correo de Gmail vinculado a la última revisión",
    "no_quote": "Caso sin cotización registrada",
    "no_crm_contact": "Sin persona de contacto en el CRM (sólo el destinatario del correo)",
}


# ─────────────────────────────────────────────────────────── Drive ledgers ──


@dataclass(frozen=True)
class DriveLink:
    document_sha256: str
    file_id: str
    file_url: str
    folder_id: str | None
    case_key: str | None
    quote_number: str | None
    revision: int | None
    original_filename: str | None
    archive_status: str | None
    ledger_crm_status: str | None
    lifecycle: str | None
    gmail_message_id: str | None
    ledger: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": "archive_ledger",
            "ledger": self.ledger,
            "document_sha256": self.document_sha256,
            "file_id": self.file_id,
            "file_url": self.file_url,
            "folder_id": self.folder_id,
            "folder_url": DRIVE_FOLDER_URL.format(self.folder_id) if self.folder_id else None,
            "case_key": self.case_key,
            "quote_number": self.quote_number,
            "revision": self.revision,
            "original_filename": self.original_filename,
            "archive_status": self.archive_status,
        }


class DriveLedgerError(ValueError):
    """An archive ledger is missing or malformed. Raised at startup, never per request."""


def _upload_report_links(p: Path) -> list[DriveLink]:
    """The first case upload (``drive_case_upload_report.json``) predates ``archive_links.jsonl``.
    Only a report whose every upload was verified is accepted, and only verified uploads."""
    try:
        report = json.loads(p.read_text(encoding="utf-8"))
        uploads = report["uploads"]
    except (ValueError, KeyError) as exc:
        raise DriveLedgerError(f"{p}: not a case upload report") from exc
    if report.get("mode") != "upload" or report.get("all_verified") is not True:
        raise DriveLedgerError(f"{p}: upload report is not a verified upload run")
    links = []
    for u in uploads:
        f, folder = u.get("file") or {}, u.get("folder") or {}
        props = f.get("appProperties") or {}
        sha = str(f.get("sha256Checksum") or props.get("origenlab_document_sha256") or "").lower()
        if not (sha and f.get("id") and u.get("downloaded_sha256_matches") is True):
            continue
        rev = props.get("origenlab_revision")
        links.append(
            DriveLink(
                document_sha256=sha,
                file_id=f["id"],
                file_url=DRIVE_FILE_URL.format(f["id"]),
                folder_id=folder.get("id"),
                case_key=u.get("case_key"),
                quote_number=u.get("quote_number"),
                revision=int(rev) if rev and str(rev).isdigit() else None,
                original_filename=f.get("name"),
                archive_status="archived_verified",
                ledger_crm_status=None,
                lifecycle=None,
                gmail_message_id=props.get("origenlab_gmail_message_id"),
                ledger=p.parent.name,
            )
        )
    return links


def _add(index: dict[str, DriveLink], link: DriveLink, where: str) -> None:
    prior = index.get(link.document_sha256)
    if prior is not None and prior.file_id != link.file_id:
        raise DriveLedgerError(
            f"{where}: document {link.document_sha256[:12]} is archived as two different Drive files"
        )
    index.setdefault(link.document_sha256, link)


def live_drive_overview(
    revisions: set[str], live_drive_shas: set[str], ledgers: Mapping[str, DriveLink],
) -> dict[str, int | bool]:
    """Account for worker-filed PDFs as well as boot-time ledgers, by immutable SHA.

    The boot-time archive is not updated when the Drive worker uploads a new quotation.
    Counting only that archive makes the health panel incorrectly show new PDFs as absent
    until an API restart (and after a restart if no ledger was refreshed).
    """
    archived = set(ledgers) | live_drive_shas
    return {
        "configured": bool(archived),
        "documents": len(archived),
        "revisions_with_drive_file": len(revisions & archived),
        "revisions_total": len(revisions),
    }


def drive_links_from_records(revisions: Iterable[Mapping[str, Any]],
                             ledgers: Mapping[str, DriveLink]) -> dict[str, DriveLink]:
    """The ledgers' links plus every revision's `drive_file` record (`_SQL_PIPELINE_REVISIONS`).
    A ledger link wins for the same document: both name the file the archiver verified."""
    out = dict(ledgers)
    for r in revisions:
        rec = r.get("drive_record")
        if isinstance(rec, str):
            rec = json.loads(rec)
        sha = (r.get("pdf_sha256") or "").lower()
        if not rec or not sha or sha in out or not rec.get("drive_file_id"):
            continue
        file_id = str(rec["drive_file_id"])
        out[sha] = DriveLink(
            document_sha256=sha,
            file_id=file_id,
            file_url=rec.get("drive_web_view_link") or DRIVE_FILE_URL.format(file_id),
            folder_id=rec.get("drive_folder_id"),
            case_key=rec.get("case_key"),
            quote_number=rec.get("quote_number"),
            revision=rec.get("revision"),
            original_filename=rec.get("original_filename") or rec.get("file_name"),
            archive_status="archived_verified",
            ledger_crm_status=None,
            lifecycle=None,
            gmail_message_id=rec.get("gmail_message_id"),
            ledger="crm",
        )
    return out


def load_drive_ledgers(paths: Iterable[str | Path]) -> dict[str, DriveLink]:
    """Index every ledger row by document SHA-256. A later ledger never silently overrides an
    earlier one with a different Drive file: two ledgers disagreeing about one document refuses.

    Accepts ``archive_links.jsonl`` ledgers and verified ``drive_case_upload_report.json`` runs."""
    index: dict[str, DriveLink] = {}
    for raw in paths:
        p = Path(raw)
        if not p.is_file():
            raise DriveLedgerError(f"archive ledger not found: {p}")
        ledger = p.parent.name
        if p.suffix == ".json":
            for link in _upload_report_links(p):
                _add(index, link, str(p))
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                sha = str(row["document_sha256"]).lower()
                file_id = str(row["drive_file_id"])
            except (ValueError, KeyError) as exc:
                raise DriveLedgerError(f"{p}:{n}: malformed ledger row") from exc
            link = DriveLink(
                document_sha256=sha,
                file_id=file_id,
                file_url=row.get("drive_web_view_link") or DRIVE_FILE_URL.format(file_id),
                folder_id=row.get("drive_folder_id"),
                case_key=row.get("case_key"),
                quote_number=row.get("quote_number"),
                revision=row.get("revision"),
                original_filename=row.get("original_filename"),
                archive_status=row.get("archive_status"),
                ledger_crm_status=row.get("crm_status"),
                lifecycle=row.get("lifecycle"),
                gmail_message_id=row.get("gmail_message_id"),
                ledger=ledger,
            )
            _add(index, link, f"{p}:{n}")
    return index


# ────────────────────────────────────────────────────────── pure composition ──


def gmail_url(message_id: str | None) -> str | None:
    return GMAIL_MESSAGE_URL.format(message_id) if message_id else None


def _first_address(recipients: str | None) -> tuple[str | None, int]:
    if not recipients:
        return None, 0
    # `getaddresses`, not a split on commas: «"Ruiz, Ana" <ana@…>» is one address, not two.
    pairs = [(name, addr) for name, addr in getaddresses([recipients.replace(";", ",")]) if addr]
    if not pairs:
        return None, 0
    name, addr = pairs[0]
    return (f"{name} <{addr}>" if name else addr), len(pairs)


def _aware(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def last_contacts(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per case, the newest email OrigenLab sent and the newest one it received on its threads.

    Pure, so it is tested without a database. A message without a usable `sent_at` is skipped;
    one message reached through two of the case's threads counts once.
    """
    out: dict[str, dict[str, Any]] = {}
    best: dict[tuple[str, str], datetime] = {}
    for row in rows:
        at = _aware(row.get("sent_at"))
        if at is None:
            continue
        direction = mail_direction(row.get("sender"), row.get("direction_hint"), row.get("comms_direction"))
        key = (row["opportunity_id"], direction)
        if key in best and best[key] >= at:
            continue
        best[key] = at
        out.setdefault(row["opportunity_id"], {"outbound": None, "inbound": None})[direction] = {
            "at": at.isoformat(),
            "subject": row.get("subject"),
            "url": gmail_url(row.get("gmail_message_id")),
        }
        # Display-header evidence, not an accepted CRM person or institution. Never pass a
        # bare mailbox as a name: viewer responses must not acquire an unmasked address.
        sender_names = [name.strip() for name, _ in getaddresses([row.get("sender") or ""])
                        if name.strip() and "@" not in name]
        if len(sender_names) == 1:
            out[row["opportunity_id"]][direction]["sender_name"] = sender_names[0]
    return out


def _sort_key_sent(rev: Mapping[str, Any]) -> tuple[str, int]:
    return (str(rev.get("sent_at") or ""), int(rev.get("revision_no") or 0))


def compose_pipeline(
    opportunities: list[Mapping[str, Any]],
    case_organizations: list[Mapping[str, Any]],
    quotes: list[Mapping[str, Any]],
    revisions: list[Mapping[str, Any]],
    sources: Mapping[str, Mapping[str, Any]],
    participants: list[Mapping[str, Any]],
    drive: Mapping[str, DriveLink],
    contacts: Mapping[str, Mapping[str, Any]] | None = None,
    tasks: list[Mapping[str, Any]] | None = None,
    notes: list[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build one card per opportunity. Pure: every input is a plain row, so this is unit-tested
    without a database. Never invents a value — an absent field stays ``None`` and is explained
    in ``attention``."""
    orgs_by_opp: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in case_organizations:
        orgs_by_opp[row["opportunity_id"]].append(row)
    people_by_opp: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in participants:
        people_by_opp[row["opportunity_id"]].append(row)
    revs_by_quote: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in revisions:
        revs_by_quote[row["quote_id"]].append(row)
    quotes_by_opp: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    opps_by_number: dict[str, set[str]] = defaultdict(set)
    for row in quotes:
        quotes_by_opp[row["opportunity_id"]].append(row)
        if row.get("number_origin") == "printed_historical":
            opps_by_number[row["quote_number"]].add(row["opportunity_id"])

    tasks_by_opp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sorted(tasks or [], key=lambda r: (str(r["due_at"]), r["task_id"])):
        tasks_by_opp[row["opportunity_id"]].append({
            "task_id": row["task_id"], "title": row["title"], "due_at": row["due_at"],
            "version": row["version"], "owner": row.get("owner_display_name"),
            "created_at": row.get("created_at"),
        })

    # The newest active note per case. `_SQL_PIPELINE_NOTES` already returns one row per case
    # (`distinct on`); the reduction here keeps the pure function honest for any caller.
    note_by_opp: dict[str, dict[str, Any]] = {}
    for row in notes or []:
        current = note_by_opp.get(row["opportunity_id"])
        if current is None or str(row["created_at"]) > str(current["created_at"]):
            note_by_opp[row["opportunity_id"]] = {
                "created_at": row["created_at"], "body": row["body"],
                "author": row.get("author_display_name"),
            }

    cards: list[dict[str, Any]] = []
    for opp in opportunities:
        oid = opp["opportunity_id"]
        attention: list[str] = []
        quote_cards: list[dict[str, Any]] = []
        all_revs: list[dict[str, Any]] = []
        for q in sorted(quotes_by_opp.get(oid, []), key=lambda r: r["quote_number"]):
            revs = sorted(revs_by_quote.get(q["quote_id"], []), key=lambda r: r["revision_no"])
            active = [r for r in revs if r["status"] != "void" and r.get("superseded_by_revision_no") is None]
            if len(active) > 1:
                attention.append("canonical_undetermined")
            if len(opps_by_number.get(q["quote_number"], ())) > 1:
                attention.append("shared_printed_number")
            rev_cards = []
            for r in revs:
                src = sources.get(r.get("origin_source_record_id") or "") or {}
                sha = (r.get("pdf_sha256") or "").lower() or None
                doc_name = None
                for d in src.get("documents") or []:
                    if sha and str(d.get("sha256", "")).lower() == sha:
                        doc_name = d.get("filename")
                link = drive.get(sha) if sha else None
                if doc_name is None and link is not None:
                    doc_name = link.original_filename
                rc = {
                    "revision_id": r["revision_id"],
                    "revision_no": r["revision_no"],
                    "status": r["status"],
                    "origin": r.get("origin"),
                    "sent_at": r.get("sent_at"),
                    "superseded_by_revision_no": r.get("superseded_by_revision_no"),
                    "is_active": r in active,
                    "document": {"sha256": sha, "filename": doc_name} if sha else None,
                    "gmail": (
                        {
                            "source_record_id": r.get("origin_source_record_id"),
                            "message_id": src.get("gmail_message_id"),
                            "thread_id": src.get("gmail_thread_id"),
                            "url": gmail_url(src.get("gmail_message_id")),
                            "subject": src.get("subject_raw"),
                        }
                        if src.get("gmail_message_id")
                        else None
                    ),
                    "drive": link.as_dict() if link else None,
                    # The archiver files a PDF from the captured `.eml`; without one it never will.
                    # The archiver skips void revisions and revisions with no PDF hash (drive_filing.py).
                    "drive_pending": (link is None and bool(src.get("has_eml"))
                                      and r.get("status") != "void" and sha is not None),
                    "quote_number": q["quote_number"],
                    "_recipients": src.get("recipients"),
                }
                rev_cards.append(rc)
                all_revs.append(rc)
            quote_cards.append(
                {
                    "quote_id": q["quote_id"],
                    "quote_number": q["quote_number"],
                    "number_origin": q.get("number_origin"),
                    "revisions": [{k: v for k, v in rc.items() if not k.startswith("_")} for rc in rev_cards],
                }
            )

        latest = max(all_revs, key=_sort_key_sent) if all_revs else None
        requesting = [o for o in orgs_by_opp.get(oid, []) if o["role"] == "requesting_institution"]
        organization = None
        if opp.get("organization_id"):
            organization = {
                "organization_id": opp["organization_id"],
                "name": opp.get("organization_name"),
                "confirmation": opp.get("organization_confirmation"),
                # The compare-and-set token «Confirmar institución» sends back.
                "version": opp.get("organization_version"),
            }
        elif requesting:
            organization = {
                "organization_id": requesting[0]["organization_id"],
                "name": requesting[0]["name"],
                "confirmation": requesting[0].get("confirmation"),
                "version": requesting[0].get("organization_version"),
            }
        if organization is None:
            attention.append("no_requesting_institution")
        if not quote_cards:
            attention.append("no_quote")

        people = people_by_opp.get(oid, [])
        contact: dict[str, Any] | None = None
        if people:
            contact = {"source": "crm_participant", "name": people[0].get("name"), "address": None, "others": len(people) - 1}
        else:
            address, n = _first_address(latest.get("_recipients") if latest else None)
            if address:
                bare = addresses_in(address)
                contact = {
                    "source": "gmail_recipient", "name": None, "address": address, "others": max(n - 1, 0),
                    # Joins this card to its equipment interests even when the address is masked.
                    "address_ref": address_ref(bare[0]) if bare else None,
                }
            attention.append("no_crm_contact")

        drive_folder = None
        for rc in sorted(all_revs, key=_sort_key_sent, reverse=True):
            if rc["drive"] and rc["drive"]["folder_id"]:
                drive_folder = {
                    "source": "archive_ledger",
                    "folder_id": rc["drive"]["folder_id"],
                    "url": rc["drive"]["folder_url"],
                }
                break
        if latest is not None:
            if latest["drive"] is None:
                attention.append("document_pending_drive" if latest.get("drive_pending") else "document_not_in_drive")
            if latest["gmail"] is None:
                attention.append("no_gmail_evidence")

        seen: set[str] = set()
        ordered = [c for c in attention if not (c in seen or seen.add(c))]
        # A closed case keeps its notes but never blocks (owner decision 2026-10-09): nothing
        # waits on it, and a blocker nobody needs to clear hides the ones that matter.
        closed = bool(opp.get("closed_at")) or opp.get("stage") in CLOSED_STAGES
        blocked = [] if closed else [c for c in ordered if c in BLOCKING_CODES]
        cards.append(
            {
                "opportunity_id": oid,
                "title": opp["title"],
                "stage": opp["stage"],
                # The version a case command compares against («Cambiar etapa», «Marcar ganada»).
                "version": opp.get("version"),
                "created_at": opp.get("created_at"),
                "updated_at": opp.get("updated_at"),
                "closed_at": opp.get("closed_at"),
                "close_reason": opp.get("close_reason"),
                "organization": organization,
                # Confirmation of this institution's ROLE on this case, not its
                # general CRM organization-record confirmation.
                "requesting_institution_confirmation": (
                    requesting[0].get("confirmation") if requesting else None
                ),
                # Machines may suggest "mentioned", not a requesting institution.
                # The exact row ID permits a separate, human-reviewed role decision.
                "pending_institution_mentions": [
                    {
                        "opportunity_organization_id": o["opportunity_organization_id"],
                        "organization_id": o["organization_id"],
                        "name": o["name"],
                    }
                    for o in orgs_by_opp.get(oid, [])
                    if o["role"] == "mentioned"
                    and o["confirmation"] == "machine_proposed"
                    and o.get("opportunity_organization_id")
                ],
                "other_organizations": [
                    {"organization_id": o["organization_id"], "name": o["name"], "role": o["role"]}
                    for o in orgs_by_opp.get(oid, [])
                    if o["role"] != "requesting_institution"
                ],
                "contact": contact,
                "quotes": quote_cards,
                "quote_numbers": [q["quote_number"] for q in quote_cards],
                "revision_count": len(all_revs),
                "latest_revision": (
                    {k: v for k, v in latest.items() if not k.startswith("_")} if latest else None
                ),
                "drive_folder": drive_folder,
                # «Último contacto»: the newest email each way on the case's Gmail threads
                # (`last_contacts`); null for a case with no captured thread.
                "last_contact": (contacts or {}).get(oid) or {"outbound": None, "inbound": None},
                "attention": [
                    {"code": c, "label": ATTENTION_LABELS_ES[c], "blocking": c in blocked}
                    for c in ordered
                ],
                "status": "blocked" if blocked else (
                    "pending" if any(c not in ("no_crm_contact", "document_pending_drive") for c in ordered) else "ok"),
                # Open `crm.task` rows (W11), earliest due first. A case whose earliest open task
                # is due after today is «En pausa hasta…» on the dashboard.
                "open_tasks": tasks_by_opp.get(oid, []),
                # The newest active note on the case (`crm.note`), or null: «Hoy» counts one
                # written after the client's last email as answered.
                "last_note": note_by_opp.get(oid),
                "next_action": task_next_action(tasks_by_opp.get(oid, []))
                or suggest_next_action(opp, blocked, latest, last_contact=(contacts or {}).get(oid),
                                       last_note=note_by_opp.get(oid)),
            }
        )
    return cards


def task_next_action(open_tasks: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The earliest open task, when the case has one: a stored next action beats a suggestion."""
    if not open_tasks:
        return None
    first = open_tasks[0]
    return {"text": first["title"], "source": "task", "due_at": first["due_at"]}


def suggest_next_action(
    opp: Mapping[str, Any], blocked: list[str], latest: Mapping[str, Any] | None, *,
    last_contact: Mapping[str, Any] | None = None, last_note: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """A deterministic suggestion, labelled as such — ``crm.task`` holds no stored next action.

    For a quoted case it names the last touch, on the dashboard's one clock (`lastTouch` in
    `caseDisplay.ts`): the client's newest email after the quote means «Responder …»; OrigenLab's
    newest email or note after the quote means «(último contacto …)»; else «(enviada …)».
    """
    if "no_requesting_institution" in blocked:
        text = "Confirmar la institución solicitante"
    elif "shared_printed_number" in blocked:
        text = "Decidir a qué caso pertenece el número impreso compartido"
    elif "canonical_undetermined" in blocked:
        text = "Anular o reemplazar la revisión duplicada"
    elif opp.get("closed_at"):
        text = "Caso cerrado — sin acción"
    elif latest is None:
        text = "Registrar la cotización del caso"
    elif opp.get("stage") in ("quoting", "negotiating"):
        touch, by = _last_touch(latest.get("sent_at"), last_contact, last_note)
        if by == "client":
            text = f"Responder al correo del {_short_date(touch)} sobre {latest['quote_number']}"
        else:
            when = _short_date(touch)
            label = "último contacto" if by == "us" else "enviada"
            text = f"Hacer seguimiento de {latest['quote_number']}" + (f" ({label} {when})" if when else "")
    else:
        text = "Revisar el caso"
    return {"text": text, "source": "suggested", "due_at": None}


def _last_touch(sent_at: Any, last_contact: Mapping[str, Any] | None,
                last_note: Mapping[str, Any] | None) -> tuple[Any, str]:
    """The last touch after the quote and whose it is: ``client``, ``us`` or ``quote``.
    A note never hides a client's email; a touch counts only after the quote went out."""
    def ts(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    sent = ts(sent_at)
    after = lambda t: t is not None and (sent is None or t > sent + timedelta(minutes=1))  # noqa: E731
    inbound = (last_contact or {}).get("inbound") or {}
    outbound = (last_contact or {}).get("outbound") or {}
    client = ts(inbound.get("at"))
    mail = ts(outbound.get("at"))
    note = ts((last_note or {}).get("created_at"))
    client = client if after(client) else None
    mail = mail if after(mail) else None
    note = note if after(note) else None
    if client and (mail is None or client >= mail):
        return inbound.get("at"), "client"
    ours = [t for t in (mail, note) if t]
    if ours:
        best = max(ours)
        return (outbound.get("at") if best == mail else (last_note or {}).get("created_at")), "us"
    return sent_at, "quote"


def _short_date(value: Any) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%d-%m-%Y")
    except ValueError:
        return None


_TYPED_QUOTE_NUMBER = re.compile(r"^(\d+)(.*)-(\d{2,4})$")


def quote_number_order(number: str | None) -> tuple[int, int, str]:
    """Sort key for a quote number as people typed it: (year, five-digit correlative, rest).

    The correlative is meant to be five digits ("01246-26"), but folders also say "1013-26"
    (leading zero dropped) and "012392-26" (a revision digit glued on). Padding to five and
    reading only the first five puts each where its correlative says. Anything else sorts last.
    """
    m = _TYPED_QUOTE_NUMBER.match((number or "").strip())
    if not m:
        return (-1, -1, number or "")
    digits, suffix, year = m.groups()
    correlative, rest = digits.zfill(5)[:5], digits.zfill(5)[5:]
    return (int(year), int(correlative), rest + suffix)


_DRIVE_ARCHIVE_CRM_KEYS = ("revision_no", "quote_number", "opportunity_id", "opportunity_title", "organization_name")


def drive_archive_from(
    rows: Iterable[Mapping[str, Any]], ledgers: Mapping[str, DriveLink], *, configured: bool
) -> dict[str, Any]:
    """Pure: `rows` are the CRM revisions with a PDF hash, each with its `drive_file` record when
    the cron filed it (`drive_record`, may be a JSON string). The archive is the ledgers plus
    those records (`drive_links_from_records`); a revision counts as «sin PDF en Drive» only when
    neither names its hash."""
    rows = list(rows)
    drive = drive_links_from_records(rows, ledgers)
    crm = {
        (r.get("sha") or r.get("pdf_sha256") or "").lower(): {k: r.get(k) for k in _DRIVE_ARCHIVE_CRM_KEYS}
        for r in rows if r.get("sha") or r.get("pdf_sha256")
    }
    out = compose_drive_archive(drive, crm)
    # As `pipeline()` does: the cron's records make the archive real even before any boot ledger
    # is mounted, so the page must not say «registros no cargados» while listing folders.
    out["configured"] = configured or bool(drive)
    out["crm_revisions_without_drive_file"] = sorted(
        ({"sha256": s, **v} for s, v in crm.items() if s not in drive),
        key=lambda r: r["quote_number"] or "",
    )
    return out


def compose_drive_archive(
    drive: Mapping[str, DriveLink], crm_revisions: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """Group the archive ledgers by case folder and say, per document, whether the CRM holds it."""
    folders: dict[str, dict[str, Any]] = {}
    for link in drive.values():
        key = link.folder_id or f"nofolder:{link.case_key}"
        folder = folders.setdefault(
            key,
            {
                "folder_id": link.folder_id,
                "folder_url": DRIVE_FOLDER_URL.format(link.folder_id) if link.folder_id else None,
                "case_key": link.case_key,
                "documents": [],
            },
        )
        crm = crm_revisions.get(link.document_sha256)
        folder["documents"].append(
            {
                **link.as_dict(),
                "gmail_url": gmail_url(link.gmail_message_id),
                "in_crm": crm is not None,
                "crm": dict(crm) if crm else None,
                "ledger_crm_status": link.ledger_crm_status,
            }
        )
    out = []
    for f in folders.values():
        f["documents"].sort(key=lambda d: (d.get("quote_number") or "", d.get("revision") or 0))
        f["quote_numbers"] = sorted({d["quote_number"] for d in f["documents"] if d.get("quote_number")})
        f["in_crm"] = sum(1 for d in f["documents"] if d["in_crm"])
        f["organization_name"] = next(
            (d["crm"]["organization_name"] for d in f["documents"] if d["crm"] and d["crm"].get("organization_name")),
            None,
        )
        out.append(f)
    out.sort(key=lambda f: quote_number_order((f["quote_numbers"][:1] or [None])[0]), reverse=True)
    docs = [d for f in out for d in f["documents"]]
    return {
        "source": "archive_ledger",
        "ledgers": sorted({d["ledger"] for d in docs}),
        "folders": out,
        "totals": {
            "folders": len(out),
            "documents": len(docs),
            "in_crm": sum(1 for d in docs if d["in_crm"]),
            "not_in_crm": sum(1 for d in docs if not d["in_crm"]),
        },
    }


# ─────────────────────────────────────────────────────────────── repository ──


if TYPE_CHECKING:
    from origenlab_api.v2.marketing_audience import AudienceInputs


class CrmWorkspaceRepository:
    """Read queries for ``/v2/workspace/*``. Same ``(connect, dsn)`` wiring as the cockpit."""

    def __init__(
        self,
        connect: Any,
        dsn: str,
        drive: Mapping[str, DriveLink] | None = None,
        statement_timeout_ms: int = 30_000,
    ) -> None:
        self._connect = connect
        self._dsn = dsn
        self._drive: Mapping[str, DriveLink] = drive or {}
        self._statement_timeout_ms = statement_timeout_ms

    @property
    def drive_configured(self) -> bool:
        return bool(self._drive)

    @contextmanager
    def _read(self):  # type: ignore[no-untyped-def]
        with self._connect(self._dsn, autocommit=False) as conn:
            with conn.cursor() as cur:
                # Pipeline both setup statements so they consume 1 RTT instead of 2.
                with conn.pipeline():
                    cur.execute("set transaction read only")
                    cur.execute(f"set local statement_timeout = {int(self._statement_timeout_ms)}")
                try:
                    yield cur
                finally:
                    conn.rollback()

    @staticmethod
    def _rows(cur: Any) -> list[dict[str, Any]]:
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    # -- overview

    def overview(self) -> dict[str, Any]:
        with self._read() as cur:
            # 14 entity counts in one round trip (scalar subqueries → single row).
            cur.execute(_OVERVIEW_COUNTS_SQL)
            _row = cur.fetchone()
            counts: dict[str, int] = {key: int(_row[i]) for i, key in enumerate(_COUNT_KEYS)}
            cur.execute(
                "select confirmation, count(*) from crm.organization "
                "where merged_into_organization_id is null group by 1"
            )
            org_confirmation = {r[0]: int(r[1]) for r in cur.fetchall()}
            cur.execute("select stage, count(*) from crm.opportunity group by 1")
            by_stage = {r[0]: int(r[1]) for r in cur.fetchall()}
            cur.execute(
                "select count(*) filter (where organization_id is not null), "
                "count(*) filter (where person_id is not null) from crm.contact_point"
            )
            linked_org, linked_person = cur.fetchone()
            cur.execute(
                "select kind, resolution, count(*) from evidence.assertion group by 1, 2 order by 1, 2"
            )
            assertions = [{"kind": r[0], "resolution": r[1], "count": int(r[2])} for r in cur.fetchall()]
            # One round-trip, preserving overview's 8-statement budget. The worker
            # files PDFs asynchronously into evidence.source_record after API startup.
            cur.execute(
                "select 'revision' as source, pdf_sha256 as sha "
                "from crm.quote_revision where pdf_sha256 is not null "
                "union all "
                "select 'drive', payload ->> 'document_sha256' "
                "from evidence.source_record where kind = 'drive_file' "
                "and payload ->> 'drive_file_id' is not null"
            )
            sha_rows = cur.fetchall()
            rev_shas = {str(sha).lower() for source, sha in sha_rows if source == 'revision' and sha}
            live_drive_shas = {str(sha).lower() for source, sha in sha_rows if source == 'drive' and sha}
        entities = [
            {"key": k, "count": counts[k], **ENTITY_NOTES[k]} for k in _COUNT_SQL
        ]
        return {
            "entities": entities,
            "opportunities_by_stage": by_stage,
            "organizations_by_confirmation": org_confirmation,
            "contact_points_linked": {"organization": int(linked_org), "person": int(linked_person)},
            "assertions": assertions,
            "drive_archive": live_drive_overview(rev_shas, live_drive_shas, self._drive),
        }

    # -- pipeline

    def pipeline(self) -> dict[str, Any]:
        with self._connect(self._dsn, autocommit=False) as conn:
            # Setup: 2 statements in 1 RTT.
            setup = conn.cursor()
            with conn.pipeline():
                setup.execute("set transaction read only")
                setup.execute(f"set local statement_timeout = {int(self._statement_timeout_ms)}")

            # 9 data queries in 1 RTT via psycopg pipeline mode.
            cur_opps = conn.cursor()
            cur_case_orgs = conn.cursor()
            cur_quotes = conn.cursor()
            cur_revisions = conn.cursor()
            cur_sources = conn.cursor()
            cur_participants = conn.cursor()
            cur_contact = conn.cursor()
            cur_tasks = conn.cursor()
            cur_notes = conn.cursor()
            with conn.pipeline():
                cur_opps.execute(_SQL_PIPELINE_OPPS)
                cur_case_orgs.execute(_SQL_PIPELINE_CASE_ORGS)
                cur_quotes.execute(_SQL_PIPELINE_QUOTES)
                cur_revisions.execute(_SQL_PIPELINE_REVISIONS)
                cur_sources.execute(_SQL_PIPELINE_SOURCES)
                cur_participants.execute(_SQL_PIPELINE_PARTICIPANTS)
                cur_contact.execute(_SQL_PIPELINE_CONTACT)
                cur_tasks.execute(_SQL_PIPELINE_TASKS)
                cur_notes.execute(_SQL_PIPELINE_NOTES)

            # Fetch after pipeline: all results are ready.
            opps = self._rows(cur_opps)
            case_orgs = self._rows(cur_case_orgs)
            quotes = self._rows(cur_quotes)
            revisions = self._rows(cur_revisions)
            sources: dict[str, Any] = {}
            for row in self._rows(cur_sources):
                docs = row.get("documents")
                if isinstance(docs, str):
                    docs = json.loads(docs)
                row["documents"] = [
                    {"sha256": d.get("sha256"), "filename": d.get("filename")} for d in (docs or [])
                ]
                sources[row["source_record_id"]] = row
            participants = self._rows(cur_participants)
            contacts = last_contacts(self._rows(cur_contact))
            tasks = self._rows(cur_tasks)
            notes = self._rows(cur_notes)
            conn.rollback()
        drive = drive_links_from_records(revisions, self._drive)
        cards = compose_pipeline(
            opps, case_orgs, quotes, revisions, sources, participants, drive, contacts, tasks, notes
        )
        return {
            "items": cards,
            "total": len(cards),
            "drive_configured": self.drive_configured or bool(drive),
        }

    # -- providers

    def providers(self) -> dict[str, Any]:
        with self._read() as cur:
            cur.execute(
                """
                select o.id::text as organization_id, o.name, o.confirmation, oo.role,
                       count(distinct oo.opportunity_id) as cases
                  from crm.opportunity_organization oo
                  join crm.organization o on o.id = oo.organization_id
                 where oo.role in ('supplier', 'manufacturer') and oo.valid_to is null
                 group by 1, 2, 3, 4
                 order by 5 desc, 2
                """
            )
            on_cases = self._rows(cur)
            # Individual assertion rows (not grouped) so each can carry assertion_id + review.
            # Multiple assertions with the same domain are shown as siblings; supplier_directory
            # groups them by brand match.
            cur.execute(
                """
                select a.id::text as assertion_id,
                       a.value_norm as domain,
                       a.value->>'trade_name' as trade_name,
                       a.resolution,
                       a.resolved_at::text as decided_at,
                       a.ambiguity_note as note
                  from evidence.assertion a
                 where a.kind = 'supplier_candidate'
                 order by a.value->>'trade_name' nulls last, a.value_norm
                """
            )
            candidates = self._rows(cur)
            # Product-line links: one row per (line_id, organization).
            cur.execute(
                """
                select pl.line_id, o.id::text as organization_id, o.name,
                       pl.id::text as link_id,
                       pl.valid_from::text, pl.note
                  from crm.organization_product_line pl
                  join crm.organization o on o.id = pl.organization_id
                 where pl.valid_to is null
                 order by pl.line_id, o.name
                """
            )
            line_orgs_rows = self._rows(cur)
        for r in on_cases:
            r["cases"] = int(r["cases"])
        # Build lines[] keyed by the six taxonomy brand-ids.
        lines_by_id: dict[str, list[dict[str, Any]]] = {lid: [] for lid in _LINE_NAMES}
        for r in line_orgs_rows:
            lid = r["line_id"]
            if lid in lines_by_id:
                lines_by_id[lid].append({
                    "organization_id": r["organization_id"],
                    "name": r["name"],
                    "link_id": r["link_id"],
                    "valid_from": r["valid_from"],
                    "note": r.get("note"),
                })
        lines = [
            {"line_id": lid, "name": _LINE_NAMES[lid], "organizations": orgs}
            for lid, orgs in lines_by_id.items()
        ]
        # Enrich each candidate with review sub-object.
        for c in candidates:
            c["review"] = {
                "state": c.pop("resolution"),
                "decided_at": c.pop("decided_at"),
                "note": c.pop("note"),
            }
        return {"on_cases": on_cases, "candidates": candidates, "lines": lines}

    # -- marketing

    def marketing(self) -> dict[str, Any]:
        with self._read() as cur:
            cur.execute(
                """
                select c.id::text as campaign_id, c.name, c.status, c.subject, c.preheader,
                       c.body_html is not null as has_html, c.version,
                       c.approved_at::text, c.created_at::text, c.updated_at::text,
                       (select min(s.accepted_at)::text from outbound.send_attempt s where s.campaign_id = c.id) as first_sent_at,
                       (select max(s.accepted_at)::text from outbound.send_attempt s where s.campaign_id = c.id) as last_sent_at,
                       c.planned_for_date::text as planned_for_date,
                       to_char(c.planned_for_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as planned_for_at,
                       c.planning_version,
                       c.audience_frozen_at::text, c.content_frozen_at::text, c.content_sha256,
                       c.audience_criteria,
                       case when c.origin_source_record_id is not null then 'imported_v1' else 'native_v2' end as origin,
                       m.address_norm as sender_address, m.display_name as sender_name
                  from outbound.campaign c
                  left join comms.mailbox m on m.id = c.mailbox_id
                 order by c.created_at desc
                """
            )
            campaigns = self._rows(cur)
            # The real send batches: accepted attempts grouped by the day (America/Santiago) they
            # were accepted on. Never one invented date for a campaign that went out over days.
            cur.execute(
                """
                select campaign_id::text,
                       (accepted_at at time zone 'America/Santiago')::date::text as day,
                       count(*),
                       to_char(min(accepted_at) at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                       to_char(max(accepted_at) at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
                  from outbound.send_attempt
                 where campaign_id is not null and accepted_at is not null
                 group by 1, 2 order by 1, 2
                """
            )
            batches: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for cid, day, n, first, last in cur.fetchall():
                batches[cid].append({"day": day, "accepted": int(n), "first_accepted_at": first, "last_accepted_at": last})
            cur.execute(
                """
                select campaign_id::text, count(*) from outbound.send_attempt
                 where campaign_id is not null and accepted_at is null group by 1
                """
            )
            undated = {r[0]: int(r[1]) for r in cur.fetchall()}
            cur.execute(
                "select campaign_id::text, state, count(*) from outbound.campaign_recipient group by 1, 2"
            )
            recip: dict[str, dict[str, int]] = defaultdict(dict)
            for cid, state, n in cur.fetchall():
                recip[cid][state] = int(n)
            cur.execute(
                """
                select campaign_id::text, submission_state, delivery_state, count(*)
                  from outbound.send_attempt where campaign_id is not null group by 1, 2, 3
                """
            )
            attempts: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for cid, sub, dele, n in cur.fetchall():
                attempts[cid].append({"submission_state": sub, "delivery_state": dele, "count": int(n)})
            cur.execute("select campaign_id::text, count(*) from outbound.campaign_reply group by 1")
            replies = {r[0]: int(r[1]) for r in cur.fetchall()}
            cur.execute(_BAJA_LINEAGE_BY_CAMPAIGN_SQL)
            baja_lineage = {r[0]: int(r[1]) for r in cur.fetchall()}
            # The card totals: the same predicates the recipient list filters by.
            totals = read_all_totals(cur)
            cur.execute("select kind, scope, count(*) from outbound.contact_control group by 1, 2 order by 1, 2")
            controls = [{"kind": r[0], "scope": r[1], "count": int(r[2])} for r in cur.fetchall()]
            holds = read_campaign_holds(cur)
            cur.execute("select current_database()")
            database = cur.fetchone()[0]
            # Campaign-content presence: override has_html and provide html_state for lists
            all_cids = [c["campaign_id"] for c in campaigns]
            content_state = _campaign_content_list_state(cur, all_cids)
        for c in campaigns:
            c["hold"] = campaign_hold(holds, c["campaign_id"])
            c["recipients_by_state"] = recip.get(c["campaign_id"], {})
            c["send_attempts"] = attempts.get(c["campaign_id"], [])
            c["replies_recorded"] = replies.get(c["campaign_id"], 0)
            c["send_batches"] = batches.get(c["campaign_id"], [])
            c["attempts_without_date"] = undated.get(c["campaign_id"], 0)
            t = totals.get(c["campaign_id"])
            c["totals"] = t["totals"] if t else None
            c["attempt_totals"] = t["attempts"] if t else None
            c["replies"] = replies_state(replies.get(c["campaign_id"], 0), baja_lineage.get(c["campaign_id"], 0))
            c["subject_state"] = _subject_state(c)
            # Override has_html when campaign_content has a sent_html row.
            ccs = content_state.get(c["campaign_id"])
            if ccs is not None:
                has_from_content, state_from_content = ccs
                if has_from_content:
                    c["has_html"] = True
                c["html_state"] = state_from_content
            else:
                # Derive html_state from the campaign row alone (same logic as campaign_archive
                # but without body_html available in the list query — approximate).
                origin = c.get("origin", "")
                status = c.get("status", "")
                if origin == "imported_v1" and status == "archived":
                    # An imported campaign never carries its own HTML: nothing recovered yet.
                    c["html_state"] = "not_recovered"
                elif c.get("content_frozen_at") is None or not c.get("has_html"):
                    c["html_state"] = "not_frozen" if status == "draft" else "not_archived"
                else:
                    c["html_state"] = "archived_verified"
        return {
            "campaigns": campaigns,
            "holds": {k: v for k, v in holds.items() if k != "by_campaign"},
            "contact_controls": controls,
            "replies_note": ENTITY_NOTES["campaign_replies"]["note"],
            "storage": {"table": "outbound.campaign", "database": database},
        }

    def campaign(self, campaign_id: str) -> dict[str, Any] | None:
        """One campaign's content, exactly as stored. `body_html` is null when never imported."""
        with self._read() as cur:
            cur.execute(
                """
                select c.id::text as campaign_id, c.name, c.status, c.subject, c.preheader,
                       c.body_html, c.body_text is not null as has_text, c.version,
                       c.max_sends, c.recontact_interval_days,
                       c.created_at::text, c.updated_at::text,
                       c.content_sha256, c.audience_sha256, c.audience_policy_version,
                       c.audience_frozen_at::text, c.audience_criteria,
                       o.display_name as created_by, current_database() as database
                  from outbound.campaign c
                  left join platform.operator o on o.id = c.created_by_operator_id
                 where c.id = %s
                """,
                (campaign_id,),
            )
            rows = self._rows(cur)
            if not rows:
                return None
            rows[0]["hold"] = campaign_hold(read_campaign_holds(cur), campaign_id)
        return rows[0]

    def campaign_archive(self, campaign_id: str) -> dict[str, Any] | None:
        """What was sent (or frozen to be sent), exactly as stored — never the editable draft.

        The HTML is returned only when it is frozen (`content_frozen_at` set, so the database
        refuses any later edit) and its stored fingerprint recomputes; otherwise `html` is null
        and `html_state` says why. Nothing is reconstructed.
        """
        from origenlab_api.v2.audience_freeze import content_sha256

        with self._read() as cur:
            cur.execute(
                """
                select c.id::text as campaign_id, c.name, c.status, c.subject, c.preheader,
                       c.body_text, c.body_html, c.version, c.content_sha256,
                       c.content_frozen_at::text, c.audience_frozen_at::text, c.audience_sha256,
                       c.audience_policy_version, c.created_at::text,
                       case when c.origin_source_record_id is not null then 'imported_v1' else 'native_v2' end as origin,
                       m.address_norm as sender_address, m.display_name as sender_name,
                       current_database() as database,
                       sr.payload->>'v1_campaign_id' as v1_campaign_id
                  from outbound.campaign c
                  left join comms.mailbox m on m.id = c.mailbox_id
                  left join evidence.source_record sr on sr.id = c.origin_source_record_id
                 where c.id = %s
                """,
                (campaign_id,),
            )
            rows = self._rows(cur)
            if not rows:
                return None
            cur.execute(
                "select state, count(*) from outbound.campaign_recipient where campaign_id = %s group by 1",
                (campaign_id,),
            )
            recipients = {r[0]: int(r[1]) for r in cur.fetchall()}
            cur.execute(
                """
                select submission_state, delivery_state, count(*) from outbound.send_attempt
                 where campaign_id = %s group by 1, 2 order by 1, 2
                """,
                (campaign_id,),
            )
            attempts = [{"submission_state": a, "delivery_state": b, "count": int(n)} for a, b, n in cur.fetchall()]
            cur.execute(
                """
                select (accepted_at at time zone 'America/Santiago')::date::text, count(*),
                       to_char(min(accepted_at) at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                       to_char(max(accepted_at) at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
                  from outbound.send_attempt where campaign_id = %s and accepted_at is not null
                 group by 1 order by 1
                """,
                (campaign_id,),
            )
            batches = [{"day": d, "accepted": int(n), "first_accepted_at": a, "last_accepted_at": b}
                       for d, n, a, b in cur.fetchall()]
            history = read_totals(cur, campaign_id)
            enforced = immutability_enforced(cur)
            cur.execute("select count(*) from outbound.campaign_reply where campaign_id = %s", (campaign_id,))
            reply_count = int(cur.fetchone()[0])
            cur.execute(_BAJA_LINEAGE_BY_CAMPAIGN_SQL.replace("group by", "and s.campaign_id = %s group by"), (campaign_id,))
            lineage_rows = cur.fetchall()
            c = rows[0]
            body_text = c.pop("body_text")
            body_html = c.pop("body_html")
            contents: list[dict] = []
            recovery: dict | None = None
            html: str | None = None
            if body_html is not None and c["content_frozen_at"] is not None:
                if c["content_sha256"] != content_sha256(c["subject"] or "", c["preheader"], body_text or "", body_html):
                    html_state, html = "fingerprint_mismatch", None
                else:
                    html_state, html = "archived_verified", body_html
            elif body_html is not None:
                # A draft with HTML but no frozen fingerprint: nothing is archived yet.
                html_state = "not_frozen" if c["status"] == "draft" else "not_archived"
            else:
                # No body_html on the campaign row (every imported campaign, whose content_frozen_at
                # is null too) — the recovered content in outbound.campaign_content decides.
                html_state, html, contents, recovery = _compute_campaign_content_state(cur, campaign_id, c)
                # Fallback subject / preheader from variant-1 content row when campaign has none
                if not c.get("subject") and contents:
                    v1 = next((cc for cc in contents if cc.get("variant_no") == 1), None)
                    if v1 and v1.get("subject"):
                        c["subject"] = v1["subject"]
                        c["subject_state_override"] = "recovered"
                    if v1 and v1.get("preheader"):
                        c["preheader"] = v1["preheader"]
                        c["preheader_state_override"] = "recovered"
        subject_state = c.pop("subject_state_override", None) or _subject_state(c)
        preheader_state = c.pop("preheader_state_override", None) or (
            "recorded" if c.get("preheader") else ("not_imported" if c.get("origin") == "imported_v1" else "not_set")
        )
        return {
            **c,
            "html": html,
            "html_state": html_state,
            "contents": contents,
            "recovery": recovery,
            "recipients_by_state": recipients,
            "send_attempts": attempts,
            "send_batches": batches,
            "totals": history["totals"],
            "attempt_totals": history["attempts"],
            "replies": replies_state(reply_count, int(lineage_rows[0][1]) if lineage_rows else 0),
            "subject_state": subject_state,
            "preheader_state": preheader_state,
            "immutable": c["status"] == "archived",
            "immutable_enforced_by_database": enforced,
            "metrics": {"opens": None, "clicks": None, "note": "Aperturas y clics no se registran en el CRM."},
            "storage": {"table": "outbound.campaign", "database": c.pop("database")},
        }

    # -- campaign history (detail tabs)

    def campaign_recipients(self, campaign_id: str, query: RecipientQuery, role: str | None) -> dict[str, Any] | None:
        """One page of a campaign's recorded recipients under one total's predicate."""
        with self._read() as cur:
            cur.execute("select name, status from outbound.campaign where id = %s", (campaign_id,))
            found = cur.fetchone()
            if found is None:
                return None
            body = read_recipients(cur, campaign_id, query, role)
            body["totals"] = read_totals(cur, campaign_id)["totals"]
            inputs = read_marketing_audience_inputs(cur) if body["rows"] else None
            address_by_id: dict[str, str] = {}
            if inputs is not None:
                # The rows carry the address as shown (masked for a viewer); interests are keyed
                # by the stored address, never by what the caller is allowed to see.
                cur.execute(
                    "select id::text, address_norm from outbound.campaign_recipient"
                    " where campaign_id = %s and id = any(%s::uuid[])",
                    (campaign_id, [r["recipient_id"] for r in body["rows"]]),
                )
                address_by_id = {rid: addr for rid, addr in cur.fetchall()}
            cur.execute("select current_database()")
            database = cur.fetchone()[0]
        body["interests_available"] = inputs is not None
        if inputs is not None:
            _attach_interests(body["rows"], address_by_id, inputs)
        return {
            "campaign_id": campaign_id, "name": found[0], "status": found[1], **body,
            "storage": {"table": "outbound.campaign_recipient", "database": database},
        }

    def campaign_replies(self, campaign_id: str, role: str | None) -> dict[str, Any] | None:
        with self._read() as cur:
            cur.execute("select name, status from outbound.campaign where id = %s", (campaign_id,))
            found = cur.fetchone()
            if found is None:
                return None
            body = read_replies(cur, campaign_id, role)
            cur.execute("select current_database()")
            database = cur.fetchone()[0]
        return {"campaign_id": campaign_id, "name": found[0], "status": found[1], **body,
                "storage": {"table": "outbound.campaign_reply", "database": database}}

    def campaign_audit(self, campaign_id: str) -> dict[str, Any] | None:
        with self._read() as cur:
            return read_audit(cur, campaign_id)

    # -- audience freeze (reads)

    def freeze_preview(self, campaign_id: str, criteria: Any, *, recontact_review: bool = False) -> dict[str, Any] | None:
        """What `freeze-campaign-audience` would write now, from one read-only snapshot."""
        from origenlab_api.v2.audience_freeze import plan_freeze
        from origenlab_api.v2.equipment_taxonomy import load_taxonomy

        with self._read() as cur:
            cur.execute(
                """
                select id::text as id, status, version, name, subject, preheader, body_html, max_sends
                  from outbound.campaign where id = %s
                """,
                (campaign_id,),
            )
            rows = self._rows(cur)
            if not rows:
                return None
            cur.execute("select outbound.campaign_hold_refusals(%s)", (campaign_id,))
            rows[0]["hold_refusals"] = list(cur.fetchone()[0])
            inputs = read_marketing_audience_inputs(cur)
        return plan_freeze(load_taxonomy(), inputs, rows[0], criteria, recontact_review=recontact_review)

    def campaign_blocks(self) -> dict[str, Any]:
        """Every active campaign safety block, the latest lifted ones, and each target's version."""
        with self._read() as cur:
            holds = read_campaign_holds(cur)
            cur.execute("select current_database()")
            database = cur.fetchone()[0]
        return {**holds, "storage": {"table": "outbound.campaign_block", "database": database}}

    def frozen_recipients(self, campaign_id: str) -> dict[str, Any] | None:
        """A frozen campaign's recipient snapshot, exactly as stored."""
        with self._read() as cur:
            cur.execute(
                """
                select id::text as campaign_id, name, status, version, audience_frozen_at::text,
                       audience_policy_version, content_sha256, audience_sha256, audience_criteria
                  from outbound.campaign where id = %s
                """,
                (campaign_id,),
            )
            campaigns = self._rows(cur)
            if not campaigns:
                return None
            cur.execute(
                """
                select r.id::text as recipient_id, r.address_norm as address,
                       r.contact_point_id::text, r.person_id::text, r.organization_id::text,
                       org.name as organization_name, pe.display_name,
                       r.frozen_at::text, r.frozen_inclusion as inclusion, r.frozen_reasons, r.frozen_notes,
                       r.relevance, r.interest_evidence, r.evidence_observed_at::text,
                       r.identity_review, r.recontact_review, r.recontact_override_at::text,
                       r.campaign_version, r.content_sha256, r.policy_version,
                       r.state as lifecycle_state,
                       outbound.marketing_contact_refusals(r.id) as send_time_refusals
                  from outbound.campaign_recipient r
                  left join crm.organization org on org.id = r.organization_id
                  left join crm.person pe on pe.id = r.person_id
                 where r.campaign_id = %s and r.frozen_at is not null
                 order by r.frozen_inclusion desc, r.address_norm
                """,
                (campaign_id,),
            )
            recipients = self._rows(cur)
            cur.execute("select current_database()")
            database = cur.fetchone()[0]
        for r in recipients:
            r["send_time_refusals"] = [
                {"code": c, "label": SEND_TIME_REFUSAL_LABEL.get(c, c)} for c in (r["send_time_refusals"] or [])
            ]
            # Frozen as included, refused now: the snapshot predates a «BAJA» or another control.
            r["suppressed_since_freeze"] = r["inclusion"] == "included" and bool(r["send_time_refusals"])
        return {
            **campaigns[0],
            "recipients": recipients,
            "suppressed_since_freeze": sum(1 for r in recipients if r["suppressed_since_freeze"]),
            "unsubscribed_since_freeze": sum(
                1 for r in recipients
                if r["suppressed_since_freeze"] and any(x["code"] == "unsubscribe" for x in r["send_time_refusals"])
            ),
            "storage": {"table": "outbound.campaign_recipient", "database": database},
        }

    # -- W10 suppressions

    def suppressions(self, limit: int = 200) -> dict[str, Any]:
        """Marketing unsubscribes and what they refuse today. Read-only; never a message body.

        An unsubscribe is an address block whose reason is `unsubscribe`, or any block a «BAJA»
        was linked to. Frozen recipients are counted against the live send-time contract
        (`outbound.marketing_contact_refusals`), so a snapshot that predates a «BAJA» shows up
        here as refused.
        """
        with self._read() as cur:
            cur.execute(
                """
                with unsub as (
                  select c.id, c.value_norm, c.purpose, c.reason, c.source, c.created_at
                    from outbound.contact_control c
                   where c.scope = 'address' and c.kind = 'block'
                     and (c.reason = 'unsubscribe' or exists (
                           select 1 from evidence.assertion a
                            where a.kind = 'unsubscribe_request' and a.resolved_kind = 'contact_control'
                              and a.resolved_id = c.id))
                )
                select u.id::text as contact_control_id, u.value_norm as address, u.purpose, u.reason, u.source,
                       u.created_at::text as recorded_at,
                       (select count(*) from evidence.assertion a
                         where a.kind = 'unsubscribe_request' and a.resolved_id = u.id)::int as baja_messages,
                       (select max((a.value->>'observed_at')::timestamptz)::text from evidence.assertion a
                         where a.kind = 'unsubscribe_request' and a.resolved_id = u.id) as last_observed_at
                  from unsub u
                 order by u.created_at desc, u.value_norm
                """
            )
            entries = self._rows(cur)
            cur.execute(
                """
                select c.id::text as campaign_id, c.name, c.status,
                       count(*) filter (where 'unsubscribe' = any(outbound.marketing_contact_refusals(r.id)))::int
                         as unsubscribed_since_freeze,
                       count(*) filter (where 'unsubscribe_pending_review' = any(outbound.marketing_contact_refusals(r.id)))::int
                         as pending_review_since_freeze,
                       count(*) filter (where cardinality(outbound.marketing_contact_refusals(r.id)) > 0)::int
                         as refused_since_freeze,
                       count(*)::int as included_at_freeze
                  from outbound.campaign_recipient r
                  join outbound.campaign c on c.id = r.campaign_id
                 where r.frozen_at is not null and r.frozen_inclusion = 'included'
                 group by c.id, c.name, c.status
                 order by c.name
                """
            )
            frozen = self._rows(cur)
            # «BAJA» replies held for review: unresolved requests. Each holds its exact address
            # out of every audience, freeze and send until an operator confirms it (or an admin
            # dismisses it, quoting its review_sha256).
            cur.execute(
                f"""
                select a.id::text as assertion_id, a.value_norm as address,
                       a.value->>'review_reason' as review_reason,
                       a.value->>'grammar_version' as grammar_version,
                       a.value->>'policy_version' as policy_version,
                       (a.value->>'observed_at')::timestamptz::text as observed_at,
                       a.created_at::text as recorded_at,
                       {REVIEW_SHA256_SQL} as review_sha256
                  from evidence.assertion a
                  join evidence.source_record s on s.id = a.source_record_id
                 where a.kind = 'unsubscribe_request' and a.resolution = 'unresolved'
                 order by a.created_at desc, a.value_norm
                """  # noqa: S608 - REVIEW_SHA256_SQL is a constant
            )
            pending = self._rows(cur)
            # What the mail triage read as an unsubscribe but the reply grammar refuses (a
            # «REMOVER» subject, a «BAJA» first line with more text): proposals for a person. One
            # per captured inbound email, from an address that no marketing block and no
            # unsubscribe request (other than a dismissed one) already covers. Never the body.
            cur.execute(
                """
                select a.id::text as assertion_id, snd.address_norm as address, m.subject,
                       m.internal_date::text as observed_at, a.created_at::text as recorded_at,
                       a.value_norm as triage_version,
                       coalesce((select array_agg(x) from jsonb_array_elements_text(a.value -> 'reasons') x), '{}')
                         as reasons,
                       m.provider_message_id as gmail_message_id
                  from evidence.assertion a
                  join evidence.source_record sr on sr.id = a.source_record_id and sr.kind = 'gmail_message'
                  join comms.message m on 'gmail_message:' || m.provider_message_id = sr.dedupe_key
                  join lateral (select p.address_norm from comms.message_participant p
                                 where p.message_id = m.id and p.role = 'from' limit 1) snd on true
                 where a.kind = 'message_triage' and a.value ->> 'class' = 'unsubscribe'
                   and m.direction = 'inbound'
                   and not exists (select 1 from outbound.contact_control c
                                    where c.scope = 'address' and c.value_norm = snd.address_norm
                                      and c.kind = 'block' and c.purpose = 'marketing')
                   and not exists (select 1 from evidence.assertion u
                                    where u.kind = 'unsubscribe_request' and u.value_norm = snd.address_norm
                                      and u.resolution <> 'rejected')
                 order by m.internal_date desc, a.id
                 limit %s
                """,
                (limit,),
            )
            readings = self._rows(cur)
            cur.execute(
                """
                select kind, purpose, count(*)::int from outbound.contact_control
                 where kind = 'block' group by 1, 2 order by 1, 2
                """
            )
            blocks = [{"kind": k, "purpose": p, "count": n} for k, p, n in cur.fetchall()]
            cur.execute("select current_database()")
            database = cur.fetchone()[0]
        return {
            "summary": {
                "unsubscribed_addresses": len(entries),
                "baja_messages": sum(e["baja_messages"] for e in entries),
                "last_recorded_at": entries[0]["recorded_at"] if entries else None,
                "pending_reviews": len(pending),
                "triage_readings": len(readings),
            },
            "pending_reviews": pending[:limit],
            "triage_readings": readings,
            "entries": entries[:limit],
            "truncated": len(entries) > limit,
            "frozen_campaigns": frozen,
            "blocks_by_purpose": blocks,
            "storage": {"table": "outbound.contact_control", "database": database},
        }

    # -- marketing audience

    def marketing_audience_inputs(self) -> "AudienceInputs":
        """Everything `marketing_audience.compose` reads, in one read-only transaction."""
        with self._read() as cur:
            return read_marketing_audience_inputs(cur)

    # -- drive archive

    def drive_archive(self) -> dict[str, Any]:
        """«Archivo Drive»: the September ledgers **and** every `drive_file` record the Drive cron
        has written since (`drive-file`, STATUS §2.7.71), the same union the case cards read.
        Before this the page read the boot ledgers alone, so each quote the cron filed counted as
        «Revisión CRM sin PDF en Drive» although its card already linked the PDF."""
        with self._read() as cur:
            cur.execute(
                """
                select lower(qr.pdf_sha256) as sha, qr.pdf_sha256, qr.revision_no, q.quote_number,
                       op.id::text as opportunity_id, op.title as opportunity_title,
                       o.name as organization_name, d.payload as drive_record
                  from crm.quote_revision qr
                  join crm.quote q on q.id = qr.quote_id
                  join crm.opportunity op on op.id = q.opportunity_id
                  left join crm.organization o on o.id = op.organization_id
                  left join evidence.source_record d
                         on d.dedupe_key = 'drive_file:' || qr.pdf_sha256 and d.kind = 'drive_file'
                 where qr.pdf_sha256 is not null
                """
            )
            rows = self._rows(cur)
        return drive_archive_from(rows, self._drive, configured=self.drive_configured)

    # -- CRM authoring reads

    def person_authoring(self, person_id: str) -> dict[str, Any] | None:
        """Full authoring view of one person: contact points, affiliations, notes, references."""
        with self._read() as cur:
            cur.execute(
                """
                select p.id::text, p.display_name, p.given_name, p.family_name, p.title,
                       p.status, p.archived_at::text, p.archive_reason, p.confirmation,
                       p.version, p.created_at::text, p.updated_at::text,
                       p.merged_into_person_id::text
                  from crm.person p where p.id = %s::uuid
                """,
                (person_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            person = dict(zip([d[0] for d in cur.description], row, strict=True))

            cur.execute(
                """
                select cp.id::text, cp.kind, cp.value_display, cp.value_norm, cp.usage,
                       cp.status, cp.version, cp.note, cp.deactivated_at::text, cp.created_at::text
                  from crm.contact_point cp
                 where cp.person_id = %s::uuid
                 order by cp.created_at, cp.id
                """,
                (person_id,),
            )
            contact_points = self._rows(cur)

            cur.execute(
                """
                select a.id::text, a.organization_id::text, o.name as organization_name,
                       a.role_title, a.unit_label, a.valid_from::text, a.valid_to::text,
                       a.confirmation, a.note
                  from crm.affiliation a
                  left join crm.organization o on o.id = a.organization_id
                 where a.person_id = %s::uuid
                 order by a.valid_from nulls last, a.created_at
                """,
                (person_id,),
            )
            affiliations = self._rows(cur)

            cur.execute(
                """
                select n.id::text, n.root_note_id::text, n.revision_no, n.body,
                       n.author_operator_id::text, op.display_name as author_name,
                       n.created_at::text, n.status, n.archived_at::text, n.archive_reason,
                       n.version,
                       not exists (
                         select 1 from crm.note n2 where n2.revision_of_note_id = n.id
                       ) as is_latest
                  from crm.note n
                  left join platform.operator op on op.id = n.author_operator_id
                 where n.subject_kind = 'person' and n.subject_id = %s::uuid
                 order by coalesce(n.root_note_id, n.id), n.revision_no
                """,
                (person_id,),
            )
            notes = self._rows(cur)

            # References — things that mention this person
            cur.execute(
                """
                select
                  (select count(*)::int from outbound.campaign_recipient
                    where person_id = %s::uuid) as campaign_recipients,
                  (select count(*)::int from crm.opportunity_participant
                    where person_id = %s::uuid and valid_to is null) as opportunity_participants,
                  (select count(*)::int from crm.quote q
                    join crm.opportunity op on op.id = q.opportunity_id
                    join crm.opportunity_participant pp
                         on pp.opportunity_id = op.id and pp.person_id = %s::uuid) as quotes,
                  (select count(*)::int from evidence.assertion a
                    where a.resolved_kind = 'contact_point'
                      and a.resolved_id in (
                          select id from crm.contact_point where person_id = %s::uuid
                      )) as evidence_assertions,
                  (select count(*)::int from crm.note
                    where subject_kind = 'person' and subject_id = %s::uuid) as notes
                """,
                (person_id, person_id, person_id, person_id, person_id),
            )
            ref_row = cur.fetchone()
            refs = {
                "campaign_recipients": ref_row[0],
                "opportunity_participants": ref_row[1],
                "quotes": ref_row[2],
                "evidence_assertions": ref_row[3],
                "notes": ref_row[4],
            }

        # Removal reasons (physical removal never allowed; archive is offered instead)
        reasons: list[str] = []
        if refs["campaign_recipients"]:
            n = refs["campaign_recipients"]
            reasons.append(f"Es destinatario en {n} campaña{'s' if n != 1 else ''}")
        if refs["opportunity_participants"]:
            n = refs["opportunity_participants"]
            reasons.append(f"Participa en {n} caso{'s' if n != 1 else ''} comercial{'es' if n != 1 else ''}")
        if refs["quotes"]:
            n = refs["quotes"]
            reasons.append(f"Hay {n} cotización{'es' if n != 1 else ''} relacionada{'s' if n != 1 else ''}")
        if refs["evidence_assertions"]:
            n = refs["evidence_assertions"]
            reasons.append(f"Hay {n} elemento{'s' if n != 1 else ''} de evidencia")
        if not reasons:
            reasons = ["Sin referencias; puede archivarse"]

        return {
            "person": person,
            "contact_points": contact_points,
            "affiliations": affiliations,
            "notes": notes,
            "references": refs,
            "removal": {"allowed": False, "reasons": reasons},
            "authoring": None,  # injected by the route from request.app.state
        }

    def organization_authoring(self, org_id: str) -> dict[str, Any] | None:
        """Full authoring view of one organization: identifiers, domains, product lines, people."""
        with self._read() as cur:
            cur.execute(
                """
                select o.id::text, o.name, o.legal_name, o.kind, o.status,
                       o.archived_at::text, o.archive_reason, o.confirmation,
                       o.version, o.merged_into_organization_id::text, o.created_at::text,
                       o.confirmed_by_operator_id::text as confirmed_by_operator_id,
                       cop.display_name as confirmed_by_name,
                       (select max(e.recorded_at)::text from crm.domain_event e
                         where e.aggregate_kind = 'organization' and e.aggregate_id = o.id
                           and e.event_type = 'organization.confirmed') as confirmed_at
                  from crm.organization o
                  left join platform.operator cop on cop.id = o.confirmed_by_operator_id
                 where o.id = %s::uuid
                """,
                (org_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            org = dict(zip([d[0] for d in cur.description], row, strict=True))

            cur.execute(
                """
                select ei.id::text, ei.scheme, ei.value_norm,
                       ei.removed_at::text, ei.remove_reason
                  from crm.external_identifier ei
                 where ei.organization_id = %s::uuid
                 order by ei.scheme, ei.value_norm
                """,
                (org_id,),
            )
            identifiers = self._rows(cur)

            cur.execute(
                """
                select od.id::text, od.domain_norm, od.scope,
                       od.removed_at::text, od.remove_reason
                  from crm.organization_domain od
                 where od.organization_id = %s::uuid
                 order by od.scope, od.domain_norm
                """,
                (org_id,),
            )
            domains = self._rows(cur)

            cur.execute(
                """
                select or2.id::text, or2.role, or2.valid_from::text, or2.valid_to::text,
                       or2.note
                  from crm.organization_relationship or2
                 where or2.organization_id = %s::uuid
                 order by or2.valid_from, or2.role
                """,
                (org_id,),
            )
            classifications = self._rows(cur)

            cur.execute(
                """
                select pl.id::text, pl.line_id, pl.valid_from::text, pl.valid_to::text,
                       pl.note
                  from crm.organization_product_line pl
                 where pl.organization_id = %s::uuid
                 order by pl.line_id, pl.valid_from
                """,
                (org_id,),
            )
            pl_rows = self._rows(cur)
            product_lines = [
                {**r, "line_name": _LINE_NAMES.get(r["line_id"], r["line_id"])}
                for r in pl_rows
            ]

            cur.execute(
                """
                select cp.id::text, cp.kind, cp.value_display, cp.value_norm, cp.usage,
                       cp.status, cp.version, cp.note, cp.deactivated_at::text, cp.created_at::text
                  from crm.contact_point cp
                 where cp.organization_id = %s::uuid
                 order by cp.created_at, cp.id
                """,
                (org_id,),
            )
            contact_points = self._rows(cur)

            cur.execute(
                """
                select a.person_id::text, pe.display_name, a.role_title,
                       a.valid_from::text, a.valid_to::text, a.id::text as affiliation_id
                  from crm.affiliation a
                  join crm.person pe on pe.id = a.person_id
                 where a.organization_id = %s::uuid
                 order by a.valid_from nulls last, pe.display_name
                """,
                (org_id,),
            )
            people = self._rows(cur)

            cur.execute(
                """
                select n.id::text, n.root_note_id::text, n.revision_no, n.body,
                       n.author_operator_id::text, op.display_name as author_name,
                       n.created_at::text, n.status, n.archived_at::text, n.archive_reason,
                       n.version,
                       not exists (
                         select 1 from crm.note n2 where n2.revision_of_note_id = n.id
                       ) as is_latest
                  from crm.note n
                  left join platform.operator op on op.id = n.author_operator_id
                 where n.subject_kind = 'organization' and n.subject_id = %s::uuid
                 order by coalesce(n.root_note_id, n.id), n.revision_no
                """,
                (org_id,),
            )
            notes = self._rows(cur)

            cur.execute(
                """
                select
                  (select count(*)::int from outbound.campaign_recipient cr
                    join crm.contact_point cp2 on cp2.id = cr.contact_point_id
                   where cp2.organization_id = %s::uuid
                      or (cp2.person_id in (
                            select person_id from crm.affiliation where organization_id = %s::uuid
                          ))) as campaign_recipients,
                  (select count(distinct op.opportunity_id)::int
                     from crm.opportunity_organization op
                    where op.organization_id = %s::uuid) as opportunities,
                  (select count(distinct q.id)::int
                     from crm.quote q
                     join crm.opportunity opp on opp.id = q.opportunity_id
                    where opp.organization_id = %s::uuid
                       or opp.id in (
                             select opportunity_id from crm.opportunity_organization
                              where organization_id = %s::uuid
                          )) as quotes,
                  (select count(*)::int from crm.affiliation
                    where organization_id = %s::uuid) as affiliations,
                  (select count(*)::int from evidence.assertion
                    where resolved_kind = 'organization' and resolved_id = %s::uuid) as evidence_assertions,
                  (select count(*)::int from catalog.product
                    where manufacturer_organization_id = %s::uuid) as catalog_products
                """,
                (org_id, org_id, org_id, org_id, org_id, org_id, org_id, org_id),
            )
            ref_row = cur.fetchone()
            refs = {
                "campaign_recipients": ref_row[0],
                "opportunities": ref_row[1],
                "quotes": ref_row[2],
                "affiliations": ref_row[3],
                "evidence_assertions": ref_row[4],
                "catalog_products": ref_row[5],
            }
            person_suggestions = [s for s in safe_person_suggestions(cur) if s["organization_id"] == org_id]

        reasons: list[str] = []
        if refs["opportunities"]:
            n = refs["opportunities"]
            reasons.append(f"Participa en {n} caso{'s' if n != 1 else ''} comercial{'es' if n != 1 else ''}")
        if refs["affiliations"]:
            n = refs["affiliations"]
            reasons.append(f"Tiene {n} afiliación{'es' if n != 1 else ''} de personas")
        if refs["campaign_recipients"]:
            n = refs["campaign_recipients"]
            reasons.append(f"Sus contactos son destinatarios en {n} campaña{'s' if n != 1 else ''}")
        if refs["catalog_products"]:
            n = refs["catalog_products"]
            reasons.append(f"Fabrica {n} producto{'s' if n != 1 else ''} del catálogo")
        if refs["evidence_assertions"]:
            n = refs["evidence_assertions"]
            reasons.append(f"Hay {n} elemento{'s' if n != 1 else ''} de evidencia")
        if not reasons:
            reasons = ["Sin referencias; puede archivarse"]

        return {
            "organization": org,
            "identifiers": identifiers,
            "domains": domains,
            "classifications": classifications,
            "product_lines": product_lines,
            "contact_points": contact_points,
            "people": people,
            "notes": notes,
            "references": refs,
            "removal": {"allowed": False, "reasons": reasons},
            "person_suggestions": person_suggestions,
            "authoring": None,  # injected by the route from request.app.state
        }

    def opportunity_notes(self, opportunity_id: str) -> dict[str, Any] | None:
        """The notes on one case («Registrar seguimiento»), in the person/organization note shape.

        `None` when the case does not exist, so the route answers 404 rather than an empty list
        that would look like a case nobody has written about.
        """
        with self._read() as cur:
            cur.execute("select 1 from crm.opportunity where id = %s::uuid", (opportunity_id,))
            if cur.fetchone() is None:
                return None
            cur.execute(
                """
                select n.id::text, n.root_note_id::text, n.revision_no, n.body,
                       n.author_operator_id::text, op.display_name as author_name,
                       n.created_at::text, n.status, n.archived_at::text, n.archive_reason,
                       n.version,
                       not exists (
                         select 1 from crm.note n2 where n2.revision_of_note_id = n.id
                       ) as is_latest
                  from crm.note n
                  left join platform.operator op on op.id = n.author_operator_id
                 where n.subject_kind = 'opportunity' and n.subject_id = %s::uuid
                 order by coalesce(n.root_note_id, n.id), n.revision_no
                """,
                (opportunity_id,),
            )
            notes = self._rows(cur)
        return {"opportunity_id": opportunity_id, "notes": notes}

    def opportunity_mail_documents(self, opportunity_id: str) -> dict[str, Any] | None:
        """The Gmail messages linked to one case and the documents each carries.

        What «Registrar cotización» / «Nueva revisión» pick from: a quote already sent is recorded
        from a message already linked to the case, and names one document that message carries.
        Each document says whether it is already a quote revision (anywhere — one document is
        one revision), so the drawer can show it as recorded instead of offering it again.
        `cn_tokens` are the capture's quote-number readings of the file name, a hint the operator
        confirms, never a number recorded on its own. `None` when the case does not exist.
        """
        with self._read() as cur:
            cur.execute("select 1 from crm.opportunity where id = %s::uuid", (opportunity_id,))
            if cur.fetchone() is None:
                return None
            cur.execute(
                """
                select distinct on (s.id)
                       s.id::text as source_record_id, s.payload->>'subject_raw' as subject,
                       s.payload->>'sent_at' as sent_at,
                       case when jsonb_typeof(s.payload->'documents') = 'array'
                            then s.payload->'documents' else '[]'::jsonb end as documents
                  from crm.opportunity_evidence e
                  join evidence.source_record s on s.id = e.source_record_id
                 where e.opportunity_id = %s::uuid and e.unlinked_at is null
                   and s.kind = 'gmail_message' and not s.is_quarantined
                 order by s.id
                """,
                (opportunity_id,),
            )
            rows = self._rows(cur)
            shas = sorted({
                str(d.get("sha256")).lower()
                for r in rows for d in (r["documents"] or [])
                if isinstance(d, dict) and d.get("sha256")
            })
            recorded: dict[str, dict[str, Any]] = {}
            if shas:
                cur.execute(
                    """
                    select r.pdf_sha256, q.quote_number, r.revision_no,
                           q.opportunity_id::text as opportunity_id
                      from crm.quote_revision r join crm.quote q on q.id = r.quote_id
                     where r.pdf_sha256 = any(%s)
                    """,
                    (shas,),
                )
                recorded = {r["pdf_sha256"]: r for r in self._rows(cur)}
        messages = []
        for r in sorted(rows, key=lambda m: str(m["sent_at"] or ""), reverse=True):
            documents = []
            for d in r["documents"] or []:
                if not isinstance(d, dict) or not d.get("sha256"):
                    continue
                sha = str(d["sha256"]).lower()
                hit = recorded.get(sha)
                documents.append({
                    "sha256": sha,
                    "filename": d.get("filename"),
                    "cn_tokens": list(d.get("cn_tokens") or []),
                    "recorded": (
                        {"quote_number": hit["quote_number"], "revision_no": hit["revision_no"],
                         "on_this_case": hit["opportunity_id"] == opportunity_id}
                        if hit else None
                    ),
                })
            messages.append({
                "source_record_id": r["source_record_id"],
                "subject": r["subject"],
                "sent_at": r["sent_at"],
                "documents": documents,
            })
        return {"opportunity_id": opportunity_id, "messages": messages}

    def opportunity_quote_candidates(self, opportunity_id: str) -> dict[str, Any] | None:
        """Read-only cross-thread proposals. Never link evidence or create a quotation here.

        Require a unique printed number in the existing case, an earlier captured *sent*
        Gmail PDF with the same exact CN token, and a shared external participant in the
        linked case evidence. A reused quote number or shared OrigenLab mailbox alone
        cannot establish identity; the operator still opens both messages and decides.
        """
        with self._read() as cur:
            cur.execute(
                """select title from crm.opportunity where id = %s::uuid""",
                (opportunity_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            title = str(row[0])
            token = _cross_thread_quote_code(title)
            if token is None:
                return {"opportunity_id": opportunity_id, "candidates": []}
            cur.execute(
                """select sr.id::text as source_record_id,
                          sr.payload->>'sender' as sender,
                          sr.payload->>'recipients' as recipients,
                          sr.payload->>'sent_at' as sent_at
                     from crm.opportunity_evidence oe
                     join evidence.source_record sr on sr.id = oe.source_record_id
                    where oe.opportunity_id = %s::uuid and oe.unlinked_at is null
                      and sr.kind = 'gmail_message' and not sr.is_quarantined
                      and sr.review_status <> 'rejected'""",
                (opportunity_id,),
            )
            case_rows = self._rows(cur)
            participants: set[str] = set()
            already_linked: set[str] = set()
            earliest = None
            for case_row in case_rows:
                already_linked.add(case_row["source_record_id"])
                participants.update(_cross_thread_addresses(
                    case_row["sender"], case_row["recipients"],
                ))
                try:
                    at = datetime.fromisoformat(str(case_row["sent_at"]))
                    if at.tzinfo is not None and (earliest is None or at < earliest):
                        earliest = at
                except (TypeError, ValueError):
                    pass
            if not participants or earliest is None:
                return {"opportunity_id": opportunity_id, "candidates": []}
            cur.execute(
                """select sr.id::text as source_record_id,
                          sr.payload->>'gmail_message_id' as gmail_message_id,
                          sr.payload->>'gmail_thread_id' as gmail_thread_id,
                          sr.payload->>'sender' as sender,
                          sr.payload->>'recipients' as recipients,
                          sr.payload->>'subject_raw' as subject,
                          sr.payload->>'sent_at' as sent_at,
                          doc.value as document,
                          exists (
                              select 1 from crm.quote_revision qr
                               where lower(qr.pdf_sha256) = lower(doc.value->>'sha256')
                          ) as recorded
                     from evidence.source_record sr
                     cross join lateral jsonb_array_elements(
                         case when jsonb_typeof(sr.payload->'documents') = 'array'
                              then sr.payload->'documents' else '[]'::jsonb end
                     ) as doc(value)
                    where sr.kind = 'gmail_message' and not sr.is_quarantined
                      and sr.review_status <> 'rejected'
                      and (doc.value->'cn_tokens') ? %s
                    order by sr.payload->>'sent_at' desc
                    limit 50""",
                (token,),
            )
            possible = self._rows(cur)
            # A quotation thread may already have its own commercial case. Never
            # let a follow-up case claim that existing case's PDF merely because
            # they share a number and contact; show the original case for review.
            threads = sorted({
                str(item["gmail_thread_id"])
                for item in possible if item["gmail_thread_id"]
            })
            existing_cases: dict[str, list[dict[str, str]]] = defaultdict(list)
            if threads:
                cur.execute(
                    """select distinct sr.payload->>'gmail_thread_id' as thread_id,
                              o.id::text as opportunity_id, o.title
                         from crm.opportunity_evidence oe
                         join crm.opportunity o on o.id = oe.opportunity_id
                         join evidence.source_record sr on sr.id = oe.source_record_id
                        where oe.unlinked_at is null and sr.kind = 'gmail_message'
                          and not sr.is_quarantined
                          and sr.payload->>'gmail_thread_id' = any(%s)
                        order by thread_id, opportunity_id""",
                    (threads,),
                )
                for row in self._rows(cur):
                    existing_cases[str(row["thread_id"])].append({
                        "opportunity_id": str(row["opportunity_id"]),
                        "title": str(row["title"]),
                    })
        candidates: list[dict[str, Any]] = []
        for candidate in possible:
            if candidate["source_record_id"] in already_linked:
                continue
            # The quote was SENT from the captured OrigenLab mailbox, not received
            # from a customer or merely mentioned in an unrelated incoming message.
            sender = str(candidate["sender"] or "")
            if "contacto@origenlab.cl" not in {
                addr.lower() for _, addr in getaddresses([sender])
            }:
                continue
            shared = participants & _cross_thread_addresses(
                candidate["sender"], candidate["recipients"],
            )
            if not shared:
                continue
            try:
                at = datetime.fromisoformat(str(candidate["sent_at"]))
                if at.tzinfo is None or at >= earliest:
                    continue
            except (TypeError, ValueError):
                continue
            doc = candidate["document"]
            sha = str(doc.get("sha256") or "").lower() if isinstance(doc, dict) else ""
            filename = str(doc.get("filename") or "") if isinstance(doc, dict) else ""
            if (not re.fullmatch(r"[0-9a-f]{64}", sha)
                    or not filename.lower().endswith(".pdf")
                    or doc.get("bytes_hash_verified") is not True):
                continue
            gmail_id = str(candidate["gmail_message_id"] or "")
            if not re.fullmatch(r"[0-9a-f]+", gmail_id):
                continue
            candidates.append({
                "source_record_id": candidate["source_record_id"],
                "subject": candidate["subject"],
                "sent_at": candidate["sent_at"],
                "quote_token": token,
                "filename": filename,
                "document_sha256": sha,
                "gmail_url": f"https://mail.google.com/mail/?authuser=contacto%40origenlab.cl#all/{gmail_id}",
                "reason": "Número de cotización exacto y destinatario externo compartido",
                "recorded_elsewhere": bool(candidate["recorded"]),
                "other_cases_on_quote_thread": [
                    case for case in existing_cases.get(str(candidate["gmail_thread_id"]), [])
                    if case["opportunity_id"] != opportunity_id
                ],
            })
        return {"opportunity_id": opportunity_id, "candidates": candidates[:10]}

    def opportunity_purchase_order_candidates(self, opportunity_id: str) -> dict[str, Any] | None:
        """Suggest a standalone Gmail PO, never assert a quote match or close a case.

        The PDF's quoted reference and line items are NOT available in Gmail's captured
        metadata. We can suggest it from its verified file, a shared external recipient
        with a sent quotation, and chronological order. Only a reviewing operator can
        compare the actual PDF, link the evidence and record the won decision.
        """
        with self._read() as cur:
            cur.execute(
                """select id::text as id from crm.opportunity where id = %s::uuid""",
                (opportunity_id,),
            )
            if cur.fetchone() is None:
                return None
            cur.execute(
                """select qr.sent_at, sr.payload->>'sender' as sender,
                          sr.payload->>'recipients' as recipients
                     from crm.quote q join crm.quote_revision qr on qr.quote_id=q.id
                     join evidence.source_record sr on sr.id=qr.origin_source_record_id
                    where q.opportunity_id=%s::uuid and qr.status='sent'
                      and qr.sent_at is not null and sr.kind='gmail_message'
                      and qr.superseded_by_revision_no is null""",
                (opportunity_id,),
            )
            rows = self._rows(cur)
            participants: set[str] = set()
            sent_dates = []
            for r in rows:
                participants.update(_cross_thread_addresses(r["sender"], r["recipients"]))
                if r["sent_at"]:
                    sent_dates.append(r["sent_at"])
            if not participants or not sent_dates:
                return {"opportunity_id": opportunity_id, "candidates": []}
            first_sent = min(sent_dates)
            cur.execute(
                """select sr.id::text as source_record_id,
                          sr.payload->>'gmail_message_id' as gmail_id,
                          sr.payload->>'gmail_thread_id' as thread_id,
                          sr.payload->>'sender' as sender,
                          sr.payload->>'recipients' as recipients,
                          sr.payload->>'subject_raw' as subject,
                          sr.payload->>'sent_at' as sent_at,
                          doc.value as document,
                          exists (
                            select 1 from crm.opportunity_evidence oe
                             where oe.source_record_id=sr.id and oe.unlinked_at is null
                          ) as already_linked
                     from evidence.source_record sr
                     cross join lateral jsonb_array_elements(
                       case when jsonb_typeof(sr.payload->'documents')='array'
                            then sr.payload->'documents' else '[]'::jsonb end
                     ) doc(value)
                    where sr.kind='gmail_message' and not sr.is_quarantined
                      and sr.review_status <> 'rejected'
                      and sr.created_at >= %s::timestamptz
                      and (
                        doc.value->>'filename' ~* '^(o[.]?c|orden de compra)[ _#n°º0-9-]*[. ]*pdf$'
                      )
                    order by sr.created_at desc
                    limit 200""",
                (first_sent,),
            )
            possible = self._rows(cur)
        import re as _re
        oc_re = _re.compile(r"(?:o[.]?c|orden\s+de\s+compra)\s*(?:n[°º]?\s*)?(\d{5,})", _re.I)
        candidates = []
        for row in possible:
            if row["already_linked"]:
                continue  # Never steal PO evidence already associated with another case.
            sender_addresses = {
                addr.lower() for _, addr in getaddresses([str(row["sender"] or "")])
            }
            if not sender_addresses or "contacto@origenlab.cl" in sender_addresses:
                continue
            shared = participants & _cross_thread_addresses(row["sender"], row["recipients"])
            if not shared:
                continue
            doc = row["document"]
            if not isinstance(doc, dict) or doc.get("bytes_hash_verified") is not True:
                continue
            filename = str(doc.get("filename") or "")
            sha = str(doc.get("sha256") or "").lower()
            if not _re.fullmatch(r"[0-9a-f]{64}", sha):
                continue
            mail_id = str(row["gmail_id"] or "")
            if not _re.fullmatch(r"[0-9a-f]+", mail_id):
                continue
            found = oc_re.search(filename) or oc_re.search(str(row["subject"] or ""))
            candidates.append({
                "source_record_id": row["source_record_id"],
                "gmail_url": f"https://mail.google.com/mail/?authuser=contacto%40origenlab.cl#all/{mail_id}",
                "subject": row["subject"],
                "sent_at": row["sent_at"],
                "filename": filename,
                "document_sha256": sha,
                "purchase_order_number": found.group(1) if found else None,
                "reason": "PDF de OC verificado y participante externo compartido con la cotización",
            })
        return {"opportunity_id": opportunity_id, "candidates": candidates[:10]}

    def person_suggestions(self) -> dict[str, Any]:
        """People the quote emails name and the CRM does not hold yet (`person_suggestions.py`)."""
        with self._read() as cur:
            items = safe_person_suggestions(cur)
        return {"items": items, "total": len(items)}

    def history(self, limit: int = 100) -> dict[str, Any]:
        """«Historial»: the decisions of the last 60 days in plain Spanish (`history.py`)."""
        from origenlab_api.v2.history import read_history

        with self._read() as cur:
            return read_history(cur, limit)

    def mail_sync(self) -> dict[str, Any]:
        """Whether the Gmail capture is running. Its age is measured by the database clock, never
        an address, a subject or a message."""
        with self._read() as cur:
            cur.execute(
                """
                select authorization_state, last_synced_at,
                       floor(extract(epoch from now() - last_synced_at) / 60)::int
                  from comms.mailbox where address_norm = %s
                """,
                (MAIL_SYNC_MAILBOX,),
            )
            row = cur.fetchone()
        return mail_sync_state(*(row or (None, None, None)))

    def mail_quote_numbers(self) -> dict[str, Any]:
        """Quote numbers the captured Gmail already shows: number, first sighting, message count.
        No address, subject or institution, so every role reads the same body."""
        with self._read() as cur:
            cur.execute(MAIL_QUOTE_NUMBERS_SQL)
            rows = cur.fetchall()
        return {
            "items": [
                {"quote_number": str(n), "first_seen_at": seen.isoformat(), "messages": int(count)}
                for n, seen, count in rows
            ]
        }

    # -- review queue: what the CRM itself cannot show

    def review(self) -> dict[str, Any]:
        with self._read() as cur:
            cur.execute("select pdf_sha256 from crm.quote_revision where pdf_sha256 is not null")
            in_crm = {str(r[0]).lower() for r in cur.fetchall()}
            cur.execute(
                """
                select a.kind, a.resolution, count(*) from evidence.assertion a
                 where a.resolution in ('unresolved', 'ambiguous') group by 1, 2 order by 3 desc
                """
            )
            assertions = [{"kind": r[0], "resolution": r[1], "count": int(r[2])} for r in cur.fetchall()]
            cur.execute(
                """
                select a.id::text as assertion_id, a.value_norm, a.ambiguity_note,
                       a.source_record_id::text
                  from evidence.assertion a
                 where a.kind = 'organization_name' and a.resolution = 'ambiguous'
                 order by a.value_norm
                """
            )
            ambiguous_orgs = self._rows(cur)
        not_imported = [
            {
                **link.as_dict(),
                "ledger_crm_status": link.ledger_crm_status,
                "gmail_url": gmail_url(link.gmail_message_id),
            }
            for link in self._drive.values()
            if link.document_sha256 not in in_crm
        ]
        not_imported.sort(key=lambda d: (d.get("ledger_crm_status") or "", d.get("quote_number") or ""))
        return {
            "archived_not_in_crm": not_imported,
            "open_assertions": assertions,
            "ambiguous_organizations": ambiguous_orgs,
            "drive_configured": self.drive_configured,
        }


#: «BAJA» requests per campaign whose In-Reply-To proved a message that campaign sent.
_BAJA_LINEAGE_BY_CAMPAIGN_SQL = """
select s.campaign_id::text, count(*)
  from evidence.assertion a
  join comms.message m on m.id = case when a.value ? 'lineage_message_id' then (a.value->>'lineage_message_id')::uuid end
  join outbound.send_attempt s on s.id = m.send_attempt_id
 where a.kind = 'unsubscribe_request' and s.campaign_id is not null
 group by 1
"""


def _subject_state(c: Mapping[str, Any]) -> str:
    """`recorded`, or why there is no subject: V1 history never imported it, or a draft has none yet."""
    if c.get("subject"):
        return "recorded"
    return "not_imported" if c.get("origin") == "imported_v1" else "not_set"


#: Brand-id → human-readable name for ``crm.organization_product_line.line_id``.
#: Mirrors the six entries in ``equipment_taxonomy.json`` brands array; pinned here so the
#: product-line read does not load the full taxonomy just to name a line.
_LINE_NAMES: dict[str, str] = {
    "hielscher":       "Hielscher Ultrasonics",
    "ortoalresa":      "Ortoalresa",
    "ika":             "IKA",
    "adam-equipment":  "Adam Equipment",
    "loeser":          "Löser Messtechnik",
    "serva":           "SERVA Electrophoresis",
}


def _compute_campaign_content_state(
    cur: Any,
    campaign_id: str,
    campaign_row: Mapping[str, Any],
) -> tuple[str, str | None, list[dict], dict | None]:
    """Return (html_state, html, contents[], recovery) for a campaign that has no body_html.

    Reads ``outbound.campaign_content`` and ``outbound.campaign_content_message``.  The
    caller must own the read-only cursor and must have already confirmed ``body_html`` is
    ``None`` on the campaign row.  This helper must not be called when ``body_html`` is set
    (the fingerprint path handles that case in ``campaign_archive``).
    """
    # Content rows for this campaign (immutable; SELECT-only)
    cur.execute(
        """
        select cc.id::text, cc.content_kind, cc.variant_no, cc.subject, cc.preheader,
               cc.body_html, cc.body_html_sha256, cc.body_html_normalized_sha256,
               cc.message_count, cc.first_sent_at::text, cc.last_sent_at::text,
               cc.attribution_method, cc.attribution_confidence, cc.attribution_policy_version,
               cc.unmatched_attempt_count,
               (select count(*)::int from outbound.campaign_content_message ccm
                 where ccm.campaign_content_id = cc.id
                   and ccm.send_attempt_id is not null) as linked_attempts
          from outbound.campaign_content cc
         where cc.campaign_id = %s
         order by cc.content_kind, cc.variant_no
        """,
        (campaign_id,),
    )
    cols = [d[0] for d in cur.description]
    raw_contents = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    # Verify each content row: sha256(body_html) == body_html_sha256.
    # Build a separate contents list (body_html excluded) and keep a parallel map of
    # body_html by id so the html-state logic can still read it without touching raw_contents.
    html_by_id: dict[str, str | None] = {}
    contents: list[dict[str, Any]] = []
    for row in raw_contents:
        body = row.get("body_html") or ""
        actual = hashlib.sha256(body.encode()).hexdigest()
        verified = actual == row["body_html_sha256"]
        html_by_id[row["id"]] = row["body_html"] if verified else None
        content_row = dict(row)
        # The variant's HTML travels only when its stored hash recomputes; a mismatch ships null.
        content_row["body_html"] = row["body_html"] if verified else None
        content_row["hash_verified"] = verified
        contents.append(content_row)

    # Recovery metadata: from the origin source_record of any content row for this campaign
    recovery: dict[str, Any] | None = None
    if raw_contents:
        cur.execute(
            """
            select distinct on (sr.id)
                   cc.attribution_policy_version as policy_version,
                   sum(cc.message_count) over () as matched_messages,
                   max(cc.unmatched_attempt_count) over () as unmatched_attempts,
                   sr.payload_sha256 as manifest_sha256
              from outbound.campaign_content cc
              join evidence.source_record sr on sr.id = cc.origin_source_record_id
             where cc.campaign_id = %s
             order by sr.id
             limit 1
            """,
            (campaign_id,),
        )
        rec_row = cur.fetchone()
        if rec_row:
            recovery = {
                "policy_version": rec_row[0],
                "matched_messages": int(rec_row[1]) if rec_row[1] is not None else 0,
                "unmatched_attempts": int(rec_row[2]) if rec_row[2] is not None else 0,
                "manifest_sha256": rec_row[3],
            }

    sent_html_rows = [c for c in contents if c["content_kind"] == "sent_html"]
    draft_rows = [c for c in contents if c["content_kind"] == "historical_draft"]

    if sent_html_rows:
        # Use variant 1; html is None when hash does not verify.
        v1 = next((c for c in sent_html_rows if c["variant_no"] == 1), sent_html_rows[0])
        html_state = "sent_html_archived" if v1["hash_verified"] else "fingerprint_mismatch"
        html = html_by_id.get(v1["id"]) if v1["hash_verified"] else None
        return html_state, html, contents, recovery

    if draft_rows:
        v1_draft = next((c for c in draft_rows if c["variant_no"] == 1), draft_rows[0])
        v1_raw = next((r for r in raw_contents if r["id"] == v1_draft["id"]), None)
        html_state = "historical_draft"
        html = v1_raw["body_html"] if v1_raw else None
        return html_state, html, contents, recovery

    # No content rows — check for an ambiguous attribution source record
    v1_id = campaign_row.get("v1_campaign_id")
    if v1_id:
        cur.execute(
            """
            select 1 from evidence.source_record sr
             where sr.kind = 'migration_manifest'
               and sr.dedupe_key like %s
               and sr.payload->>'decision' = 'ambiguous'
             limit 1
            """,
            (f"migration_manifest:campaign-content-recovery:{v1_id}:%",),
        )
        if cur.fetchone():
            return "ambiguous_attribution", None, contents, recovery

    # Archived imported campaign with no content → not_recovered; native → not_archived
    origin = campaign_row.get("origin", "")
    status = campaign_row.get("status", "")
    if origin == "imported_v1" and status == "archived":
        return "not_recovered", None, contents, recovery

    return ("not_frozen" if status == "draft" else "not_archived"), None, contents, recovery


def _campaign_content_list_state(
    cur: Any,
    campaign_ids: list[str],
) -> dict[str, tuple[bool, str]]:
    """Bulk-compute (has_html_from_content, html_state_stub) for the marketing list.

    Returns a dict keyed by campaign_id. ``has_html`` is True when there is at least one
    ``sent_html`` content row. ``html_state_stub`` is ``'sent_html_archived'`` or
    ``'historical_draft'`` when content exists; callers must fall back to the campaign-row
    logic for campaigns with no content rows.
    """
    if not campaign_ids:
        return {}
    cur.execute(
        """
        select campaign_id::text,
               bool_or(content_kind = 'sent_html') as has_sent_html,
               bool_or(content_kind = 'historical_draft') as has_draft
          from outbound.campaign_content
         where campaign_id = any(%s::uuid[])
         group by 1
        """,
        (campaign_ids,),
    )
    result: dict[str, tuple[bool, str]] = {}
    for cid, has_sent, has_draft in cur.fetchall():
        if has_sent:
            result[cid] = (True, "sent_html_archived")
        elif has_draft:
            result[cid] = (False, "historical_draft")
    return result


def _attach_interests(
    rows: list[dict[str, Any]], address_by_id: dict[str, str], inputs: "AudienceInputs"
) -> None:
    """The recorded equipment-interest evidence of each recipient on the page, by exact stored address.

    The same derivation as the Marketing audience (`compose`); a recipient with none gets an empty
    list, which the dashboard shows as «Sin interés registrado», never as a low interest.
    """
    from origenlab_api.v2.equipment_taxonomy import load_taxonomy
    from origenlab_api.v2.marketing_audience import compose

    composed = compose(load_taxonomy(), inputs)
    by_address: dict[str, list[dict[str, Any]]] = {}
    for p in composed.get("persons", []):
        by_address.setdefault(p["address"], []).extend(p.get("interests") or [])
    for r in rows:
        r["interests"] = by_address.get(address_by_id.get(r["recipient_id"], ""), [])


def read_marketing_audience_inputs(cur: Any) -> "AudienceInputs":
    """Everything `marketing_audience.compose` reads, on the caller's cursor.

    The workspace calls it in a read-only transaction; `freeze-campaign-audience` calls it
    inside its own repeatable-read write transaction, so the audience it freezes is the one
    it evaluated.
    """
    from origenlab_api.v2.marketing_audience import AudienceInputs, CaseFacts, EligibilityFacts

    _rows = CrmWorkspaceRepository._rows
    cur.execute(
        """
        select o.id::text as opportunity_id, o.title, o.stage, o.created_at, o.closed_at,
               org.id::text as organization_id, org.name as organization_name,
               (select min(qr.sent_at) from crm.quote q
                  join crm.quote_revision qr on qr.quote_id = q.id
                 where q.opportunity_id = o.id and qr.sent_at is not null) as first_sent_at,
               coalesce((select array_agg(distinct q.quote_number order by q.quote_number)
                           from crm.quote q where q.opportunity_id = o.id), '{}') as quote_numbers
          from crm.opportunity o
          left join crm.organization org on org.id = coalesce(
                o.organization_id,
                (select oo.organization_id from crm.opportunity_organization oo
                  where oo.opportunity_id = o.id and oo.role = 'requesting_institution'
                    and (oo.valid_to is null or oo.valid_to > current_date)
                  order by oo.valid_from desc limit 1))
        """
    )
    cases = {r["opportunity_id"]: CaseFacts(**r) for r in _rows(cur)}

    cur.execute(
        """
        select i.id::text as interest_id, i.opportunity_id::text as opportunity_id,
               i.confirmation, i.created_at, i.origin_source_record_id::text as origin_source_record_id,
               array_remove(array[i.model_text, i.description, p.name, p.model_number,
                                  mo.name], null) as texts
          from crm.opportunity_interest i
          left join catalog.product p on p.id = i.product_id
          left join crm.organization mo
                 on mo.id = coalesce(i.manufacturer_organization_id, p.manufacturer_organization_id)
         where i.withdrawn_at is null
        """
    )
    interests = _rows(cur)
    cur.execute(
        """
        select op.opportunity_id::text as opportunity_id, cp.value_norm as address
          from crm.opportunity_participant op
          join crm.contact_point cp
            on cp.id = op.contact_point_id
            or (op.contact_point_id is null and cp.person_id = op.person_id and cp.kind = 'email')
         where cp.kind = 'email' and (op.valid_to is null or op.valid_to > current_date)
        """
    )
    participants: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in _rows(cur):
        participants[r["opportunity_id"]].append({"address": r["address"]})
    for row in interests:
        row["participants"] = participants.get(row["opportunity_id"], [])

    # A sent quotation's Gmail message, reached through the revision recorded from it or
    # through an evidence link on the case.
    # Only live evidence: a link a person removed, or a record rejected or quarantined, is not interest.
    cur.execute(
        """
        select distinct on (s.id, link.opportunity_id)
               s.id::text as source_record_id, link.opportunity_id::text as opportunity_id,
               s.payload->>'subject_raw' as subject,
               array(select d->>'filename'
                       from jsonb_array_elements(case when jsonb_typeof(s.payload->'documents') = 'array'
                                                      then s.payload->'documents' else '[]'::jsonb end) d
                      where d->>'filename' is not null) as filenames,
               coalesce(link.sent_at::text, s.payload->>'sent_at') as sent_at,
               s.payload->>'recipients' as recipients
          from evidence.source_record s
          join (
                select qr.origin_source_record_id as source_record_id, q.opportunity_id, qr.sent_at
                  from crm.quote_revision qr join crm.quote q on q.id = qr.quote_id
                 where qr.origin_source_record_id is not null
                union all
                select e.source_record_id, e.opportunity_id, null::timestamptz
                  from crm.opportunity_evidence e
                 where e.source_record_id is not null and e.unlinked_at is null
               ) link on link.source_record_id = s.id
         where s.kind = 'gmail_message'
           and s.review_status <> 'rejected'
           and not s.is_quarantined
         order by s.id, link.opportunity_id, link.sent_at nulls last
        """
    )
    evidence = _rows(cur)

    facts = EligibilityFacts()
    cur.execute(
        """
        select cp.id::text as id, cp.value_norm as address, cp.usage,
               cp.person_id::text as person_id, cp.organization_id::text as organization_id,
               pe.display_name as person_name
          from crm.contact_point cp
          left join crm.person pe on pe.id = cp.person_id
         where cp.kind = 'email'
        """
    )
    facts.contact_points = {r["address"]: r for r in _rows(cur)}
    # An address block is an unsubscribe when its reason says so or when a «BAJA» was linked to
    # it (W10): the same rule as outbound.marketing_contact_refusals, the send-time contract.
    cur.execute(
        """
        select c.scope, c.value_norm, c.kind,
               c.kind = 'block' and c.scope = 'address' and (c.reason = 'unsubscribe' or exists (
                 select 1 from evidence.assertion a
                  where a.kind = 'unsubscribe_request' and a.resolved_kind = 'contact_control'
                    and a.resolved_id = c.id)) as unsubscribe
          from outbound.contact_control c
         where c.kind = 'prior_contact'
            or (c.kind = 'block' and c.purpose in ('all', 'marketing'))
            or (c.kind = 'cooldown' and c.until_at > now())
        """
    )
    for scope, value, kind, unsubscribe in cur.fetchall():
        if kind == "block" and unsubscribe:
            facts.unsubscribed_addresses.add(value)
        elif kind == "block":
            (facts.blocked_addresses if scope == "address" else facts.blocked_domains).add(value)
        elif kind == "cooldown":
            facts.cooldown_addresses.add(value)
        else:
            facts.prior_contact_addresses.add(value)
    # A «BAJA» held for review (W10): the same exact-address hold as the send-time contract.
    cur.execute(
        """
        select distinct value_norm from evidence.assertion
         where kind = 'unsubscribe_request' and resolution = 'unresolved'
        """
    )
    facts.unsubscribe_pending_addresses = {r[0] for r in cur.fetchall()}
    # W12: what a recontact reviewer is shown — the recorded sources of each prior contact, the
    # last accepted send (date and campaign), or, for an imported V1 recipient with no attempt,
    # its campaign without a date. Nothing is inferred: an unknown date stays unknown.
    cur.execute(
        """
        select value_norm, source, reason, created_at::text
          from outbound.contact_control
         where kind = 'prior_contact' and scope = 'address'
         order by value_norm, created_at, source
        """
    )
    for value, source, reason, recorded_at in cur.fetchall():
        facts.prior_contact_details.setdefault(value, {"sources": []})["sources"].append(
            {"source": source, "reason": reason, "recorded_at": recorded_at})
    cur.execute(
        """
        select distinct on (r.address_norm) r.address_norm, c.id::text, c.name
          from outbound.campaign_recipient r join outbound.campaign c on c.id = r.campaign_id
         where r.state in ('sent', 'bounced', 'replied', 'unsubscribed')
         order by r.address_norm, r.updated_at desc, c.id
        """
    )
    for value, campaign_id, campaign_name in cur.fetchall():
        if value in facts.prior_contact_details:
            facts.prior_contact_details[value].update(campaign_id=campaign_id, campaign_name=campaign_name)
    cur.execute(
        """
        select distinct on (a.address_norm) a.address_norm, a.accepted_at::text, c.id::text, c.name
          from outbound.send_attempt a left join outbound.campaign c on c.id = a.campaign_id
         where a.submission_state = 'accepted'
         order by a.address_norm, a.accepted_at desc, a.id
        """
    )
    for value, accepted_at, campaign_id, campaign_name in cur.fetchall():
        if value in facts.prior_contact_details:
            facts.prior_contact_details[value].update(
                last_contact_at=accepted_at, campaign_id=campaign_id, campaign_name=campaign_name)
    cur.execute(
        """
        select address_norm from outbound.send_attempt
         where error_class = 'invalid_address' or bounce_class = 'hard'
        union
        select address_norm from outbound.campaign_recipient
         where 'invalid_address' = any(exclusion_reasons)
        """
    )
    facts.invalid_addresses = {r[0] for r in cur.fetchall()}
    cur.execute(
        """
        select organization_id::text from crm.organization_relationship
         where role in ('supplier', 'manufacturer')
           and (valid_to is null or valid_to > current_date)
        union
        select organization_id::text from crm.opportunity_organization
         where role in ('supplier', 'manufacturer')
           and (valid_to is null or valid_to > current_date)
        """
    )
    facts.supplier_organization_ids = {r[0] for r in cur.fetchall()}
    cur.execute(
        """
        select d.domain_norm from crm.organization_domain d
         where d.scope = 'exclusive' and d.organization_id::text = any(%s)
        """,
        (list(facts.supplier_organization_ids),),
    )
    facts.supplier_domains = {r[0] for r in cur.fetchall()}
    cur.execute(
        """
        select value_norm from evidence.assertion
         where kind = 'supplier_candidate' and resolution in ('unresolved', 'ambiguous')
        """
    )
    facts.candidate_supplier_domains = {r[0] for r in cur.fetchall()}
    cur.execute(
        """
        select kind || ':' || resolution, count(*) from evidence.assertion
         where resolution in ('unresolved', 'ambiguous')
           and kind in ('organization_name', 'contact_address', 'affiliation', 'supplier_candidate')
         group by 1
        """
    )
    review_queue = {k: int(n) for k, n in cur.fetchall()}

    return AudienceInputs(
        cases=cases,
        interests=interests,
        quotation_evidence=evidence,
        eligibility=facts,
        review_queue=review_queue,
    )
