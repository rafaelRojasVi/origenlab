"""Campaign drafts — the content of a campaign that has not been frozen.

Two commands, on the same `CommandTransaction` as every other V2 command (one transaction, one
receipt per `Idempotency-Key`, one `crm.domain_event` per change):

* `create-campaign-draft` — a new `outbound.campaign` row in `draft`.
* `save-campaign-draft` — rewrite a draft's name, content and limits, as a compare-and-set on
  the `version` the operator was shown.

What a draft is **not**: an audience, a send, or an approval. Neither command writes a
recipient, audience criteria or a send flag, and neither can move `status` — the schema keeps
audience criteria frozen-or-absent, and `outbound.campaign_content_draft_only` refuses content
edits once a campaign leaves `draft`, whatever the application does.

**HTML is stored as written, after one refusal.** A draft that contains a script, a frame, a
form, an inline event handler or a `javascript:` URL is refused rather than cleaned: an email
has no use for any of them, and silently altering what an operator wrote would make the saved
draft differ from the one they saw. Previews sanitize separately, in the dashboard.
"""

from __future__ import annotations

import hashlib
import re
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from origenlab_api.v2.command_core import CommandTransaction
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity

CREATE_CAMPAIGN_DRAFT = "create-campaign-draft"
SAVE_CAMPAIGN_DRAFT = "save-campaign-draft"

MAX_HTML_BYTES = 512_000
CONTENT_FIELDS = ("name", "subject", "preheader", "body_html", "max_sends", "recontact_interval_days")

_UNSAFE_HTML = (
    (re.compile(r"<\s*script\b", re.I), "a <script> element"),
    (re.compile(r"<\s*(iframe|frame|frameset|object|embed|applet)\b", re.I), "an embedded frame or object"),
    (re.compile(r"<\s*form\b", re.I), "a <form> element"),
    (re.compile(r"<\s*meta\b[^>]*http-equiv\s*=\s*['\"]?\s*refresh", re.I), "a meta refresh"),
    (re.compile(r"<\s*base\b", re.I), "a <base> element"),
    (re.compile(r"[\s\"'/]on[a-z]+\s*=", re.I), "an inline event handler (on…=)"),
    (re.compile(r"(?:href|src|action|formaction|background)\s*=\s*['\"]?\s*(?:javascript|vbscript|data:text/html)", re.I),
     "a script URL"),
)


def unsafe_html_reason(html: str | None) -> str | None:
    """The first construct an email draft may not contain, or None."""
    if not html:
        return None
    for pattern, label in _UNSAFE_HTML:
        if pattern.search(html):
            return label
    return None


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


class _DraftContent(BaseModel):
    """Name, content and the two limits the schema requires of every campaign row.

    `max_sends` and `recontact_interval_days` have no defaults on purpose: they are sending
    policy, and a draft editor that filled them in would be making that decision silently.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: Annotated[str, Field(min_length=1, max_length=200)]
    subject: Annotated[str | None, Field(max_length=300)] = None
    preheader: Annotated[str | None, Field(max_length=255)] = None
    body_html: str | None = None
    max_sends: Annotated[int, Field(ge=1, le=100_000)]
    recontact_interval_days: Annotated[int, Field(ge=1, le=3650)]

    @field_validator("subject", "preheader", "body_html", mode="before")
    @classmethod
    def _blank_is_absent(cls, value: Any) -> Any:
        return _blank_to_none(value)

    @field_validator("body_html")
    @classmethod
    def _html_fits(cls, value: str | None) -> str | None:
        if value is not None and len(value.encode("utf-8")) > MAX_HTML_BYTES:
            raise ValueError(f"body_html must be at most {MAX_HTML_BYTES} bytes")
        return value


class CreateCampaignDraftBody(_DraftContent):
    #: When the draft was started as a copy, which campaign it copied — recorded in the event only.
    duplicated_from_campaign_id: UUID | None = None


class SaveCampaignDraftBody(_DraftContent):
    campaign_id: UUID
    expected_version: Annotated[int, Field(ge=1)]


def validated_draft(command_name: str, body: _DraftContent) -> dict[str, Any]:
    reason = unsafe_html_reason(body.body_html)
    if reason is not None:
        raise CommandRefused(
            422, "unsafe_html",
            f"the HTML contains {reason}; an email draft may not — remove it and save again",
        )
    fields = body.model_dump(mode="json")
    fields["command"] = command_name
    return fields


def _html_digest(html: str | None) -> str | None:
    return hashlib.sha256(html.encode("utf-8")).hexdigest() if html else None


class V2CampaignDraftRepository(CommandTransaction):
    """The two draft commands. Writes only `outbound.campaign` and the audit stream."""

    def _mailbox(self, cur: Any) -> str:
        """The one mailbox a draft can belong to. The schema requires one; choosing between
        several is a sending decision this editor does not make."""
        cur.execute(
            "select id::text as id, is_production_sender from comms.mailbox order by created_at"
        )
        rows = [dict(zip(("id", "prod"), r, strict=True)) for r in cur.fetchall()]
        production = [r for r in rows if r["prod"]]
        if len(production) == 1:
            return production[0]["id"]
        if len(rows) == 1:
            return rows[0]["id"]
        if not rows:
            raise CommandRefused(409, "no_mailbox", "no mailbox is recorded; a campaign needs one")
        raise CommandRefused(
            409, "mailbox_ambiguous",
            "several mailboxes are recorded and none is the single production sender",
        )

    def _storage(self, cur: Any) -> dict[str, str]:
        cur.execute("select current_database()")
        return {"table": "outbound.campaign", "database": cur.fetchone()[0]}

    def _create(self, cur: Any, operator: OperatorIdentity, f: dict[str, Any], receipt_id: str) -> dict[str, Any]:
        mailbox_id = self._mailbox(cur)
        cur.execute(
            """
            insert into outbound.campaign
                (name, status, mailbox_id, max_sends, recontact_interval_days,
                 subject, preheader, body_html, created_by_operator_id)
            values (%s, 'draft', %s, %s, %s, %s, %s, %s, %s)
            returning id::text as id, version, updated_at::text as saved_at
            """,
            (f["name"], mailbox_id, f["max_sends"], f["recontact_interval_days"],
             f["subject"], f["preheader"], f["body_html"], operator.operator_id),
        )
        row = self._row(cur)
        assert row is not None  # noqa: S101
        self._append_event(
            cur,
            aggregate_kind="campaign",
            aggregate_id=row["id"],
            event_type="campaign.draft_created",
            payload={
                "name": f["name"],
                "fields_present": [k for k in CONTENT_FIELDS if f.get(k) is not None],
                "body_html_sha256": _html_digest(f["body_html"]),
                "duplicated_from_campaign_id": f.get("duplicated_from_campaign_id"),
            },
            operator=operator,
            receipt_id=receipt_id,
        )
        return {
            "campaign_id": row["id"], "status": "draft", "version": row["version"],
            "saved_at": row["saved_at"], "changed_fields": [k for k in CONTENT_FIELDS if f.get(k) is not None],
            "storage": self._storage(cur),
        }

    def _save(self, cur: Any, operator: OperatorIdentity, f: dict[str, Any], receipt_id: str) -> dict[str, Any]:
        cur.execute(
            """
            select id::text as id, status, version, name, subject, preheader, body_html,
                   max_sends, recontact_interval_days, updated_at::text as saved_at
              from outbound.campaign where id = %s for update
            """,
            (f["campaign_id"],),
        )
        current = self._row(cur)
        if current is None:
            raise CommandRefused(404, "campaign_not_found", "no such campaign")
        if current["status"] != "draft":
            raise CommandRefused(
                409, "campaign_not_draft",
                f"the campaign is '{current['status']}'; only a draft's content can change",
            )
        if current["version"] != f["expected_version"]:
            raise CommandRefused(
                409, "stale_version",
                f"the draft is at version {current['version']}, not {f['expected_version']}; "
                "someone saved it after you opened it — reload before saving",
            )
        changed = [k for k in CONTENT_FIELDS if f[k] != current[k]]
        if not changed:
            return {
                "campaign_id": current["id"], "status": "draft", "version": current["version"],
                "saved_at": current["saved_at"], "changed_fields": [], "storage": self._storage(cur),
            }
        cur.execute(
            """
            update outbound.campaign
               set name = %s, subject = %s, preheader = %s, body_html = %s,
                   max_sends = %s, recontact_interval_days = %s,
                   version = version + 1, updated_at = now()
             where id = %s and version = %s
            returning version, updated_at::text as saved_at
            """,
            (f["name"], f["subject"], f["preheader"], f["body_html"], f["max_sends"],
             f["recontact_interval_days"], current["id"], current["version"]),
        )
        row = self._row(cur)
        assert row is not None  # noqa: S101 - the row is locked and its version checked
        self._append_event(
            cur,
            aggregate_kind="campaign",
            aggregate_id=current["id"],
            event_type="campaign.draft_content_saved",
            payload={
                "changed_fields": changed,
                "from_version": current["version"],
                "to_version": row["version"],
                "body_html_sha256": _html_digest(f["body_html"]),
            },
            operator=operator,
            receipt_id=receipt_id,
        )
        return {
            "campaign_id": current["id"], "status": "draft", "version": row["version"],
            "saved_at": row["saved_at"], "changed_fields": changed, "storage": self._storage(cur),
        }

    _HANDLERS = {CREATE_CAMPAIGN_DRAFT: _create, SAVE_CAMPAIGN_DRAFT: _save}
