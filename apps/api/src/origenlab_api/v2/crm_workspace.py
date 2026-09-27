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

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

GMAIL_MESSAGE_URL = "https://mail.google.com/mail/u/0/#all/{}"
DRIVE_FOLDER_URL = "https://drive.google.com/drive/folders/{}"
DRIVE_FILE_URL = "https://drive.google.com/file/d/{}/view"

# Per-entity provenance: what a count of zero (or a partial count) means. ``imported`` = a
# migration or command has written it and the count is the real state; ``partial`` = rows exist
# but a known part is missing; ``not_imported`` = no import has been built or run, so zero says
# nothing about the business; ``no_write_path`` = V2 has no command that creates it yet.
ENTITY_NOTES: dict[str, dict[str, str]] = {
    "opportunities": {
        "provenance": "imported",
        "note": "Casos comerciales: importación histórica de cotizaciones + casos abiertos a mano.",
    },
    "quotes": {"provenance": "imported", "note": "Cotizaciones históricas con número impreso."},
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
        "provenance": "not_imported",
        "note": "Ninguna persona importada: el volcado V1 commercial.* no existe localmente y "
        "ninguna evidencia se ha promovido a persona.",
    },
    "affiliations": {"provenance": "not_imported", "note": "Depende de personas; no importado."},
    "tasks": {
        "provenance": "no_write_path",
        "note": "V2 aún no tiene comando para crear tareas; las tareas V1 no se migraron.",
    },
    "activities": {
        "provenance": "no_write_path",
        "note": "V2 aún no registra actividades; las actividades V1 no se migraron.",
    },
    "messages": {
        "provenance": "not_imported",
        "note": "comms.message vacío: no existe el sincronizador de Gmail. Los correos de las "
        "cotizaciones están como evidencia (evidence.source_record).",
    },
    "products": {"provenance": "not_imported", "note": "El catálogo V2 no se ha cargado."},
    "campaigns": {
        "provenance": "imported",
        "note": "Campañas históricas importadas como registro de envío (archivadas).",
    },
    "campaign_replies": {
        "provenance": "not_imported",
        "note": "Las respuestas a campañas no se han importado; cero no significa sin respuestas.",
    },
    "drive_links_in_crm": {
        "provenance": "not_imported",
        "note": "crm.external_identifier está vacío: los enlaces de Drive viven sólo en los "
        "registros locales del archivo de casos.",
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
    "drive_links_in_crm": "select count(*) from crm.external_identifier",
}

# Codes that stop a case from moving until an operator decides something. Everything else in a
# card's ``attention`` list is informational (``pending``), never a blocker.
BLOCKING_CODES = frozenset(
    {"shared_printed_number", "canonical_undetermined", "no_requesting_institution"}
)

ATTENTION_LABELS_ES: dict[str, str] = {
    "shared_printed_number": "El mismo número impreso aparece en otro caso",
    "canonical_undetermined": "Hay más de una revisión vigente: no se sabe cuál es la canónica",
    "no_requesting_institution": "Caso sin institución solicitante",
    "document_not_in_drive": "El PDF de la última revisión no está en el archivo de Drive",
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
    parts = [p.strip() for p in recipients.replace(";", ",").split(",") if p.strip()]
    return (parts[0] if parts else None), len(parts)


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
            }
        elif requesting:
            organization = {
                "organization_id": requesting[0]["organization_id"],
                "name": requesting[0]["name"],
                "confirmation": requesting[0].get("confirmation"),
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
                contact = {"source": "gmail_recipient", "name": None, "address": address, "others": max(n - 1, 0)}
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
                attention.append("document_not_in_drive")
            if latest["gmail"] is None:
                attention.append("no_gmail_evidence")

        seen: set[str] = set()
        ordered = [c for c in attention if not (c in seen or seen.add(c))]
        blocked = [c for c in ordered if c in BLOCKING_CODES]
        cards.append(
            {
                "opportunity_id": oid,
                "title": opp["title"],
                "stage": opp["stage"],
                "created_at": opp.get("created_at"),
                "updated_at": opp.get("updated_at"),
                "closed_at": opp.get("closed_at"),
                "close_reason": opp.get("close_reason"),
                "organization": organization,
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
                "attention": [
                    {"code": c, "label": ATTENTION_LABELS_ES[c], "blocking": c in BLOCKING_CODES}
                    for c in ordered
                ],
                "status": "blocked" if blocked else ("pending" if any(c != "no_crm_contact" for c in ordered) else "ok"),
                "next_action": suggest_next_action(opp, blocked, latest),
            }
        )
    return cards


def suggest_next_action(
    opp: Mapping[str, Any], blocked: list[str], latest: Mapping[str, Any] | None
) -> dict[str, Any]:
    """A deterministic suggestion, labelled as such — ``crm.task`` holds no stored next action."""
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
        when = _short_date(latest.get("sent_at"))
        text = f"Hacer seguimiento de {latest['quote_number']}" + (f" (enviada {when})" if when else "")
    else:
        text = "Revisar el caso"
    return {"text": text, "source": "suggested", "due_at": None}


def _short_date(value: Any) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%d-%m-%Y")
    except ValueError:
        return None


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
    out.sort(key=lambda f: (f["quote_numbers"][:1] or [""])[0], reverse=True)
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
            counts: dict[str, int] = {}
            for key, sql in _COUNT_SQL.items():
                cur.execute(sql)
                counts[key] = int(cur.fetchone()[0])
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
            cur.execute("select pdf_sha256 from crm.quote_revision where pdf_sha256 is not null")
            rev_shas = {str(r[0]).lower() for r in cur.fetchall()}
        entities = [
            {"key": k, "count": counts[k], **ENTITY_NOTES[k]} for k in _COUNT_SQL
        ]
        return {
            "entities": entities,
            "opportunities_by_stage": by_stage,
            "organizations_by_confirmation": org_confirmation,
            "contact_points_linked": {"organization": int(linked_org), "person": int(linked_person)},
            "assertions": assertions,
            "drive_archive": {
                "configured": self.drive_configured,
                "documents": len(self._drive),
                "revisions_with_drive_file": len(rev_shas & set(self._drive)),
                "revisions_total": len(rev_shas),
            },
        }

    # -- pipeline

    def pipeline(self) -> dict[str, Any]:
        with self._read() as cur:
            cur.execute(
                """
                select op.id::text as opportunity_id, op.title, op.stage,
                       op.organization_id::text as organization_id, o.name as organization_name,
                       o.confirmation as organization_confirmation,
                       op.created_at::text, op.updated_at::text, op.closed_at::text, op.close_reason
                  from crm.opportunity op
                  left join crm.organization o on o.id = op.organization_id
                 order by op.updated_at desc, op.id
                """
            )
            opps = self._rows(cur)
            cur.execute(
                """
                select oo.opportunity_id::text, oo.organization_id::text, oo.role,
                       o.name, oo.confirmation
                  from crm.opportunity_organization oo
                  join crm.organization o on o.id = oo.organization_id
                 where oo.valid_to is null
                """
            )
            case_orgs = self._rows(cur)
            cur.execute(
                """
                select q.id::text as quote_id, q.opportunity_id::text, q.quote_number, q.number_origin
                  from crm.quote q
                """
            )
            quotes = self._rows(cur)
            cur.execute(
                """
                select qr.id::text as revision_id, qr.quote_id::text, qr.revision_no, qr.status,
                       qr.origin, qr.sent_at::text, qr.pdf_sha256, qr.superseded_by_revision_no,
                       qr.origin_source_record_id::text
                  from crm.quote_revision qr
                """
            )
            revisions = self._rows(cur)
            cur.execute(
                """
                select sr.id::text as source_record_id,
                       sr.payload->>'gmail_message_id' as gmail_message_id,
                       sr.payload->>'gmail_thread_id' as gmail_thread_id,
                       sr.payload->>'recipients' as recipients,
                       sr.payload->>'subject_raw' as subject_raw,
                       sr.payload->'documents' as documents
                  from evidence.source_record sr
                 where sr.id in (select origin_source_record_id from crm.quote_revision
                                  where origin_source_record_id is not null)
                """
            )
            sources = {}
            for row in self._rows(cur):
                docs = row.get("documents")
                if isinstance(docs, str):
                    docs = json.loads(docs)
                row["documents"] = [
                    {"sha256": d.get("sha256"), "filename": d.get("filename")} for d in (docs or [])
                ]
                sources[row["source_record_id"]] = row
            cur.execute(
                """
                select p.opportunity_id::text, pe.display_name as name, p.role, p.is_primary
                  from crm.opportunity_participant p
                  left join crm.person pe on pe.id = p.person_id
                 where p.valid_to is null
                 order by p.is_primary desc
                """
            )
            participants = self._rows(cur)
        cards = compose_pipeline(opps, case_orgs, quotes, revisions, sources, participants, self._drive)
        return {
            "items": cards,
            "total": len(cards),
            "drive_configured": self.drive_configured,
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
            cur.execute(
                """
                select a.value_norm as domain, a.value->>'trade_name' as trade_name,
                       a.resolution, count(*) as mentions
                  from evidence.assertion a
                 where a.kind = 'supplier_candidate'
                 group by 1, 2, 3
                 order by 2 nulls last, 1
                """
            )
            candidates = self._rows(cur)
        for r in on_cases + candidates:
            for k in ("cases", "mentions"):
                if k in r:
                    r[k] = int(r[k])
        return {"on_cases": on_cases, "candidates": candidates}

    # -- marketing

    def marketing(self) -> dict[str, Any]:
        with self._read() as cur:
            cur.execute(
                """
                select c.id::text as campaign_id, c.name, c.status, c.subject,
                       c.approved_at::text, c.created_at::text,
                       (select min(s.accepted_at)::text from outbound.send_attempt s where s.campaign_id = c.id) as first_sent_at,
                       (select max(s.accepted_at)::text from outbound.send_attempt s where s.campaign_id = c.id) as last_sent_at
                  from outbound.campaign c
                 order by c.created_at desc
                """
            )
            campaigns = self._rows(cur)
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
            cur.execute("select kind, scope, count(*) from outbound.contact_control group by 1, 2 order by 1, 2")
            controls = [{"kind": r[0], "scope": r[1], "count": int(r[2])} for r in cur.fetchall()]
        for c in campaigns:
            c["recipients_by_state"] = recip.get(c["campaign_id"], {})
            c["send_attempts"] = attempts.get(c["campaign_id"], [])
            c["replies_recorded"] = replies.get(c["campaign_id"], 0)
        return {
            "campaigns": campaigns,
            "contact_controls": controls,
            "replies_note": ENTITY_NOTES["campaign_replies"]["note"],
        }

    # -- drive archive

    def drive_archive(self) -> dict[str, Any]:
        with self._read() as cur:
            cur.execute(
                """
                select lower(qr.pdf_sha256) as sha, qr.revision_no, q.quote_number,
                       op.id::text as opportunity_id, op.title as opportunity_title,
                       o.name as organization_name
                  from crm.quote_revision qr
                  join crm.quote q on q.id = qr.quote_id
                  join crm.opportunity op on op.id = q.opportunity_id
                  left join crm.organization o on o.id = op.organization_id
                 where qr.pdf_sha256 is not null
                """
            )
            crm = {r["sha"]: {k: v for k, v in r.items() if k != "sha"} for r in self._rows(cur)}
        out = compose_drive_archive(self._drive, crm)
        out["configured"] = self.drive_configured
        out["crm_revisions_without_drive_file"] = sorted(
            ({"sha256": s, **v} for s, v in crm.items() if s not in self._drive),
            key=lambda r: r["quote_number"],
        )
        return out

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
