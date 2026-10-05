"""«Enviar prueba»: send one campaign's stored email to one address, as contacto@, as a test.

Two transactions around one Gmail call, because an email cannot be rolled back:
1. claim the receipt (idempotency), check the shared limits under an advisory lock, resolve the
   content — committed, so the attempt counts and a retry with the same key replays or waits;
2. send through Gmail, outside any transaction;
3. complete the receipt with the Gmail message id, or mark it failed with the error class.
A crash between 2 and 3 leaves the receipt `in_progress`: a retry with that key answers 409 and
cannot send twice; a new key may send again — at most one duplicate, on a human's second click.

Admin only. The request names a campaign and an address; it never carries HTML or a subject.
An address that asked to be removed or is blocked is refused in step 1, before anything is
recorded: an address-scope `outbound.contact_control` block for purpose `all` or `marketing`
(every unsubscribe is one) or an unresolved «BAJA» held for review (WORKFLOWS.md §W10). A test has
no campaign recipient, so it reads those live facts itself instead of
`outbound.marketing_contact_refusals`; a prior contact or a cooldown is no block.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from origenlab_api.errors import DatabaseRefusal
from origenlab_api.v2.command_core import (
    DEFAULT_COMMAND_LOCK_TIMEOUT_MS,
    DEFAULT_COMMAND_TIMEOUT_MS,
    CommandTransaction,
    json_payload,
)
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.gmail_send import (
    SENDER_ADDRESS,  # noqa: F401 - re-exported for the marketing read
    TEST_SUBJECT_PREFIX,
    GmailSendError,
    build_test_message,
    valid_test_address,
)
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.v1_lane_campaigns import load_v1_lane_campaigns, load_v1_lane_html

_log = logging.getLogger(__name__)

SEND_CAMPAIGN_TEST = "send-campaign-test"
TEST_SENDS_PER_HOUR = 10
TEST_SENDS_PER_DAY = 30
HISTORY_LIMIT = 20

FAILURE_MESSAGE_ES = {
    "token_refresh_failed": "No se pudo enviar: el permiso de Gmail venció o fue revocado.",
    "gmail_rejected": "Gmail rechazó el mensaje.",
    "network": "No se pudo contactar a Gmail.",
}

#: Gmail accepted the message and recording it failed. Not «nothing was written, retry»: the
#: email is out, and a resend would send it again.
SENT_NOT_RECORDED_ES = "La prueba se envió, pero no quedó registrada. No la reenvíes; avisa al administrador."


class SendCampaignTestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: UUID | None = None
    v1_lane_key: Annotated[str | None, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")] = None
    to: Annotated[str, Field(max_length=300)]

    @field_validator("to")
    @classmethod
    def _one_plain_address(cls, value: str) -> str:
        return valid_test_address(value)

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "SendCampaignTestBody":
        if (self.campaign_id is None) == (self.v1_lane_key is None):
            raise ValueError("name exactly one of campaign_id or v1_lane_key")
        return self

    def target(self) -> dict[str, str]:
        return {"campaign_id": str(self.campaign_id)} if self.campaign_id else {"v1_lane_key": str(self.v1_lane_key)}


class TestSendLimitRefused(CommandRefused):
    """The 429: carries when the next test is allowed, as an ISO-8601 UTC string."""

    __test__ = False  # not a pytest class, despite the name

    def __init__(self, next_allowed_at: str | None) -> None:
        super().__init__(429, "test_send_limit", f"límite de pruebas alcanzado; next_allowed_at={next_allowed_at}")
        self.next_allowed_at = next_allowed_at


class V2CampaignTestSendRepository(CommandTransaction):
    def __init__(self, connect: Any, dsn: str, sender: Any, v1_lane_content_dir: str | None,
                 statement_timeout_ms: int = DEFAULT_COMMAND_TIMEOUT_MS,
                 lock_timeout_ms: int = DEFAULT_COMMAND_LOCK_TIMEOUT_MS) -> None:
        super().__init__(connect, dsn, statement_timeout_ms, lock_timeout_ms)
        self._sender = sender
        self._v1_lane_content_dir = v1_lane_content_dir

    # ------------------------------------------------------------------ content

    def _content(self, cur: Any, body: SendCampaignTestBody) -> tuple[str, str]:
        if body.campaign_id is not None:
            cur.execute("select subject, body_html from outbound.campaign where id = %s", (str(body.campaign_id),))
            row = self._row(cur)
            if row is None:
                raise CommandRefused(404, "campaign_not_found", "la campaña no existe")
            subject, html = row["subject"], row["body_html"]
        else:
            campaign = next((c for c in load_v1_lane_campaigns() if c.key == body.v1_lane_key), None)
            if campaign is None:
                raise CommandRefused(404, "campaign_not_found", "la campaña no existe")
            subject, html = campaign.subject, load_v1_lane_html(campaign.key, self._v1_lane_content_dir)
        if not html:
            raise CommandRefused(422, "campaign_has_no_html", "la campaña no tiene un correo guardado")
        if not subject:
            raise CommandRefused(422, "campaign_has_no_subject", "la campaña no tiene asunto")
        return subject, html

    # ------------------------------------------------------------------ the recipient

    def _refuse_blocked_recipient(self, cur: Any, to: str) -> None:
        address = to.strip().lower()
        cur.execute(
            """
            select exists (
                     select 1 from outbound.contact_control c
                      where c.scope = 'address' and c.value_norm = %s and c.kind = 'block'
                        and c.purpose in ('all', 'marketing'))
                or exists (
                     select 1 from evidence.assertion a
                      where a.kind = 'unsubscribe_request' and a.resolution = 'unresolved'
                        and a.value_norm = %s) as refused
            """,
            (address, address),
        )
        row = self._row(cur)
        if row is None or row["refused"]:
            raise CommandRefused(422, "test_recipient_blocked", "esa dirección pidió la baja o está bloqueada para envíos")

    # ------------------------------------------------------------------ limits

    def _usage(self, cur: Any, exclude_receipt: str | None = None) -> dict[str, Any]:
        cur.execute(
            """
            select count(*) filter (where created_at > now() - interval '1 hour') as hour,
                   count(*) as day,
                   min(created_at) filter (where created_at > now() - interval '1 hour') + interval '1 hour'
                       as hour_frees_at,
                   min(created_at) + interval '1 day' as day_frees_at
              from platform.command_receipt
             where command_name = %s and status in ('in_progress', 'completed')
               and created_at > now() - interval '1 day'
               and (%s::uuid is null or id <> %s::uuid)
            """,
            (SEND_CAMPAIGN_TEST, exclude_receipt, exclude_receipt),
        )
        return self._row(cur) or {"hour": 0, "day": 0, "hour_frees_at": None, "day_frees_at": None}

    # ------------------------------------------------------------------ the command

    def send_test(self, *, operator: OperatorIdentity, body: SendCampaignTestBody,
                  idempotency_key: str, digest: str) -> dict[str, Any]:
        if operator.role != "admin":
            raise CommandRefused(403, "admin_only", "sólo un perfil de administración envía pruebas")
        with self._write(SEND_CAMPAIGN_TEST) as cur:
            receipt_id, replay = self._claim_receipt(cur, operator, idempotency_key, SEND_CAMPAIGN_TEST, digest)
            if replay is not None:
                return replay
            self._refuse_blocked_recipient(cur, body.to)  # rolls the claim back: no receipt
            cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (SEND_CAMPAIGN_TEST,))
            used = self._usage(cur, exclude_receipt=receipt_id)
            blocked = []
            if used["hour"] >= TEST_SENDS_PER_HOUR:
                blocked.append(used["hour_frees_at"])
            if used["day"] >= TEST_SENDS_PER_DAY:
                blocked.append(used["day_frees_at"])
            if blocked:
                latest = max(blocked)
                raise TestSendLimitRefused(latest.astimezone(timezone.utc).isoformat())
            subject, html = self._content(cur, body)
            try:
                raw = build_test_message(to=body.to, subject=subject, html=html)
            except ValueError as exc:  # e.g. a line break in the subject; rolls the claim back
                raise CommandRefused(422, "campaign_subject_invalid", "el asunto de la campaña no es válido") from exc
        record = {"campaign": body.target(), "to": body.to, "subject": TEST_SUBJECT_PREFIX + subject,
                  "content_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest()}
        try:
            message_id = self._sender.send(raw)
        except GmailSendError as exc:
            # `detail` is Google's HTTP status and error code (or the network error's class): never
            # Google's message text, the recipient or a token.
            failed = {**record, "status": "failed", "error": exc.kind}
            if exc.detail:
                failed["error_detail"] = exc.detail
            try:
                self._finish(receipt_id, 502, failed)
            except Exception as record_exc:  # nothing was sent: the send failure is the answer
                # A refusal the database gave is expected; anything else is a bug and keeps its traceback.
                _log.error("test send %s failed and its failure was not recorded (%s, code=%s)",
                           receipt_id, type(record_exc).__name__, getattr(record_exc, "code", None),
                           exc_info=not isinstance(record_exc, DatabaseRefusal))
            raise CommandRefused(502, exc.kind, FAILURE_MESSAGE_ES.get(exc.kind, "No se pudo enviar.")) from exc
        response = {**record, "status": "sent", "gmail_message_id": message_id,
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                    "idempotency_key": idempotency_key, "command_receipt_id": receipt_id, "replayed": False}
        try:
            self._finish(receipt_id, 200, response)
        except Exception as record_exc:
            # The email is already out. Whatever stopped the record (a busy row, a timeout, a lost
            # connection), «nothing was written, retry» would be false and a retry would resend it.
            # A refusal the database gave is expected; anything else is a bug and keeps its traceback.
            _log.error("test send %s delivered but not recorded (%s, code=%s)",
                       receipt_id, type(record_exc).__name__, getattr(record_exc, "code", None),
                       exc_info=not isinstance(record_exc, DatabaseRefusal))
            raise CommandRefused(503, "sent_not_recorded", SENT_NOT_RECORDED_ES) from record_exc
        return response

    def _finish(self, receipt_id: str, status_code: int, response: dict[str, Any]) -> None:
        with self._write(SEND_CAMPAIGN_TEST) as cur:
            cur.execute(
                """
                update platform.command_receipt
                   set status = %s, response_status = %s, response_body = %s::jsonb, completed_at = now()
                 where id = %s and status = 'in_progress'
                """,
                ("completed" if status_code == 200 else "failed", status_code, json_payload(response), receipt_id),
            )

    # ------------------------------------------------------------------ the read

    def history(self, *, campaign_id: str | None, v1_lane_key: str | None) -> dict[str, Any]:
        target = {"campaign_id": str(UUID(campaign_id))} if campaign_id else {"v1_lane_key": v1_lane_key}
        with self._write() as cur:
            cur.execute(
                """
                select r.created_at::text as at, o.display_name as by,
                       r.response_body->>'to' as to, r.response_body->>'status' as status,
                       r.response_body->>'error' as error, r.response_body->>'error_detail' as error_detail
                  from platform.command_receipt r
                  left join platform.operator o on o.id = r.operator_id
                 where r.command_name = %s and r.status in ('completed', 'failed')
                   and r.response_body->'campaign' = %s::jsonb
                 order by r.created_at desc
                 limit %s
                """,
                (SEND_CAMPAIGN_TEST, json_payload(target), HISTORY_LIMIT),
            )
            tests = [dict(zip([d[0] for d in cur.description], row, strict=True)) for row in cur.fetchall()]
            used = self._usage(cur)
        return {"tests": tests,
                "remaining": {"hour": max(0, TEST_SENDS_PER_HOUR - used["hour"]),
                              "day": max(0, TEST_SENDS_PER_DAY - used["day"])}}
