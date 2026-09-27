"""Phase 3: what the owner's worksheet answers (W001–W117) would decide — computed, never applied.

The human-review worksheet (`operator_questions.csv`) holds one owner answer per question about
the 125 quotation cases the review policy (P01–P13) could not settle. The answers are free text in
a CSV column; nothing reads them. This module turns them into document decisions a later, separate
apply step could append to `document_decisions.jsonl`, and says for every case why it can or cannot.

It is pure: it opens no file, database or API. The caller hands it the worksheet rows, the
second-pass cases, the known document hashes and the ledger's present state; it returns a
resolution. Nothing here writes a ledger, the CRM or anything else.

**Three layers, each versioned.**

1. *Normalisation.* Every recorded answer is mapped through `ANSWER_TABLE` to one enum value per
   decision type. A value outside its row's `answer_options` is accepted only when it is a listed,
   owner-approved extension (W103 `keep_both_same_quotation_versions`, W116
   `not_applicable:labdelivery_quotation`, the `one_opportunity (canonical:…)` variant). Any other
   string is refused, never guessed.
2. *Per-document outcome.* The normalised answers touching a document decide whether it is a sent
   OrigenLab quotation (canonical or earlier version), not OrigenLab's, supplier correspondence, or
   still pending. Contradictory answers make the case blocked, not resolved by precedence.
3. *Opportunities.* `one_opportunity`, `same_opportunity_as`, version families and W103 join
   documents (union-find); `new_opportunity` forbids joining the named context documents. A group
   that reaches an already-recorded opportunity joins it; otherwise its planned id is a UUIDv5 of
   its member hashes, so the same answers always plan the same id.

Every identifier a later apply would write (decision id, planned opportunity id) is derived from
stable inputs, so a rerun after an apply finds each intent already recorded and proposes nothing.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

POLICY_VERSION = "W001-W117/2026-09-25.1"

# UUIDv5 namespace for every id this module plans. Changing it changes every planned id.
NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "origenlab:quote-worksheet-resolution")

# ─── normalised values ─────────────────────────────────────────────────────────────────────────

V_VERSION_OF = "version_of_same_quotation"
V_EXACT_RESEND = "exact_resend"
V_SEPARATE = "separate_quotations"
V_NUMBER_PRINTED = "printed_number_is_the_number"
V_NUMBER_NOT = "not_the_number"
V_SUPPLIER = "supplier_correspondence"
V_CUSTOMER_QUOTATION = "customer_quotation"
V_SENT = "yes_sent_to_client"
V_NOT_CUSTOMER = "no_not_a_customer_quotation"
V_UNSURE = "unsure"
V_ONE_OPP = "one_opportunity"
V_ONE_PER_DOC = "one_per_document"
V_SPLIT = "split"
V_CLIENT = "client_identified"
V_LEAVE_PENDING = "leave_pending"
V_ORIGENLAB = "origenlab_quotation"
V_NOT_ORIGENLAB = "labdelivery_quotation_not_origenlab"
V_MIRROR_REJECT = "mirror_reject"
V_LEAVE_UNMIRRORED = "leave_unmirrored"
V_REAL_SENT = "real_sent_quotations"
V_NOT_SENT = "not_sent"
V_SAME_OPP_AS = "same_opportunity_as"
V_NEW_OPP = "new_opportunity"
V_KEEP_BOTH = "keep_both_as_separate_quotations"
V_THIS_NOT_SENT = "this_one_not_sent"
V_REISSUE = "one_keeps_number_other_reissued"
V_SAME_QUOTATION_VERSIONS = "same_quotation_versions"

# Values the table accepts but to which Phase 3 gives no automatic meaning yet: a case carrying
# one is blocked for a human instead of being applied under a guessed reading.
NO_PHASE3_SEMANTICS = frozenset({V_EXACT_RESEND, V_SEPARATE, V_SPLIT, V_REISSUE, V_NUMBER_NOT,
                                 V_CUSTOMER_QUOTATION, V_CLIENT, V_LEAVE_UNMIRRORED, V_ORIGENLAB})


@dataclass(frozen=True)
class AnswerRule:
    """One recorded answer form. `param` rules match `prefix` + a value (a document or a name)."""

    decision_type: str
    raw: str
    value: str
    param: bool = False
    # Empty when the form is one of the row's own answer_options; else the owner decision that
    # allowed it outside them.
    extension: str = ""

    def matches(self, answer: str) -> str | None:
        """The parameter ('' when none) when `answer` is this form, else None. A parenthesised
        form (`… (canonical:`) must close with ')'; a colon form (`client:`) takes the rest."""
        if not self.param:
            return "" if answer == self.raw else None
        if not answer.startswith(self.raw):
            return None
        inner = answer[len(self.raw):]
        if "(" in self.raw:
            if not inner.endswith(")"):
                return None
            inner = inner[:-1]
        return inner.strip() or None


EXT_W103 = "W103: owner approved 2026-09-25 16:28Z (not a collision; versions of one quotation)"
EXT_W116 = "W116: owner approved 2026-09-25 17:38Z (Labdelivery quotation only forwarded by OrigenLab)"
EXT_GROUP_CANONICAL = "grouping answered with a canonical hint (owner, W001–W101 batch)"

ANSWER_TABLE: tuple[AnswerRule, ...] = (
    AnswerRule("canonical_version", "version_of_same_quotation (canonical:", V_VERSION_OF, param=True),
    AnswerRule("canonical_version", "exact_resend", V_EXACT_RESEND),
    AnswerRule("canonical_version", "separate_quotations", V_SEPARATE),
    AnswerRule("confirm_printed_number", "printed_number_is_the_number", V_NUMBER_PRINTED),
    AnswerRule("confirm_printed_number", "not_the_number", V_NUMBER_NOT),
    AnswerRule("confirm_supplier_correspondence", "supplier_correspondence", V_SUPPLIER),
    AnswerRule("confirm_supplier_correspondence", "customer_quotation", V_CUSTOMER_QUOTATION),
    AnswerRule("direction", "yes_sent_to_client", V_SENT),
    AnswerRule("direction", "no_not_a_customer_quotation", V_NOT_CUSTOMER),
    AnswerRule("direction", "unsure", V_UNSURE),
    AnswerRule("grouping", "one_opportunity", V_ONE_OPP),
    AnswerRule("grouping", "one_opportunity (canonical:", V_ONE_OPP, param=True, extension=EXT_GROUP_CANONICAL),
    AnswerRule("grouping", "one_per_document", V_ONE_PER_DOC),
    AnswerRule("grouping", "split", V_SPLIT),
    AnswerRule("identify_generic_client", "client:", V_CLIENT, param=True),
    AnswerRule("identify_generic_client", "leave_pending", V_LEAVE_PENDING),
    AnswerRule("labdelivery_issuer", "origenlab_quotation", V_ORIGENLAB),
    AnswerRule("labdelivery_issuer", "labdelivery_quotation_not_origenlab", V_NOT_ORIGENLAB),
    AnswerRule("labdelivery_issuer", "leave_pending", V_LEAVE_PENDING),
    AnswerRule("mirror_email_decision", "mirror_reject", V_MIRROR_REJECT),
    AnswerRule("mirror_email_decision", "leave_unmirrored", V_LEAVE_UNMIRRORED),
    AnswerRule("read_new_template", "real_sent_quotations", V_REAL_SENT),
    AnswerRule("read_new_template", "not_sent", V_NOT_SENT),
    AnswerRule("same_client_other_thread", "same_opportunity_as:", V_SAME_OPP_AS, param=True),
    AnswerRule("same_client_other_thread", "new_opportunity", V_NEW_OPP),
    AnswerRule("same_client_other_thread", "not_applicable:labdelivery_quotation", V_NOT_ORIGENLAB,
               extension=EXT_W116),
    AnswerRule("serial_collision", "keep_both_as_separate_quotations", V_KEEP_BOTH),
    AnswerRule("serial_collision", "yes_separate_real_quotation", V_KEEP_BOTH),
    AnswerRule("serial_collision", "A_both_keep_01242-26", V_KEEP_BOTH),
    AnswerRule("serial_collision", "this_one_not_sent", V_THIS_NOT_SENT),
    AnswerRule("serial_collision", "no_not_sent_as_quotation", V_THIS_NOT_SENT),
    AnswerRule("serial_collision", "B_one_keeps_01242-26_other_reissued", V_REISSUE),
    AnswerRule("serial_collision", "leave_pending", V_LEAVE_PENDING),
    AnswerRule("serial_collision", "C_leave_both_pending", V_LEAVE_PENDING),
    AnswerRule("serial_collision", "keep_both_same_quotation_versions", V_SAME_QUOTATION_VERSIONS,
               extension=EXT_W103),
)

# ─── outcomes and blockers ─────────────────────────────────────────────────────────────────────

O_CONFIRM = "confirm_quotation"
O_CONFIRM_PRIOR = "confirm_prior_version"
O_REJECT_NOT_ORIGENLAB = "reject_not_origenlab_quotation"
O_REJECT_SUPPLIER = "reject_supplier_correspondence"
O_REJECT_NOT_QUOTATION = "reject_not_customer_quotation"
O_EMAIL_REJECT_SUPPLIER = "email_reject_supplier_correspondence"
O_PENDING = "pending"
O_CONTRADICTION = "contradiction"

CONFIRM_OUTCOMES = (O_CONFIRM, O_CONFIRM_PRIOR)
REJECT_OUTCOMES = (O_REJECT_NOT_ORIGENLAB, O_REJECT_SUPPLIER, O_REJECT_NOT_QUOTATION)

B_UNANSWERED = "decision_type_without_answer"
B_PENDING = "owner_left_pending"
B_CLIENT_PENDING = "client_identity_pending"
B_GROUP_PENDING = "opportunity_member_pending"
B_CONTRADICTION = "contradictory_answers"
B_NO_SEMANTICS = "value_without_phase3_semantics"
B_NO_NUMBER = "no_printed_quote_number"
B_NUMBER_OTHER_OPP = "quote_number_on_other_opportunity"
B_NEW_OPP_JOINED = "new_opportunity_joined_to_context_document"
B_SEVERAL_RECORDED_OPPS = "group_spans_several_recorded_opportunities"
B_CANONICAL_CONFLICT = "several_canonical_versions"
B_RECORDED_DIFFERS = "ledger_already_records_other_decision"
B_EMAIL_TARGET_NO_DOCUMENT = "email_case_without_document"

BLOCKER_ES = {
    B_UNANSWERED: "la pregunta necesaria no tiene respuesta en la hoja",
    B_PENDING: "el owner lo dejó pendiente",
    B_CLIENT_PENDING: "cliente sin identificar (sólo dirección genérica)",
    B_GROUP_PENDING: "otro documento de la misma oportunidad está pendiente",
    B_CONTRADICTION: "respuestas contradictorias",
    B_NO_SEMANTICS: "valor sin semántica automática en la Fase 3",
    B_NO_NUMBER: "sin número impreso utilizable",
    B_NUMBER_OTHER_OPP: "el mismo número está en otra oportunidad (el ledger y crm.quote lo rechazan)",
    B_NEW_OPP_JOINED: "new_opportunity, pero otra respuesta lo une a un documento de contexto",
    B_SEVERAL_RECORDED_OPPS: "el grupo alcanza varias oportunidades ya registradas",
    B_CANONICAL_CONFLICT: "más de una versión canónica en la misma familia",
    B_RECORDED_DIFFERS: "el ledger ya registra otra decisión para el documento",
    B_EMAIL_TARGET_NO_DOCUMENT: "caso de correo sin documento de cotización asociado",
}


class UnknownAnswer(ValueError):
    """A recorded answer that no row of ANSWER_TABLE describes. Never guessed."""


class PolicyChanged(RuntimeError):
    pass


# ─── the policy fingerprint ────────────────────────────────────────────────────────────────────

OUTCOME_RULES: tuple[tuple[str, str], ...] = (
    ("R1", "any leave_pending/unsure on a document → pending; a generic-client leave_pending also "
           "holds every other document of its opportunity"),
    ("R2", "not-OrigenLab (labdelivery_quotation_not_origenlab, not_applicable:labdelivery_quotation) → "
           "reject_non_quotation; yes_sent_to_client does not contradict it (delivery, not issuer); "
           "a keep/version/printed-number answer on the same document does → contradiction"),
    ("R3", "supplier_correspondence → reject_non_quotation (document) or email-ledger reject (email case)"),
    ("R4", "mirror_reject → reject_non_quotation mirroring the email decision"),
    ("R5", "version_of_same_quotation(canonical:X) → X is canonical, the family's other documents are "
           "earlier versions; all are confirmed on one opportunity; two canonicals → contradiction"),
    ("R6", "keep_both_same_quotation_versions (W103) joins the documents as one quotation; the "
           "canonical comes from the canonical_version answer"),
    ("R7", "one_opportunity / same_opportunity_as / version families join opportunities; "
           "new_opportunity forbids joining the named context documents"),
    ("R8", "a group reaching exactly one recorded opportunity joins it; several → blocked; none → "
           "planned id uuid5(member hashes)"),
    ("R9", "the quote number is the printed number; printed_number_is_the_number admits the widened "
           "print (AI/AII suffixes); one number on two opportunities → blocked for the owner"),
    ("R10", "values without Phase 3 semantics block the case instead of being read"),
)


def policy_spec() -> dict[str, Any]:
    return {
        "policy_version": POLICY_VERSION,
        "namespace": str(NAMESPACE),
        "answer_table": [
            {"decision_type": r.decision_type, "raw": r.raw, "value": r.value, "param": r.param,
             "extension": r.extension}
            for r in ANSWER_TABLE
        ],
        "no_phase3_semantics": sorted(NO_PHASE3_SEMANTICS),
        "outcome_rules": [list(r) for r in OUTCOME_RULES],
    }


def policy_fingerprint() -> str:
    blob = json.dumps(policy_spec(), ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# Frozen by the owner 2026-09-25 ("Congelar la política como W001-W117/2026-09-25.1 con la huella
# 41ea7f3b…"). Any change to the table, the rules, the version or the namespace breaks it.
FROZEN_POLICY_SHA256: str | None = "41ea7f3bf972896bc07457f10ed280ae0b340a712e33a7311a936cf26a71b408"


# ─── apply gates: what a resolved case may reach (ledger, CRM) ─────────────────────────────────
#
# The owner's Phase 3 approval (2026-09-25) decided how resolved cases reach the ledger and the
# CRM without changing how a case is resolved. Those decisions live here, versioned and
# fingerprinted apart from the frozen resolution policy above, so that policy keeps its approved
# fingerprint and every resolution it produces stays byte-identical.

GATES_VERSION = "G1-G5/2026-09-25.1"

GATE_RULES: tuple[tuple[str, str], ...] = (
    ("G1", "a quotation's stable identity is (printed_number, opportunity_id, document_sha256): the "
           "printed number is kept exactly, unique per opportunity and not globally; no suffix, no "
           "renumbering. A printed number on two opportunities stays blocked for the ledger and the CRM "
           "until the document ledger and crm.quote support that key"),
    ("G2", "a number taken from a file name is never a quote number: a document without a printed "
           "number stays in human review (CN01200, CN01201)"),
    ("G3", "partially blocked opportunities: every unblocked document is recorded in the ledger on its "
           "own; blocked documents stay pending; no opportunity is promoted to the CRM while any of its "
           "documents is blocked. The R1 hold of an opportunity is a CRM hold, not a ledger hold"),
    ("G4", "yes_sent_to_client records delivery only: W099 with W095 is a Labdelivery quotation "
           "delivered to the client, rejected as not OrigenLab's, with no OrigenLab opportunity"),
    ("G5", "approved enum extensions: W103 keep_both_same_quotation_versions (versions of one "
           "quotation), W116 not_applicable:labdelivery_quotation, W073 one_opportunity with an "
           "explicit canonical"),
)

# Blockers that hold only the CRM promotion of an opportunity, never a document's ledger entry (G3).
CRM_ONLY_BLOCKERS = frozenset({B_GROUP_PENDING})

# Which gate or rule governs each blocker, and what would lift it.
BLOCKER_GATE: dict[str, tuple[str, str]] = {
    B_NUMBER_OTHER_OPP: ("G1", "soporte explícito de la clave (número impreso, oportunidad, documento) "
                               "en el validador del ledger de documentos y en crm.quote"),
    B_NO_NUMBER: ("G2", "revisión humana: sin número impreso, el del nombre de archivo no se acepta"),
    B_GROUP_PENDING: ("G3", "sólo retiene el CRM; el documento se registra en el ledger"),
    B_CLIENT_PENDING: ("R1", "identidad del cliente"),
    B_PENDING: ("R1", "decisión del owner"),
    B_CONTRADICTION: ("R2", "resolver la contradicción"),
    B_NO_SEMANTICS: ("R10", "semántica aprobada para el valor"),
    B_UNANSWERED: ("R10", "respuesta en la hoja"),
    B_NEW_OPP_JOINED: ("R7", "corregir la agrupación"),
    B_SEVERAL_RECORDED_OPPS: ("R8", "elegir la oportunidad"),
    B_CANONICAL_CONFLICT: ("R5", "una sola canónica"),
    B_RECORDED_DIFFERS: ("R8", "reconciliar con el ledger"),
    B_EMAIL_TARGET_NO_DOCUMENT: ("R3", "documento asociado"),
}


def gates_spec() -> dict[str, Any]:
    return {
        "gates_version": GATES_VERSION,
        "resolution_policy_version": POLICY_VERSION,
        "resolution_policy_fingerprint": FROZEN_POLICY_SHA256,
        "gate_rules": [list(r) for r in GATE_RULES],
        "crm_only_blockers": sorted(CRM_ONLY_BLOCKERS),
    }


def gates_fingerprint() -> str:
    blob = json.dumps(gates_spec(), ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# The gates restate the owner's approved decisions 1–5 of 2026-09-25; their fingerprint is new
# and is shown to the owner with the dry run before any apply.
FROZEN_GATES_SHA256: str | None = "98b8ab7a493ad09f80693295f51c060e1ba10da922be09a65c43fbf01cca40c5"


def assert_policy_frozen() -> None:
    """Both layers an apply depends on must match what the owner approved."""
    if FROZEN_POLICY_SHA256 is None:
        raise PolicyChanged(f"{POLICY_VERSION} is a proposal: no fingerprint has been approved")
    if policy_fingerprint() != FROZEN_POLICY_SHA256:
        raise PolicyChanged(f"{POLICY_VERSION} differs from the approved fingerprint")
    if FROZEN_GATES_SHA256 is None or gates_fingerprint() != FROZEN_GATES_SHA256:
        raise PolicyChanged(f"{GATES_VERSION} differs from the approved gates fingerprint")


# ─── normalisation ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NormalizedAnswer:
    question_id: str
    decision_type: str
    raw: str
    value: str
    param: str
    in_answer_options: bool
    extension: str
    documents: tuple[str, ...]  # full hashes the question is about
    context_documents: tuple[str, ...]
    case_ids: tuple[str, ...]
    mentioned_documents: tuple[str, ...]  # `sha12` in the question text, resolved
    mentioned_emails: tuple[int, ...] = ()  # "email NNNNNN" in the question text

    def as_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id, "decision_type": self.decision_type, "raw": self.raw,
            "value": self.value, "param": self.param, "in_answer_options": self.in_answer_options,
            "extension": self.extension,
        }


def option_tokens(answer_options: str) -> list[str]:
    """`a | b (explanation) | c:<doc>` → the forms an answer may take: 'a', 'b', 'c:<doc>'."""
    out = []
    for option in (o.strip() for o in answer_options.split("|")):
        if "<" not in option and " (" in option:
            option = option.split(" (", 1)[0]
        out.append(option)
    return out


def in_options(answer_options: str, answer: str) -> bool:
    for token in option_tokens(answer_options):
        if token == answer:
            return True
        if "<" in token:
            prefix, _, rest = token.partition("<")
            suffix = rest.partition(">")[2]
            if answer.startswith(prefix) and answer.endswith(suffix) and len(answer) > len(prefix + suffix):
                return True
    return False


def normalize_answer(decision_type: str, answer: str, answer_options: str = "") -> tuple[AnswerRule, str]:
    """The table row and its parameter for a recorded answer; UnknownAnswer when none fits, or
    when a form outside the row's own options is not a listed extension."""
    answer = (answer or "").strip()
    for rule in ANSWER_TABLE:
        if rule.decision_type != decision_type:
            continue
        param = rule.matches(answer)
        if param is None:
            continue
        if not rule.extension and answer_options and not in_options(answer_options, answer):
            continue
        return rule, param
    raise UnknownAnswer(f"{decision_type}: {answer!r} is not a known answer form")


class DocumentIndex:
    """Full hashes by their 12-character prefix; an ambiguous or unknown prefix is refused."""

    def __init__(self, hashes: Iterable[str]):
        self.hashes = frozenset(hashes)
        by_prefix: dict[str, list[str]] = {}
        for h in self.hashes:
            by_prefix.setdefault(h[:12], []).append(h)
        self._by_prefix = by_prefix

    def resolve(self, ref: str) -> str:
        ref = ref.strip()
        if ref in self.hashes:
            return ref
        hits = self._by_prefix.get(ref[:12], []) if len(ref) >= 12 else []
        hits = [h for h in hits if h.startswith(ref)]
        if len(hits) != 1:
            raise UnknownAnswer(f"document reference {ref!r} resolves to {len(hits)} documents")
        return hits[0]


_SHA12 = re.compile(r"`([0-9a-f]{12})`")
_EMAIL = re.compile(r"\bemail (\d{6})\b")


def normalize_worksheet(rows: Sequence[Mapping[str, str]], index: DocumentIndex) -> list[NormalizedAnswer]:
    out = []
    for row in rows:
        answer = (row.get("answer") or "").strip()
        if not answer:
            raise UnknownAnswer(f"{row['question_id']} has no answer")
        rule, param = normalize_answer(row["decision_type"], answer, row.get("answer_options") or "")
        if rule.param and rule.value in (V_VERSION_OF, V_ONE_OPP, V_SAME_OPP_AS):
            param = index.resolve(param)
        out.append(NormalizedAnswer(
            question_id=row["question_id"], decision_type=row["decision_type"], raw=answer,
            value=rule.value, param=param,
            in_answer_options=in_options(row.get("answer_options") or "", answer),
            extension=rule.extension,
            documents=tuple(index.resolve(s) for s in (row.get("document_sha256") or "").split()),
            context_documents=tuple(index.resolve(s) for s in (row.get("context_documents") or "").split()),
            case_ids=tuple((row.get("case_ids") or "").split()),
            mentioned_documents=tuple(index.resolve(s) for s in _SHA12.findall(row.get("question") or "")),
            mentioned_emails=tuple(sorted({int(e) for e in _EMAIL.findall(row.get("question") or "")})),
        ))
    return out


# ─── resolution ────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Case:
    """One second-pass case, reduced to what the resolution needs."""

    case_id: str
    target: str  # document | email
    sha256: str | None
    email_id: int | None
    sent_at: str
    printed_number: str | None
    widened_number: str | None
    decisions_needed: tuple[str, ...]
    same_serial_documents: tuple[str, ...] = ()


@dataclass(frozen=True)
class Recorded:
    """The ledger's present decision on a document."""

    decision: str
    quote_number: str | None
    opportunity_key: str | None
    opportunity_mode: str | None
    decision_id: str


@dataclass(frozen=True)
class Blocker:
    code: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "es": BLOCKER_ES.get(self.code, self.code), "detail": self.detail}


@dataclass
class DocumentResolution:
    sha256: str
    case_ids: tuple[str, ...]
    email_id: int | None
    sent_at: str
    outcome: str
    questions: tuple[str, ...]
    blockers: list[Blocker] = field(default_factory=list)
    quote_number: str | None = None
    version_role: str | None = None  # canonical | prior | None
    canonical_sha256: str | None = None
    group: str | None = None  # planned opportunity key
    group_members: tuple[str, ...] = ()
    recorded: Recorded | None = None

    @property
    def ledger_blockers(self) -> list[Blocker]:
        return [b for b in self.blockers if b.code not in CRM_ONLY_BLOCKERS]

    @property
    def automatable(self) -> bool:
        """Ready for the ledger. A CRM-only blocker (G3) does not hold the document's own entry."""
        return not self.ledger_blockers and self.outcome in CONFIRM_OUTCOMES + REJECT_OUTCOMES

    def as_dict(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256, "case_ids": list(self.case_ids), "email_id": self.email_id,
            "sent_at": self.sent_at, "outcome": self.outcome, "questions": list(self.questions),
            "blockers": [b.as_dict() for b in self.blockers], "quote_number": self.quote_number,
            "version_role": self.version_role, "canonical_sha256": self.canonical_sha256,
            "opportunity_key": self.group, "opportunity_members": list(self.group_members),
            "recorded": self.recorded and self.recorded.__dict__,
            "automatable": self.automatable,
        }


@dataclass
class EmailResolution:
    case_id: str
    email_id: int | None
    outcome: str
    questions: tuple[str, ...]
    blockers: list[Blocker] = field(default_factory=list)
    documents: tuple[str, ...] = ()

    @property
    def automatable(self) -> bool:
        return not self.blockers and self.outcome == O_EMAIL_REJECT_SUPPLIER

    def as_dict(self) -> dict[str, Any]:
        return {"case_id": self.case_id, "email_id": self.email_id, "outcome": self.outcome,
                "questions": list(self.questions), "blockers": [b.as_dict() for b in self.blockers],
                "documents": list(self.documents), "automatable": self.automatable}


@dataclass
class Resolution:
    answers: list[NormalizedAnswer]
    documents: dict[str, DocumentResolution]
    emails: list[EmailResolution]
    groups: dict[str, tuple[str, ...]]


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)

    def groups(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for x in sorted(self.parent):
            out.setdefault(self.find(x), []).append(x)
        return out


def planned_opportunity_id(members: Iterable[str]) -> str:
    return str(uuid.uuid5(NAMESPACE, "opportunity:" + ",".join(sorted(members))))


def resolve(
    answers: Sequence[NormalizedAnswer],
    cases: Sequence[Case],
    recorded: Mapping[str, Recorded],
    recorded_documents_by_email: Mapping[int, Sequence[str]] | None = None,
) -> Resolution:
    """Every case's outcome under the normalised answers. Pure and deterministic.

    `recorded_documents_by_email` lets a canonical-version question that names "the confirmed
    01046-26 … (email 710516)" reach that already-recorded document: a version family is the
    question's documents, its backticked hashes, the canonical, and the *confirmed* documents of
    the emails it names."""
    recorded_documents_by_email = recorded_documents_by_email or {}
    by_doc: dict[str, list[NormalizedAnswer]] = {}
    by_case: dict[str, list[NormalizedAnswer]] = {}
    for a in answers:
        for d in a.documents:
            by_doc.setdefault(d, []).append(a)
        for c in a.case_ids:
            by_case.setdefault(c, []).append(a)

    doc_cases = [c for c in cases if c.target == "document" and c.sha256]
    email_cases = [c for c in cases if c.target != "document"]
    case_by_sha = {c.sha256: c for c in doc_cases}

    # Documents reached through an email case (715527's two PDFs) are resolved like the others.
    extra_docs: dict[str, Case] = {}
    for c in email_cases:
        for a in by_case.get(c.case_id, []):
            for d in a.documents:
                if d not in case_by_sha and d not in extra_docs:
                    extra_docs[d] = Case(case_id=c.case_id, target="document", sha256=d,
                                         email_id=c.email_id, sent_at=c.sent_at, printed_number=None,
                                         widened_number=None, decisions_needed=())

    docs: dict[str, DocumentResolution] = {}
    uf = _UnionFind()
    forbidden: list[tuple[str, str, str]] = []  # (doc, context doc, question)
    canonical_of: dict[str, set[tuple[str, str]]] = {}  # doc → {(canonical, question)}

    for case in [*doc_cases, *extra_docs.values()]:
        sha = case.sha256
        assert sha is not None
        # A question is about a document when it lists the document's hash (its `case_ids` can
        # name an email case that carries several documents).
        mine = sorted({a.question_id: a for a in by_doc.get(sha, [])}.values(), key=lambda a: a.question_id)
        res = DocumentResolution(sha256=sha, case_ids=(case.case_id,), email_id=case.email_id,
                                 sent_at=case.sent_at, outcome=O_PENDING,
                                 questions=tuple(a.question_id for a in mine), recorded=recorded.get(sha))
        uf.find(sha)
        values = {a.value for a in mine}

        # Coverage: each needed decision type has an answer on this document, or — for a serial
        # collision — on a document it shares the serial with.
        for needed in case.decisions_needed:
            if any(a.decision_type == needed for a in mine):
                continue
            if needed == "serial_collision" and any(
                a.decision_type == needed for d in case.same_serial_documents for a in by_doc.get(d, [])
            ):
                continue
            res.blockers.append(Blocker(B_UNANSWERED, needed))

        for a in mine:
            if a.value in NO_PHASE3_SEMANTICS:
                res.blockers.append(Blocker(B_NO_SEMANTICS, f"{a.question_id}={a.raw}"))

        # `yes_sent_to_client` says the PDF reached the client, not who issued it (W099 defers
        # to W095), so it does not contradict a not-OrigenLab issuer answer.
        sent = values & {V_REAL_SENT, V_KEEP_BOTH, V_SAME_QUOTATION_VERSIONS, V_VERSION_OF, V_NUMBER_PRINTED}
        if not (values & {V_NOT_ORIGENLAB}):
            sent |= values & {V_SENT}
        not_origenlab = values & {V_NOT_ORIGENLAB}
        supplier = values & {V_SUPPLIER}
        not_quotation = values & {V_NOT_CUSTOMER, V_THIS_NOT_SENT, V_NOT_SENT, V_MIRROR_REJECT}
        pending = values & {V_LEAVE_PENDING, V_UNSURE}
        negatives = [n for n in (not_origenlab, supplier, not_quotation) if n]

        if len(negatives) > 1 or (negatives and sent):
            res.outcome = O_CONTRADICTION
            res.blockers.append(Blocker(B_CONTRADICTION, ", ".join(sorted(values))))
        elif pending:
            res.outcome = O_PENDING
            generic = any(a.decision_type == "identify_generic_client" and a.value == V_LEAVE_PENDING
                          for a in mine)
            res.blockers.append(Blocker(B_CLIENT_PENDING if generic else B_PENDING,
                                        ", ".join(a.question_id for a in mine if a.value in pending)))
        elif not_origenlab:
            res.outcome = O_REJECT_NOT_ORIGENLAB
        elif supplier:
            res.outcome = O_REJECT_SUPPLIER
        elif not_quotation:
            res.outcome = O_REJECT_NOT_QUOTATION
        else:
            res.outcome = O_CONFIRM
            number = case.printed_number
            if not number and V_NUMBER_PRINTED in values:
                number = case.widened_number
            if not number:
                res.blockers.append(Blocker(B_NO_NUMBER, "ni impreso ni ampliado confirmado"))
            res.quote_number = number or None

        # Opportunity links.
        for a in mine:
            if a.value in (V_ONE_OPP, V_SAME_QUOTATION_VERSIONS):
                for d in a.documents:
                    uf.union(sha, d)
            if a.value == V_SAME_OPP_AS:
                uf.union(sha, a.param)
            if a.value == V_VERSION_OF:
                family = {*a.documents, *a.mentioned_documents, a.param}
                family |= {d for e in a.mentioned_emails for d in recorded_documents_by_email.get(e, ())
                           if d in recorded and recorded[d].decision == "confirm_customer_quotation"}
                for d in family:
                    uf.union(a.param, d)
                    canonical_of.setdefault(d, set()).add((a.param, a.question_id))
            if a.value == V_NEW_OPP:
                for ctx in a.context_documents:
                    forbidden.append((sha, ctx, a.question_id))
        docs[sha] = res

    # Recorded documents reached by a family or a group take part in the grouping, not the outcome.
    for sha in list(uf.parent):
        if sha not in docs and sha in recorded:
            uf.find(sha)

    # Version roles.
    for sha, res in docs.items():
        canon = canonical_of.get(sha, set())
        canon_docs = {c for c, _ in canon}
        if len(canon_docs) > 1:
            res.blockers.append(Blocker(B_CANONICAL_CONFLICT, ", ".join(sorted(q for _, q in canon))))
        elif canon_docs:
            (c,) = canon_docs
            res.canonical_sha256 = c
            res.version_role = "canonical" if c == sha else "prior"
            if res.outcome == O_CONFIRM and c != sha:
                res.outcome = O_CONFIRM_PRIOR

    # Groups → planned opportunity keys.
    groups: dict[str, tuple[str, ...]] = {}
    for _, members in sorted(uf.groups().items()):
        keys = sorted({recorded[m].opportunity_key for m in members
                       if m in recorded and recorded[m].opportunity_key})
        key = keys[0] if len(keys) == 1 else planned_opportunity_id(members)
        groups[key] = tuple(members)
        for m in members:
            if m in docs:
                docs[m].group = key
                docs[m].group_members = tuple(members)
                if len(keys) > 1:
                    docs[m].blockers.append(Blocker(B_SEVERAL_RECORDED_OPPS, ", ".join(keys)))

    for sha, ctx, q in forbidden:
        if uf.find(sha) == uf.find(ctx):
            docs[sha].blockers.append(Blocker(B_NEW_OPP_JOINED, f"{q}: {ctx[:12]}"))

    # A pending member holds its whole opportunity (W114: do not promote it yet).
    held = {r.group for r in docs.values() if r.outcome == O_PENDING}
    for r in docs.values():
        if r.outcome in CONFIRM_OUTCOMES and r.group in held:
            pending = [m[:12] for m in r.group_members if m in docs and docs[m].outcome == O_PENDING]
            r.blockers.append(Blocker(B_GROUP_PENDING, ", ".join(pending)))

    # One quote number, one opportunity (the ledger refuses otherwise, and crm.quote is unique).
    number_groups: dict[str, set[str]] = {}
    for r in docs.values():
        if r.outcome in CONFIRM_OUTCOMES and r.quote_number and r.group:
            number_groups.setdefault(r.quote_number, set()).add(r.group)
    for sha, rec in recorded.items():
        if rec.decision == "confirm_customer_quotation" and rec.quote_number and sha not in docs:
            key = next((k for k, m in groups.items() if sha in m), rec.opportunity_key)
            if key:
                number_groups.setdefault(rec.quote_number, set()).add(key)
    for r in docs.values():
        if r.outcome in CONFIRM_OUTCOMES and r.quote_number:
            others = number_groups.get(r.quote_number, set()) - {r.group}
            if others:
                r.blockers.append(Blocker(B_NUMBER_OTHER_OPP, f"{r.quote_number}: {len(others)} otra(s)"))

    # What the ledger already says.
    for r in docs.values():
        rec = r.recorded
        if rec is None or rec.decision == "leave_pending":
            continue
        wanted = "confirm_customer_quotation" if r.outcome in CONFIRM_OUTCOMES else (
            "reject_non_quotation" if r.outcome in REJECT_OUTCOMES else None)
        if wanted and (rec.decision != wanted or (
                wanted == "confirm_customer_quotation" and rec.quote_number != r.quote_number)):
            r.blockers.append(Blocker(B_RECORDED_DIFFERS, f"{rec.decision} {rec.quote_number or ''}".strip()))

    emails = []
    for c in email_cases:
        mine = sorted({a.question_id: a for a in by_case.get(c.case_id, [])}.values(), key=lambda a: a.question_id)
        values = {a.value for a in mine}
        er = EmailResolution(case_id=c.case_id, email_id=c.email_id, outcome=O_PENDING,
                             questions=tuple(a.question_id for a in mine),
                             documents=tuple(sorted({d for a in mine for d in a.documents})))
        if values == {V_SUPPLIER}:
            er.outcome = O_EMAIL_REJECT_SUPPLIER
        elif er.documents:
            er.outcome = "resolved_per_document"
        else:
            er.blockers.append(Blocker(B_EMAIL_TARGET_NO_DOCUMENT, ", ".join(sorted(values))))
        emails.append(er)

    for r in docs.values():
        r.blockers.sort(key=lambda b: (b.code, b.detail))
    return Resolution(answers=list(answers), documents=dict(sorted(docs.items())), emails=emails,
                      groups=groups)


def crm_holds(resolution: Resolution) -> dict[str, tuple[str, ...]]:
    """G3: opportunity key → its documents that are not ledger-ready. Such an opportunity is not
    promoted to the CRM, although its ready documents are recorded in the ledger."""
    out: dict[str, tuple[str, ...]] = {}
    for key, members in resolution.groups.items():
        blocked = tuple(m for m in members if m in resolution.documents
                        and not resolution.documents[m].automatable)
        if blocked:
            out[key] = blocked
    return out


def quote_identity(r: DocumentResolution) -> tuple[str, str, str] | None:
    """G1: (printed_number, opportunity_id, document_sha256) for a confirmed quotation."""
    if r.outcome not in CONFIRM_OUTCOMES or not r.quote_number or not r.group:
        return None
    return (r.quote_number, r.group, r.sha256)


# ─── ledger intents ────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LedgerIntent:
    """What an apply would append to a ledger for one resolved case. Nothing is appended here."""

    ledger: str  # document_decisions.jsonl | decisions.jsonl
    target: str  # document sha256 | email id
    decision: str
    decision_id: str
    opportunity_mode: str | None
    opportunity_id: str | None  # existing opportunity (typed) or planned id
    quote_number: str | None
    reason: str
    provenance: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ledger": self.ledger, "target": self.target, "decision": self.decision,
            "decision_id": self.decision_id, "opportunity_mode": self.opportunity_mode,
            "opportunity_id": self.opportunity_id, "quote_number": self.quote_number,
            "reason": self.reason, "provenance": dict(self.provenance),
        }


def intent_id(*parts: str) -> str:
    return str(uuid.uuid5(NAMESPACE, "decision:" + POLICY_VERSION + ":" + ":".join(parts)))


def _reason(r: DocumentResolution, answers: Mapping[str, NormalizedAnswer]) -> str:
    cited = "; ".join(f"{q}={answers[q].raw}" for q in r.questions)
    role = {"canonical": " Versión canónica.", "prior": f" Versión anterior de {(r.canonical_sha256 or '')[:12]}."}
    return f"Fase 3 ({POLICY_VERSION}): {cited}.{role.get(r.version_role or '', '')}"


def ledger_intents(resolution: Resolution, recorded_opportunity_modes: Mapping[str, str]) -> list[LedgerIntent]:
    """One intent per automatable, not-yet-recorded case, in apply order.

    Apply order: rejections first, then per opportunity group its documents by send time, so the
    first document of a new group is `create_new` and the rest join it as `planned`. A group that
    reaches a recorded opportunity joins it in that opportunity's own mode (`existing` for a typed
    CRM opportunity, else `planned`).
    """
    answers = {a.question_id: a for a in resolution.answers}
    intents: list[LedgerIntent] = []
    ready = [r for r in resolution.documents.values() if r.automatable]

    for r in sorted((r for r in ready if r.outcome in REJECT_OUTCOMES), key=lambda r: (r.sent_at, r.sha256)):
        did = intent_id(r.sha256, "reject_non_quotation")
        if r.recorded and r.recorded.decision_id == did:
            continue
        intents.append(LedgerIntent(
            ledger="document_decisions.jsonl", target=r.sha256, decision="reject_non_quotation",
            decision_id=did, opportunity_mode=None, opportunity_id=None, quote_number=None,
            reason=_reason(r, answers) + f" Resultado: {r.outcome}.",
            provenance={"policy_version": POLICY_VERSION, "questions": list(r.questions), "outcome": r.outcome},
        ))

    by_group: dict[str, list[DocumentResolution]] = {}
    for r in ready:
        if r.outcome in CONFIRM_OUTCOMES:
            by_group.setdefault(r.group or r.sha256, []).append(r)
    for key in sorted(by_group):
        members = sorted(by_group[key], key=lambda r: (r.sent_at, r.sha256))
        recorded_mode = recorded_opportunity_modes.get(key)
        opened = recorded_mode is not None
        for r in members:
            did = intent_id(r.sha256, "confirm_customer_quotation", r.quote_number or "", key)
            if r.recorded and r.recorded.decision == "confirm_customer_quotation":
                opened = True  # already on the ledger under this opportunity
                continue
            if recorded_mode == "existing":
                mode = "existing"
            elif opened:
                mode = "planned"
            else:
                mode = "create_new"
            opened = True
            intents.append(LedgerIntent(
                ledger="document_decisions.jsonl", target=r.sha256, decision="confirm_customer_quotation",
                decision_id=did, opportunity_mode=mode, opportunity_id=key, quote_number=r.quote_number,
                reason=_reason(r, answers),
                provenance={"policy_version": POLICY_VERSION, "questions": list(r.questions),
                            "outcome": r.outcome, "version_role": r.version_role,
                            "canonical_sha256": r.canonical_sha256,
                            "opportunity_members": list(r.group_members)},
            ))

    for e in sorted((e for e in resolution.emails if e.automatable), key=lambda e: e.case_id):
        intents.append(LedgerIntent(
            ledger="decisions.jsonl", target=str(e.email_id), decision="reject_non_quotation",
            decision_id=intent_id("email", str(e.email_id), "reject_non_quotation"),
            opportunity_mode=None, opportunity_id=None, quote_number=None,
            reason=f"Fase 3 ({POLICY_VERSION}): " + "; ".join(
                f"{q}={answers[q].raw}" for q in e.questions) + ". Correspondencia de proveedor.",
            provenance={"policy_version": POLICY_VERSION, "questions": list(e.questions), "outcome": e.outcome},
        ))
    return intents


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1)


def resolution_digest(resolution: Resolution, intents: Sequence[LedgerIntent]) -> str:
    blob = canonical_json({
        "documents": {k: v.as_dict() for k, v in resolution.documents.items()},
        "emails": [e.as_dict() for e in resolution.emails],
        "intents": [i.as_dict() for i in intents],
    })
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
