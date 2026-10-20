"""`open-tender-case`: a tender case from a Mercado Público code or link (spec §6.1, §6.6, §6.10).

A tender is a `crm.opportunity` of `kind = 'tender'` with its own `tender_status`, opened at
`preparando`. The command runs in two halves:

1. **Outside the transaction** (`prepare_open_tender_case`): the reference is parsed into one code
   and the tender's detail is read from the official API. A network call never holds a database
   transaction, and the detail is `None` both without a ticket and with a rejected one — the
   operator then types the buyer.
2. **In one transaction** (`open_tender_case`, through `CommandTransaction`): the notice is
   upserted on `codigo_externo`; the buyer is resolved (ChileCompra buyer code, then RUT, then an
   exact case-insensitive name) or registered `machine_proposed` with its identifiers; the case is
   inserted (`kind = 'tender'`, `tender_status = 'preparando'`, stage `lead`) with that institution
   as the requesting one; the notice becomes its `origin` evidence and is marked promoted. Events:
   `opportunity.created` and `notice.promoted` (plus the institution and evidence events the
   shared writers append).

Refusals (codes Task 15's UI maps):

| Status | Code | When |
|---|---|---|
| 422 | `tender_reference_invalid` / `tender_code_not_in_link` | the reference does not parse |
| 422 | `buyer_rut_invalid` | the typed buyer RUT fails its check digit |
| 422 | `buyer_required` | no detail and no typed buyer; `details: {"fetched": false}` |
| 409 | `tender_case_exists` | the notice's promoted case is still open; `details: {"opportunity_id"}` |
| 409 | `organization_name_ambiguous` | two or more active institutions share the buyer's exact name; `details: {"candidates"}` |
| 422 | `supplier_exception_required` | the buyer holds a current supplier relationship with OrigenLab (`_add_case_organization`) |
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Callable
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

from origenlab_api.v2.case_command_repository import (
    V2CaseCommandRepository,
    lock_new_organization_name,
    same_name_or_domain_organization,
)
from origenlab_api.v2.command_core import DEFAULT_COMMAND_TIMEOUT_MS, json_payload
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.org_web_suggestions import valid_rut
from origenlab_api.v2.quote_document_identity import normalize_rut
from origenlab_api.v2.quotes.inputs import no_provider
from origenlab_api.v2.tenders.annexes import (
    ATTACH_TENDER_ANNEXES,
    REMOVE_TENDER_ANNEX,
    attach_tender_annexes,
    remove_tender_annex,
)
from origenlab_api.v2.tenders.bidder import (
    ADD_BIDDER_DOCUMENT,
    RETIRE_BIDDER_DOCUMENT,
    SAVE_BIDDER_PROFILE,
    add_bidder_document,
    retire_bidder_document,
    save_bidder_profile,
)
from origenlab_api.v2.tenders.codes import TenderReferenceInvalid, parse_tender_reference, portal_url
from origenlab_api.v2.tenders.mercado_publico import TenderDetail, fetch_tender_detail
from origenlab_api.v2.tenders.preparation import (
    CREATE_TENDER_OFFER_QUOTE,
    PREPARE_TENDER_BID,
    create_tender_offer_quote,
    request_bid_preparation,
)
from origenlab_api.v2.tenders.status import SET_TENDER_STATUS, set_tender_status

OPEN_TENDER_CASE = "open-tender-case"
TENDER_KIND = "tender"
OPENING_TENDER_STATUS = "preparando"
_TITLE_MAX = 200
#: Mercado Público prints its dates in Chilean time, without an offset.
_CHILE = ZoneInfo("America/Santiago")

_REFERENCE_MESSAGES = {
    "tender_reference_invalid": "Eso no es un código de licitación ni un enlace de Mercado Público.",
    "tender_code_not_in_link": "El enlace no trae el código de la licitación; copia el código (p. ej. 1234567-12-LE26).",
}


class TenderBuyer(BaseModel):
    """The buyer as the operator typed it, used when the API gave no detail."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: Annotated[str, Field(min_length=1, max_length=200)]
    rut: Annotated[str | None, Field(max_length=20)] = None

    @field_validator("rut")
    @classmethod
    def _blank_rut_is_none(cls, value: str | None) -> str | None:
        return value or None


class OpenTenderCaseBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reference: Annotated[str, Field(min_length=1, max_length=2000)]
    buyer: TenderBuyer | None = None


def _typed_rut(raw: str | None) -> str | None:
    """The typed RUT normalised (`61111111-8`), None when none was typed; a wrong check digit is
    refused 422 `buyer_rut_invalid` rather than dropped."""
    if raw is None:
        return None
    rut = normalize_rut(raw)
    if not valid_rut(rut):
        raise CommandRefused(422, "buyer_rut_invalid", "El RUT de la institución compradora no es válido.")
    return rut


def prepare_open_tender_case(
    body: OpenTenderCaseBody,
    *,
    ticket: str | None,
    fetch: Callable[..., TenderDetail | None] = fetch_tender_detail,
) -> dict[str, Any]:
    """Parse the reference and read the detail — everything that happens before the transaction.

    The detail is looked up on every call, a replay included: a replay answers the stored response
    regardless, and the lookup is what the operator's first request needed anyway.
    """
    try:
        code = parse_tender_reference(body.reference)
    except TenderReferenceInvalid as exc:
        raise CommandRefused(422, exc.code, _REFERENCE_MESSAGES[exc.code]) from exc
    detail = fetch(code, ticket=ticket)
    typed = body.buyer
    return {
        "code": code,
        "detail": detail,
        "typed_buyer_name": typed.name if typed is not None else None,
        "typed_buyer_rut": _typed_rut(typed.rut) if typed is not None else None,
    }


# ------------------------------------------------------------------ the transaction


def _row(cur: Any) -> dict[str, Any] | None:
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([d[0] for d in cur.description], row, strict=True))


def _chile_time(raw: str | None) -> datetime | None:
    """An API date as an aware datetime: an offset-less one is Chilean time; unreadable is None."""
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=_CHILE)


def _upsert_notice(cur: Any, code: str, detail: TenderDetail | None, buyer_name: str) -> dict[str, Any]:
    """The notice of this code, inserted or — when the API answered — refreshed with what it read.

    Without a detail an existing notice keeps its head (it may hold the poller's full reading); a
    new one gets the operator's head `{"source": "operator", "code": …}`. With one, the previous
    head is appended to `head_history` when it differs, and `source_status` / `published_at` are
    refreshed.
    """
    head = detail.head if detail is not None else {"source": "operator", "code": code}
    cur.execute(
        """
        insert into procurement.notice
            (codigo_externo, buyer_name, buyer_code, title, source_status, published_at, closes_at, head)
        values (%(code)s, %(buyer_name)s, %(buyer_code)s, %(title)s, %(source_status)s,
                %(published_at)s, %(closes_at)s, %(head)s::jsonb)
        on conflict (codigo_externo) do update
           set head = case when %(fetched)s then excluded.head else notice.head end,
               head_history = case when %(fetched)s and notice.head is distinct from excluded.head
                                   then notice.head_history || jsonb_build_array(notice.head)
                                   else notice.head_history end,
               title = case when %(fetched)s then excluded.title else coalesce(notice.title, excluded.title) end,
               buyer_name = case when %(fetched)s then excluded.buyer_name
                                 else coalesce(notice.buyer_name, excluded.buyer_name) end,
               buyer_code = coalesce(excluded.buyer_code, notice.buyer_code),
               closes_at = coalesce(excluded.closes_at, notice.closes_at),
               source_status = case when %(fetched)s then excluded.source_status else notice.source_status end,
               published_at = case when %(fetched)s then coalesce(excluded.published_at, notice.published_at)
                                   else notice.published_at end,
               last_seen_at = case when %(fetched)s then now() else notice.last_seen_at end,
               updated_at = now()
        returning id::text as id, promoted_opportunity_id::text as promoted_opportunity_id
        """,
        {
            "code": code,
            "buyer_name": buyer_name,
            "buyer_code": detail.buyer_code if detail is not None else None,
            "title": detail.title if detail is not None else None,
            "source_status": detail.source_status if detail is not None else None,
            "published_at": _chile_time(detail.published_at) if detail is not None else None,
            "closes_at": _chile_time(detail.closes_at) if detail is not None else None,
            "head": json_payload(head),
            "fetched": detail is not None,
        },
    )
    notice = _row(cur)
    assert notice is not None  # noqa: S101 - `returning` on an upsert always answers
    return notice


def _open_case_of_notice(cur: Any, opportunity_id: str | None) -> str | None:
    if opportunity_id is None:
        return None
    cur.execute("select id::text from crm.opportunity where id = %s and closed_at is null", (opportunity_id,))
    row = cur.fetchone()
    return row[0] if row else None


def _organization_by_identifier(cur: Any, scheme: str, value: str | None) -> str | None:
    if not value:
        return None
    cur.execute(
        """
        select o.id::text from crm.external_identifier e join crm.organization o on o.id = e.organization_id
         where e.scheme = %s and e.value_norm = %s
           and o.status = 'active' and o.merged_into_organization_id is null
        """,
        (scheme, value),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _add_identifier(cur: Any, organization_id: str, scheme: str, value: str) -> bool:
    """One identifier on the institution; a value another subject already holds is left there."""
    cur.execute(
        """
        insert into crm.external_identifier (scheme, value_norm, organization_id)
        values (%s, %s, %s)
        on conflict (scheme, value_norm) do nothing
        returning id
        """,
        (scheme, value, organization_id),
    )
    return cur.fetchone() is not None


def _resolve_buyer(
    repo: V2CaseCommandRepository,
    cur: Any,
    operator: OperatorIdentity,
    receipt_id: str,
    *,
    name: str,
    buyer_code: str | None,
    rut: str | None,
    note: str,
) -> tuple[str, list[str]]:
    """The buyer's institution — known by buyer code, RUT or exact name — else a new one
    `machine_proposed` (spec §6.6, as the mail rules create one). An institution found by name or
    created here gains the buyer code and RUT it lacks, so the next open finds it by identifier."""
    from origenlab_api.v2.mail_rules_repository import register_machine_organization

    found = _organization_by_identifier(cur, "chilecompra_buyer_code", buyer_code) or _organization_by_identifier(
        cur, "rut", rut
    )
    if found is not None:
        return found, []
    lock_new_organization_name(cur, name)
    same = same_name_or_domain_organization(cur, name, None)
    events: list[str] = []
    if same is not None:
        organization_id = same["id"]
    else:
        made = register_machine_organization(
            repo, cur, operator, {"name": name, "domain": None, "origin_source_record_id": None, "note": note},
            receipt_id,
        )
        organization_id, events = made["organization_id"], list(made["event_ids"])
    if buyer_code:
        _add_identifier(cur, organization_id, "chilecompra_buyer_code", buyer_code)
    if rut:
        _add_identifier(cur, organization_id, "rut", rut)
    return organization_id, events


def open_tender_case(
    repo: V2CaseCommandRepository, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
) -> dict[str, Any]:
    code: str = fields["code"]
    detail: TenderDetail | None = fields["detail"]
    buyer_name = (detail.buyer_name if detail is not None else None) or fields["typed_buyer_name"]
    if not buyer_name:
        raise CommandRefused(
            422,
            "buyer_required",
            "No se pudo leer la licitación en Mercado Público: escribe la institución compradora.",
            details={"fetched": detail is not None},
        )
    buyer_code = detail.buyer_code if detail is not None else None
    rut = (detail.buyer_rut if detail is not None else None) or fields["typed_buyer_rut"]
    note = f"Licitación {code} abierta desde Mercado Público"

    # Two opens of one code queue here; the second sees the first's promotion.
    cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"tender:open:{code}",))
    notice = _upsert_notice(cur, code, detail, buyer_name)
    existing = _open_case_of_notice(cur, notice["promoted_opportunity_id"])
    if existing is not None:
        raise CommandRefused(
            409,
            "tender_case_exists",
            "Esta licitación ya tiene un caso abierto.",
            details={"opportunity_id": existing},
        )

    organization_id, events = _resolve_buyer(
        repo, cur, operator, receipt_id, name=buyer_name, buyer_code=buyer_code, rut=rut, note=note
    )

    title = f"Licitación {code} — {(detail.title if detail is not None else None) or buyer_name}"[:_TITLE_MAX]
    opened = repo._open_case_for_client(
        cur, operator, receipt_id, title=title, organization_id=organization_id, note=note,
        kind=TENDER_KIND, tender_status=OPENING_TENDER_STATUS,
        extra_payload={"kind": TENDER_KIND, "tender_status": OPENING_TENDER_STATUS, "notice_id": notice["id"]},
    )
    case_id = opened["opportunity_id"]
    events.extend(opened["event_ids"])
    cur.execute("select confirmation from crm.organization where id = %s", (organization_id,))
    confirmation = cur.fetchone()[0]

    evidence_id, evidence_event = repo._link_evidence_row(
        cur, case_id=case_id, subject_column="notice_id", subject_id=notice["id"], relation="origin",
        operator=operator, receipt_id=receipt_id, note=note,
    )
    events.append(evidence_event)

    cur.execute(
        "update procurement.notice set promoted_opportunity_id = %s, updated_at = now() where id = %s",
        (case_id, notice["id"]),
    )
    events.append(repo._append_event(
        cur, aggregate_kind="notice", aggregate_id=notice["id"], event_type="notice.promoted",
        payload={"codigo_externo": code, "opportunity_id": case_id, "fetched": detail is not None,
                 "previous_opportunity_id": notice["promoted_opportunity_id"]},
        operator=operator, receipt_id=receipt_id,
    ))

    return {
        "command": OPEN_TENDER_CASE,
        "opportunity_id": case_id,
        "opportunity_version": int(opened["opportunity_version"]),
        "notice_id": notice["id"],
        "organization_id": organization_id,
        "organization_confirmation": confirmation,
        "opportunity_evidence_id": evidence_id,
        "fetched": detail is not None,
        "portal_url": portal_url(code),
        "event_ids": events,
    }


class V2TenderCommandRepository(V2CaseCommandRepository):
    """The tender commands: the case writers (`_open_case_for_client`, `_link_evidence_row`) with
    their own dispatch, so no general case command can be called through this repository."""

    _HANDLERS = {
        OPEN_TENDER_CASE: open_tender_case,
        SET_TENDER_STATUS: set_tender_status,
        SAVE_BIDDER_PROFILE: save_bidder_profile,
        ADD_BIDDER_DOCUMENT: add_bidder_document,
        ATTACH_TENDER_ANNEXES: attach_tender_annexes,
        REMOVE_TENDER_ANNEX: remove_tender_annex,
        RETIRE_BIDDER_DOCUMENT: retire_bidder_document,
        PREPARE_TENDER_BID: request_bid_preparation,
        CREATE_TENDER_OFFER_QUOTE: create_tender_offer_quote,
    }

    def __init__(self, connect: Any, dsn: str, statement_timeout_ms: int = DEFAULT_COMMAND_TIMEOUT_MS, *,
                 fx_resolver: Any = no_provider,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        super().__init__(connect, dsn, statement_timeout_ms)
        # What the Cotizador's handlers read off their repository (`create-tender-offer-quote` calls
        # them in its own transaction). Like the quote commands in production (`no_provider`), no
        # write transaction asks a provider: only stored rates count, and a matched line whose cost
        # needs another is saved free (`preparation._without_exchange_rate`).
        self._fx_resolver = fx_resolver
        self._clock = clock

    def case_kind(self, opportunity_id: str) -> str | None:
        """The `kind` of the case, or None if there is none. A cheap read, no lock: a route uses it
        to refuse before storing objects; the command re-checks under its lock."""
        with self._connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute("select kind from crm.opportunity where id = %s::uuid", (opportunity_id,))
            row = cur.fetchone()
        return row[0] if row else None


__all__ = [
    "OPEN_TENDER_CASE",
    "OpenTenderCaseBody",
    "TenderBuyer",
    "V2TenderCommandRepository",
    "open_tender_case",
    "prepare_open_tender_case",
]
