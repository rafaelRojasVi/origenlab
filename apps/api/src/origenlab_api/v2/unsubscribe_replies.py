"""«BAJA» replies — the conservative grammar and the staging boundary for already-fetched replies.

W10 (docs/WORKFLOWS.md §W10 step 3): a marketing email says «responda BAJA»; a recipient who
does is recorded as a permanent marketing suppression. This module is the pure half — no
database, no network, no clock except the one it is handed:

* :func:`classify_reply` decides whether one reply body is a **clear, standalone BAJA
  instruction** (or its legacy V1 form, REMOVER). Nothing else is inferred. Prose that merely
  contains the word («dar de baja», «la baja del equipo», «BAJA por favor», «remover mi correo»)
  is *not* an instruction here; it is left for a human.
* :class:`GmailReplyRecord` is the shape of one reply as the mail pipeline already fetched it.
  This module never fetches, labels, moves or answers a message: it has no mail client, and a
  test refuses any network or Google import in it.
* :func:`plan_batch` turns a batch into one outcome per record against the facts the database
  holds, and :func:`input_sha256` fingerprints exactly what was planned. The preview answers
  with that fingerprint; the apply command refuses a batch whose fingerprint differs.

**The sender policy** (``UNSUBSCRIBE_POLICY_VERSION``) — who a clear instruction suppresses:

* OrigenLab's own mailboxes: never (``own_mailbox``).
* An address already known here (a contact control, a campaign recipient or a CRM email):
  suppressed (basis ``known_address``).
* Otherwise, when the reply's ``In-Reply-To`` names a recorded outbound message and the sender is
  exactly one of that message's recipients: suppressed (basis ``outbound_lineage``), without a
  person, contact point or organization being created.
* Otherwise the reply is recorded as a **pending review** (``lineage_missing`` or
  ``recipient_mismatch``): durable evidence and an unresolved request that holds the exact
  address out of every audience preview, freeze and send until an operator confirms it.

**The grammar** (``BAJA_GRAMMAR_VERSION``), applied to ``body_text`` only — never the subject:

1. No plain-text body → not an instruction (``no_plain_text``).
2. Unicode NFKC, zero-width characters removed, line endings unified.
3. The reply's own text ends at the first line that starts quoted history or a signature:
   a line beginning with ``>``, an attribution line («El … escribió:», «On … wrote:»), an
   «-----Mensaje original-----» / «-----Original Message-----» separator, an Outlook ``____``
   rule or ``De:`` / ``From:`` header, or the RFC 3676 signature delimiter ``--``. An
   attribution this grammar does not recognise is *not* cut: the quoted text then stays in the
   reply and the reply is refused — the safe direction.
4. What remains, trimmed and case-folded, must be exactly ``baja``, ``baja.``, ``remover`` or
   ``remover.`` — one optional final full stop and nothing else. ``REMOVER`` is the word the V1
   templates asked for, accepted so those replies are honoured. ``BAJA!``, ``¡BAJA!``,
   ``BAJA?``, ``"BAJA"``, ``*BAJA*``, ``BAJA BAJA``, ``REMOVER!``, ``REMOVER BAJA``, either word
   followed by a name or «Enviado desde mi iPhone» are all refused.

A refused reply is not an error and is not lost: it is listed for an operator, and it writes
nothing.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import getaddresses
from typing import Annotated, Any, Iterable, Mapping

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

#: v2 added the legacy standalone REMOVER / REMOVER. to v1's BAJA / BAJA.
BAJA_GRAMMAR_VERSION = "baja-reply/2026-09-27.v2"
#: v2: an unknown sender is suppressed only with proven outbound lineage, otherwise held for review.
UNSUBSCRIBE_POLICY_VERSION = "unsubscribe-sender/2026-09-27.v2"
RECORD_SCHEMA_VERSION = "gmail-reply-staging/2026-09-27.v1"
APPLY_UNSUBSCRIBE_REPLIES = "apply-unsubscribe-replies"
RESOLVE_UNSUBSCRIBE_REVIEW = "resolve-unsubscribe-review"
DISMISS_UNSUBSCRIBE_REVIEW = "dismiss-unsubscribe-review"
#: An operator confirms what the mail triage read as an unsubscribe (STATUS.md §2.7.86).
CONFIRM_TRIAGE_UNSUBSCRIBE = "confirm-triage-unsubscribe"

#: The version of one «BAJA» held for review, as ``outbound.add_contact_control`` recomputes it
#: before a dismissal: over the request (alias ``a``) and its reply evidence (alias ``s``). A
#: dismissal must quote it, so a screen read before the request changed is refused, not applied.
REVIEW_SHA256_SQL = (
    "encode(sha256(convert_to(concat_ws('|', 'unsubscribe-review/v1', a.id::text, a.value_norm, a.resolution, "
    "a.source_record_id::text, coalesce(s.payload_sha256, ''), a.value::text), 'UTF8')), 'hex')"
)

MAX_RECORDS = 500
MAX_BODY_CHARS = 20_000
#: A reply dated this far after "now" is a clock or parsing error, not a reply.
MAX_FUTURE_SKEW = timedelta(minutes=5)

#: Same shape as the contact_control address CHECK and the pipeline's ADDRESS_SHAPE_PATTERN.
ADDRESS_SHAPE = re.compile(r'^[^@\s<>,;"]+@[^@\s<>,;"]+\.[^@\s<>,;"]+$')
_MESSAGE_ID = re.compile(r"^[^\s<>@]+@[^\s<>@]+$")
_SOURCE = re.compile(r"^gmail:\S{1,190}$")
#: The domain every OrigenLab mailbox lives on; a «BAJA» from it is our own mail, never a request.
OWN_DOMAINS: frozenset[str] = frozenset({"origenlab.cl"})

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)
_HISTORY_STARTS = (
    re.compile(r"^>"),
    re.compile(r"^(el|on)\s.+\s(escribió|escribio|wrote)\s*:$", re.IGNORECASE),
    re.compile(r"^-{2,}\s*(mensaje original|original message)\s*-{2,}$", re.IGNORECASE),
    re.compile(r"^_{5,}$"),
    re.compile(r"^(de|from)\s*:\s", re.IGNORECASE),
    re.compile(r"^--$"),
)
#: The whole accepted language, after normalization and case folding.
_ACCEPTED = {"baja": "standalone_baja", "baja.": "standalone_baja",
             "remover": "standalone_remover", "remover.": "standalone_remover"}
ACCEPTED_FORMS = ("BAJA", "BAJA.", "REMOVER", "REMOVER.")

VERDICT_LABEL = {
    "standalone_baja": "Instrucción BAJA clara",
    "standalone_remover": "Instrucción REMOVER clara (plantillas V1)",
    "no_plain_text": "Sin cuerpo de texto",
    "empty_reply": "Respuesta vacía",
    "baja_not_standalone": "Menciona «baja» pero no es una instrucción aislada",
    "remover_not_standalone": "Menciona «remover» pero no es una instrucción aislada",
    "no_baja": "No es una solicitud de BAJA",
}

OUTCOME_LABEL = {
    "suppress": "Se registrará la BAJA (supresión de marketing permanente)",
    "evidence_only": "Ya suprimida: se vinculará la nueva evidencia",
    "already_recorded": "Este mensaje ya está registrado",
    "not_baja": "No se aplica: no es una instrucción BAJA clara",
    "already_pending": "Este mensaje ya está registrado y espera revisión",
    "pending_review": "Remitente no comprobado: queda en revisión y bloqueado para marketing",
    "own_mailbox": "No se aplica: el remitente es un buzón propio",
    "malformed": "Registro mal formado",
}
#: Outcomes the apply command acts on; every other outcome writes nothing.
APPLIED_OUTCOMES = frozenset({"suppress", "evidence_only", "pending_review"})

REVIEW_REASON_LABEL = {
    "lineage_missing": "La respuesta no remite a ningún correo enviado registrado",
    "recipient_mismatch": "El remitente no es el destinatario del correo enviado al que responde",
}


# --------------------------------------------------------------------------- the grammar


@dataclass(frozen=True)
class ReplyVerdict:
    accepted: bool
    code: str

    @property
    def label(self) -> str:
        return VERDICT_LABEL[self.code]


def reply_text(body_text: str) -> str:
    """The reply's own text: normalized, cut before quoted history or a signature, trimmed."""
    text = unicodedata.normalize("NFKC", body_text).translate(_ZERO_WIDTH)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    kept: list[str] = []
    for line in text.split("\n"):
        if any(p.match(line.strip()) for p in _HISTORY_STARTS):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def classify_reply(body_text: str | None) -> ReplyVerdict:
    """Whether a reply body is a clear standalone BAJA instruction (see the module docstring)."""
    if body_text is None:
        return ReplyVerdict(False, "no_plain_text")
    own = reply_text(body_text)
    if not own:
        return ReplyVerdict(False, "empty_reply")
    folded = own.casefold()
    if folded in _ACCEPTED:
        return ReplyVerdict(True, _ACCEPTED[folded])
    if "baja" in folded:
        return ReplyVerdict(False, "baja_not_standalone")
    if "remover" in folded:
        return ReplyVerdict(False, "remover_not_standalone")
    return ReplyVerdict(False, "no_baja")


# --------------------------------------------------------------------------- the record


class GmailReplyRecord(BaseModel):
    """One reply as the mail pipeline already fetched it. Nothing here reaches a mailbox.

    Field names follow the pipeline's ``emails`` row: ``message_id`` is the RFC 822
    ``Message-ID``, ``source`` is ``gmail:<mailbox>/<folder>``, ``from_header`` the raw ``From``
    header and ``body_text`` the plain-text body as received (quoted history included).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    message_id: Annotated[str, Field(min_length=3, max_length=998)]
    source: Annotated[str, Field(min_length=7, max_length=200)]
    from_header: Annotated[str, Field(min_length=3, max_length=998)]
    received_at: AwareDatetime
    subject: Annotated[str | None, Field(max_length=998)] = None
    in_reply_to: Annotated[str | None, Field(max_length=998)] = None
    body_text: Annotated[str | None, Field(max_length=MAX_BODY_CHARS)] = None


def normalize_message_id(raw: str) -> str | None:
    value = raw.strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1].strip()
    value = value.lower()
    return value if _MESSAGE_ID.match(value) else None


def sender_address(from_header: str) -> str | None:
    """The one address in a From header, normalized; None unless there is exactly one."""
    if "\n" in from_header or "\r" in from_header:
        return None
    pairs = [(name, addr) for name, addr in getaddresses([from_header]) if addr]
    if len(pairs) != 1:
        return None
    address = pairs[0][1].strip().lower()
    return address if ADDRESS_SHAPE.match(address) else None


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def input_sha256(records: list[Any]) -> str:
    """The batch fingerprint: the records exactly as sent, under the grammar that judged them.

    A different record, a different order, a different grammar or sender-policy version is a
    different fingerprint, so an apply can never act on a batch the operator did not preview.
    """
    return sha256_hex(_canonical({"grammar_version": BAJA_GRAMMAR_VERSION,
                                  "policy_version": UNSUBSCRIBE_POLICY_VERSION, "records": records}))


def plan_sha256(batch_sha256: str, rows: list[dict[str, Any]]) -> str:
    """The fingerprint of what the operator was shown: each record's outcome and address.

    Apply recomputes the plan against the facts of its own transaction and refuses when this
    differs, so a sender that became known, or an address suppressed meanwhile, is never acted on
    under a consent given for a different plan.
    """
    shown = [[r["index"], r["outcome"], r.get("address"), r.get("basis"), r.get("review_reason")] for r in rows]
    return sha256_hex(_canonical({"input_sha256": batch_sha256, "rows": shown}))


@dataclass(frozen=True)
class ParsedReply:
    record: GmailReplyRecord
    message_id_norm: str
    message_id_sha256: str
    address: str
    verdict: ReplyVerdict
    #: The normalized In-Reply-To, when there is one of a valid shape.
    in_reply_to_norm: str | None = None

    @property
    def dedupe_key(self) -> str:
        return f"gmail_unsubscribe:{self.message_id_sha256}"

    def evidence(self, batch_sha256: str, basis: str = "known_address",
                 review_reason: str | None = None) -> dict[str, Any]:
        """What `outbound.add_contact_control` stores: the reply as received, and its hashes.

        ``basis`` is the policy's claim (``known_address``, ``outbound_lineage`` or
        ``pending_review``); the database proves the first two itself and refuses a false one.
        """
        r = self.record
        body = r.body_text or ""
        out = {
            "basis": basis,
            "message_id_sha256": self.message_id_sha256,
            "observed_at": r.received_at.isoformat(),
            "grammar_version": BAJA_GRAMMAR_VERSION,
            "policy_version": UNSUBSCRIBE_POLICY_VERSION,
            "input_sha256": batch_sha256,
            "payload": {
                "schema": RECORD_SCHEMA_VERSION,
                "message_id": r.message_id,
                "message_id_norm": self.message_id_norm,
                "message_id_sha256": self.message_id_sha256,
                "source": r.source,
                "from_header": r.from_header,
                "from_address": self.address,
                "received_at": r.received_at.isoformat(),
                "subject": r.subject,
                "in_reply_to": r.in_reply_to,
                "body_text": body,
                "body_sha256": sha256_hex(body),
                "grammar_version": BAJA_GRAMMAR_VERSION,
                "policy_version": UNSUBSCRIBE_POLICY_VERSION,
                "verdict": self.verdict.code,
                "input_sha256": batch_sha256,
            },
        }
        if review_reason is not None:
            out["review_reason"] = review_reason
        return out


def parse_record(raw: Any, now: datetime) -> tuple[ParsedReply | None, list[str]]:
    """One staged record, or the reasons it is malformed. Values are never echoed back."""
    if not isinstance(raw, Mapping):
        return None, ["not_an_object"]
    try:
        record = GmailReplyRecord.model_validate(raw)
    except ValidationError as exc:
        return None, sorted({f"{'.'.join(map(str, e['loc'])) or 'record'}:{e['type']}" for e in exc.errors()})
    problems: list[str] = []
    message_id = normalize_message_id(record.message_id)
    if message_id is None:
        problems.append("message_id:shape")
    if not _SOURCE.match(record.source):
        problems.append("source:shape")
    address = sender_address(record.from_header)
    if address is None:
        problems.append("from_header:not_one_address")
    if record.received_at > now + MAX_FUTURE_SKEW:
        problems.append("received_at:future")
    if problems:
        return None, problems
    assert message_id is not None and address is not None  # noqa: S101 - checked above
    in_reply_to = normalize_message_id(record.in_reply_to) if record.in_reply_to else None
    return ParsedReply(record, message_id, sha256_hex(message_id), address, classify_reply(record.body_text),
                       in_reply_to), []


# --------------------------------------------------------------------------- the plan


@dataclass
class ReplyFacts:
    """What the database knows about the batch's senders and messages, read in one transaction."""

    now: datetime
    #: Addresses OrigenLab knows: a contact control, a campaign recipient or a CRM email.
    known_addresses: set[str] = field(default_factory=set)
    #: Normalized RFC 822 id of a recorded outbound message → that message's to/cc/bcc recipients
    #: (the union when one id was recorded more than once). Only ids the batch replies to.
    outbound_recipients: dict[str, set[str]] = field(default_factory=dict)
    #: Addresses already under a marketing block → that block's reason.
    marketing_blocked: dict[str, str] = field(default_factory=dict)
    #: `gmail_unsubscribe:` dedupe keys already recorded.
    recorded_keys: set[str] = field(default_factory=set)
    #: … of which the request is still held for review (unresolved).
    pending_keys: set[str] = field(default_factory=set)
    #: OrigenLab's own mailboxes, compared as whole addresses: a mailbox on a shared provider
    #: must never turn every sender on that provider into "our own mail".
    own_addresses: set[str] = field(default_factory=set)


def batch_addresses_and_keys(records: Iterable[Any], now: datetime) -> tuple[list[str], list[str], list[str]]:
    """The addresses, dedupe keys and In-Reply-To ids a batch names — what the facts are read for."""
    addresses: set[str] = set()
    keys: set[str] = set()
    parents: set[str] = set()
    for raw in records:
        parsed, _ = parse_record(raw, now)
        if parsed is not None:
            addresses.add(parsed.address)
            keys.add(parsed.dedupe_key)
            if parsed.in_reply_to_norm:
                parents.add(parsed.in_reply_to_norm)
    return sorted(addresses), sorted(keys), sorted(parents)


def sender_basis(parsed: ParsedReply, facts: ReplyFacts) -> tuple[str, str | None]:
    """Why this sender may be suppressed (the basis), or why it is held for review."""
    if parsed.address in facts.known_addresses:
        return "known_address", None
    recipients = facts.outbound_recipients.get(parsed.in_reply_to_norm or "")
    if recipients is None:
        return "pending_review", "lineage_missing"
    if parsed.address in recipients:
        return "outbound_lineage", None
    return "pending_review", "recipient_mismatch"


def plan_batch(records: list[Any], facts: ReplyFacts) -> dict[str, Any]:
    """One outcome per record, in order, and whether the batch may be applied at all.

    Any malformed record blocks the whole batch: the operator fixes the export, never the
    command. Records that are not a clear instruction or come from our own mailbox are listed
    and never applied; a clear instruction from a sender that cannot be proven is held for
    review (see the sender policy in the module docstring).
    """
    batch = input_sha256(records)
    rows: list[dict[str, Any]] = []
    parsed_rows: list[ParsedReply | None] = []
    seen_messages: set[str] = set()
    suppressing: set[str] = set()
    for index, raw in enumerate(records):
        parsed, problems = parse_record(raw, facts.now)
        if parsed is not None and parsed.message_id_sha256 in seen_messages:
            parsed, problems = None, ["message_id:duplicate_in_batch"]
        if parsed is None:
            rows.append({"index": index, "outcome": "malformed", "problems": problems})
            parsed_rows.append(None)
            continue
        seen_messages.add(parsed.message_id_sha256)
        domain = parsed.address.split("@", 1)[1]
        basis: str | None = None
        review_reason: str | None = None
        if not parsed.verdict.accepted:
            outcome = "not_baja"
        elif domain in OWN_DOMAINS or parsed.address in facts.own_addresses:
            outcome = "own_mailbox"
        elif parsed.dedupe_key in facts.pending_keys:
            outcome = "already_pending"
        elif parsed.dedupe_key in facts.recorded_keys:
            outcome = "already_recorded"
        else:
            basis, review_reason = sender_basis(parsed, facts)
            if basis == "pending_review":
                outcome = "pending_review"
            elif parsed.address in facts.marketing_blocked or parsed.address in suppressing:
                outcome = "evidence_only"
            else:
                outcome = "suppress"
                suppressing.add(parsed.address)
        rows.append({
            "index": index,
            "outcome": outcome,
            "address": parsed.address,
            "message_id_sha256": parsed.message_id_sha256,
            "received_at": parsed.record.received_at.isoformat(),
            "verdict": {"code": parsed.verdict.code, "label": parsed.verdict.label},
            "basis": basis,
            "review_reason": review_reason,
            "review_reason_label": REVIEW_REASON_LABEL.get(review_reason or ""),
            "existing_block_reason": facts.marketing_blocked.get(parsed.address),
            "body_chars": len(parsed.record.body_text or ""),
        })
        parsed_rows.append(parsed)
    for row in rows:
        row["label"] = OUTCOME_LABEL[row["outcome"]]
    counts: dict[str, int] = {k: 0 for k in OUTCOME_LABEL}
    for row in rows:
        counts[row["outcome"]] += 1
    malformed = counts["malformed"]
    return {
        "input_sha256": batch,
        "plan_sha256": plan_sha256(batch, rows),
        "grammar_version": BAJA_GRAMMAR_VERSION,
        "policy_version": UNSUBSCRIBE_POLICY_VERSION,
        "record_schema": RECORD_SCHEMA_VERSION,
        "records": len(records),
        "counts": counts,
        "applicable": sum(counts[o] for o in APPLIED_OUTCOMES),
        "blocked": malformed > 0,
        "blocked_reason": (f"{malformed} registro(s) mal formado(s): corrija la exportación" if malformed else None),
        "rows": rows,
        "_parsed": parsed_rows,
        "sends_email": False,
        "reads_mailbox": False,
    }
