"""Phase 3 ledger apply: which rows of the frozen dry run may be appended, and which may not.

The Phase 3 dry run (`scripts/quote_worksheet_phase3_dryrun.py`) wrote the exact ledger rows an
apply would append (`ledger_intents.jsonl`). This module decides, without opening any file, whether
those rows may be appended to `document_decisions.jsonl` / `decisions.jsonl` as they stand now:

* both approved fingerprints hold — resolution `W001-W117/2026-09-25.1` (41ea7f3b…) and gates
  `G1-G5/2026-09-25.1` (98b8ab7a…, approved 2026-09-25 together with the `phase3` row field);
* no row touches a document the owner held back (`EXCLUDED_DOCUMENTS`): the 13 cross-opportunity
  number collisions (G1), CN01200/CN01201 (G2, no printed number), both 01242-26, the three
  generic-address clients, and W114's opportunity;
* a confirmed number is the one the frozen resolution read from the print (G2), and no number
  lands on two opportunities (G1);
* the ledger is the audited file, or the audited file followed by a prefix of these very rows in
  plan order (a rerun, or a resumed stop). Anything else — a foreign row, a reordered or edited
  row, a plan row whose target already carries a decision — refuses the whole run.

What remains after the rows already present is what an apply appends; on a rerun it is empty.
The script does the reading, the backup and the appends; nothing here writes.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from origenlab_api.v2 import quote_worksheet_resolution as w

DOCUMENT_LEDGER = "document_decisions.jsonl"
EMAIL_LEDGER = "decisions.jsonl"
LEDGER_ORDER = (DOCUMENT_LEDGER, EMAIL_LEDGER)

# Approved by the owner. Restated here, apart from the module constants, so that editing one place
# cannot silently re-approve a changed policy.
APPROVED_POLICY_SHA256 = "41ea7f3bf972896bc07457f10ed280ae0b340a712e33a7311a936cf26a71b408"
APPROVED_GATES_SHA256 = "98b8ab7a493ad09f80693295f51c060e1ba10da922be09a65c43fbf01cca40c5"
# 2026-09-25: "Apruebo también el campo phase3 en las nuevas filas del ledger."
PHASE3_FIELD = "phase3"
OPERATOR = "Rafael"
CONFIRM = "confirm_customer_quotation"
REJECT = "reject_non_quotation"

X_G1 = "G1_numero_en_otra_oportunidad"
X_G2 = "G2_sin_numero_impreso"
X_01242 = "01242_pendiente_owner"
X_CLIENT = "cliente_pendiente"
X_W114 = "W114_oportunidad_retenida"

# Every document the frozen dry run blocked, by full hash. None of them is appended, whatever a
# plan file says. 13 + 2 + 2 + 3 = 20; W114's two documents are also in G1 / cliente_pendiente.
EXCLUDED_DOCUMENTS: Mapping[str, tuple[str, ...]] = {
    "0b2ae3d008c5d7f0fdcf02ec9729b7accafe6bd91a1ceadc6cbde5cd45f2eba1": (X_G1,),  # 01230-26
    "41307074a9a37cf158b99f2f628ea6314b3a9c7e83a1cc2fd356b83f0d7289b0": (X_G1,),  # 01230-26
    "0f392557d5a6a3a9ab0bb6097189509d964a861af196a407a9080529e682180f": (X_G1,),  # 01125-26
    "99643a67f99c8161ee1b26361bdba8f2b1f0341109c4044eabdc2bcd49f11a72": (X_G1, X_W114),  # 01125-26
    "16b0845ae44f62f67384c8aebdb0b52ed125e20adf3391c353fa4ee9cc5f9d54": (X_G1,),  # 01135-26
    "1f02809c9cc5d33a4f433aa8a629aca788f7450c1726e331a0cde2aecbd9e2d6": (X_G1,),  # 01135-26
    "4404ebefb704c1dec62a1135e0abedf143400824e59aec1abe5a08c974caf628": (X_G1,),  # 01162-26
    "6f5f0c8c5f97495ba6a89ffc4322192f646fd3779b0b576475c37a85e0552601": (X_G1,),  # 01162-26
    "626e860c5990aeab9de063712ea7dddf5b514fb429cbead1021eec6d0bb7ee9a": (X_G1,),  # 01158-26
    "aed8047867100774eb58bf8fb3fb249fe1c665a143d1cbb325142cbdfc195b3a": (X_G1,),  # 01158-26
    "89567e8f529ff1d2510a7b17ea123c269e5c1e56eda5e116bd2b79e5bc702988": (X_G1,),  # 01065-26
    "be5d4f42c59f5b82c43e14d4c5c15d04dbf24f9c15b2ca2dd59e3e17b48d688a": (X_G1,),  # 01046-26
    "d5e62cb2a29d60fb5bd32d28634dfaca35dafa65c7ae0d3826596fe92be5bc43": (X_G1,),  # 01046-26
    "18168111ab6e1fb13fc3c8ef74d282eb6fec1844a214552507836937764c9b0c": (X_G2,),  # CN01201 (file name)
    "c1d0b14744fbb8a5cdb5d8f9e2a38d76de20d5277416614a4a863cd9ed1af6fc": (X_G2,),  # CN01200 (file name)
    "54b01b02fe04ecd3c8e751d892adbeaf6a4abe47abbbc651aec631c43bd985e0": (X_01242,),  # SAG, W008
    "e9a62030316676b03d54f28aaabadb7e418b2a67ae6aed300811e0b04062457c": (X_01242,),  # Condecal, W008
    "cb5f6f8f5e2cd0a9d82f97cc57256249be59d1d5cde02ca673ab139b58208bfc": (X_CLIENT,),  # 01051-26, W100
    "cb36ca5003e96bb52b2596820d7ee8ce7fa3e21b9388be002ddc6f41de167aec": (X_CLIENT, X_W114),  # 01127-26, W101
    "4903786e90a088aa98d7d5509812e0a4cb0779612f1abe1566167ecf45f685ac": (X_CLIENT,),  # 01226-26, W102
}
# Questions whose documents stay pending: 01242 (W008), the generic clients (W100–W102), W114.
EXCLUDED_QUESTIONS = frozenset({"W008", "W100", "W101", "W102", "W114"})
EXCLUDED_NUMBERS = frozenset({"01242-26"})


class ApplyRefused(RuntimeError):
    """The run stops before any write."""


def assert_approved() -> None:
    """Both layers equal what the owner approved, in the module and here."""
    w.assert_policy_frozen()
    if w.policy_fingerprint() != APPROVED_POLICY_SHA256 or w.FROZEN_POLICY_SHA256 != APPROVED_POLICY_SHA256:
        raise ApplyRefused(f"resolution policy is not the approved {APPROVED_POLICY_SHA256[:8]}…")
    if w.gates_fingerprint() != APPROVED_GATES_SHA256 or w.FROZEN_GATES_SHA256 != APPROVED_GATES_SHA256:
        raise ApplyRefused(f"gates are not the approved {APPROVED_GATES_SHA256[:8]}…")


# ─── rows ──────────────────────────────────────────────────────────────────────────────────────


def serialize_row(entry: Mapping[str, Any]) -> bytes:
    """Byte-for-byte what `DecisionLedger.append` writes for one entry."""
    return (json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def comparable(entry: Mapping[str, Any]) -> str:
    """An entry without its time stamp: the dry run's rows and the applied rows must match on it."""
    e = dict(entry)
    e.pop("recorded_at", None)
    return json.dumps(e, ensure_ascii=False, sort_keys=True)


def stamp(entry: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    """The row an apply writes: the plan's row with the apply time as `recorded_at`."""
    out = copy.deepcopy(dict(entry))
    out["recorded_at"] = now.isoformat()
    return out


def ledger_of(entry: Mapping[str, Any]) -> str:
    return DOCUMENT_LEDGER if entry.get("target") == "quote_document" else EMAIL_LEDGER


def target_of(entry: Mapping[str, Any]) -> str:
    if ledger_of(entry) == DOCUMENT_LEDGER:
        return str(entry["document_sha256"])
    return str(entry["email_id"])


def opportunity_key(entry: Mapping[str, Any]) -> str | None:
    opp = entry.get("opportunity") or {}
    return opp.get("opportunity_id") or opp.get("planned_opportunity_id")


def audited_prefix(lines: Sequence[bytes], base_sha256: str) -> int | None:
    """How many leading lines hash to the audited ledger; None when the file no longer starts
    with the audited bytes."""
    h = hashlib.sha256()
    if h.hexdigest() == base_sha256:
        return 0
    for n, line in enumerate(lines, start=1):
        h.update(line)
        if h.hexdigest() == base_sha256:
            return n
    return None


# ─── checks ────────────────────────────────────────────────────────────────────────────────────


def entry_problems(entry: Mapping[str, Any], resolved_numbers: Mapping[str, str | None]) -> list[str]:
    """Why one plan row may not be appended; empty when it may.

    `resolved_numbers` is the frozen resolution's printed (or confirmed widened) number per
    confirmed document — never a file-name number (G2)."""
    out: list[str] = []
    target = target_of(entry)
    tag = f"{ledger_of(entry)}:{target[:12]}"
    if target in EXCLUDED_DOCUMENTS:
        out.append(f"{tag} documento retenido ({', '.join(EXCLUDED_DOCUMENTS[target])})")
    p3 = entry.get(PHASE3_FIELD)
    if not isinstance(p3, Mapping):
        out.append(f"{tag} sin campo {PHASE3_FIELD}")
        p3 = {}
    if p3.get("policy_version") != w.POLICY_VERSION or p3.get("gates_version") != w.GATES_VERSION:
        out.append(f"{tag} {PHASE3_FIELD} no cita {w.POLICY_VERSION} / {w.GATES_VERSION}")
    held = sorted(EXCLUDED_QUESTIONS & set(p3.get("questions") or ()))
    if held:
        out.append(f"{tag} cita preguntas retenidas {' '.join(held)}")
    if entry.get("applied") is not False:
        out.append(f"{tag} applied debe ser false")
    if entry.get("operator") != OPERATOR:
        out.append(f"{tag} operador {entry.get('operator')!r}")
    decision = entry.get("decision")
    if decision == CONFIRM:
        number = entry.get("quote_number")
        if ledger_of(entry) != DOCUMENT_LEDGER:
            out.append(f"{tag} una confirmación de Fase 3 va sólo al ledger de documentos")
        if not number:
            out.append(f"{tag} confirmación sin número (G2)")
        elif number in EXCLUDED_NUMBERS:
            out.append(f"{tag} número retenido {number}")
        elif resolved_numbers.get(target) != number:
            out.append(f"{tag} {number} no es el número impreso resuelto "
                       f"({resolved_numbers.get(target) or 'ninguno'}) (G2)")
        if not opportunity_key(entry):
            out.append(f"{tag} confirmación sin oportunidad")
    elif decision == REJECT:
        if entry.get("opportunity") or entry.get("quote_number"):
            out.append(f"{tag} un rechazo no lleva oportunidad ni número")
    else:
        out.append(f"{tag} decisión {decision!r} fuera de la Fase 3")
    return out


def exclusion_problems(blocked_by_resolution: Iterable[str]) -> list[str]:
    """The documents the resolution blocks today must be exactly the owner's retained list: a new
    blocked document, or a retained one that became ready, is for the owner to look at first."""
    blocked = set(blocked_by_resolution)
    retained = set(EXCLUDED_DOCUMENTS)
    out = [f"bloqueado por la resolución pero fuera de la lista retenida: {s[:12]}" for s in sorted(blocked - retained)]
    out += [f"retenido pero ya no bloqueado por la resolución: {s[:12]}" for s in sorted(retained - blocked)]
    return out


def g1_problems(existing: Iterable[Mapping[str, Any]], appended: Iterable[Mapping[str, Any]]) -> list[str]:
    """G1: after the append, no confirmed number sits on two opportunities (latest row per document)."""
    latest: dict[str, Mapping[str, Any]] = {}
    for e in [*existing, *appended]:
        if e.get("target") == "quote_document":
            latest[e["document_sha256"]] = e
    by_number: dict[str, set[str]] = {}
    for e in latest.values():
        if e.get("decision") == CONFIRM and e.get("quote_number"):
            by_number.setdefault(e["quote_number"], set()).add(opportunity_key(e) or "?")
    return [f"G1: {n} quedaría en {len(k)} oportunidades" for n, k in sorted(by_number.items()) if len(k) > 1]


# ─── the per-ledger plan ───────────────────────────────────────────────────────────────────────


@dataclass
class LedgerPlan:
    ledger: str
    base_rows: int
    already: list[dict[str, Any]] = field(default_factory=list)  # plan rows already in the file
    to_append: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def plan_ledger(
    ledger: str,
    plan_entries: Sequence[Mapping[str, Any]],
    base_rows: Sequence[Mapping[str, Any]],
    extra_rows: Sequence[Mapping[str, Any]],
) -> LedgerPlan:
    """Split one ledger's plan rows into already appended / still to append.

    `base_rows` are the audited file's rows, `extra_rows` whatever follows them now. The extra rows
    must be the first plan rows, in plan order, equal except for `recorded_at`."""
    lp = LedgerPlan(ledger=ledger, base_rows=len(base_rows))
    ids = [e["decision_id"] for e in plan_entries]
    if len(ids) != len(set(ids)):
        lp.problems.append(f"{ledger}: decision_id repetido en el plan")
    targets = [target_of(e) for e in plan_entries]
    if len(targets) != len(set(targets)):
        lp.problems.append(f"{ledger}: el plan decide dos veces el mismo objetivo")
    base_targets = {target_of(r) for r in base_rows}
    base_ids = {r.get("decision_id") for r in base_rows}
    for e in plan_entries:
        if target_of(e) in base_targets:
            lp.problems.append(f"{ledger}: {target_of(e)[:12]} ya tiene una decisión; no se sobrescribe")
        if e["decision_id"] in base_ids:
            lp.problems.append(f"{ledger}: {e['decision_id']} ya está en el ledger auditado")
    if len(extra_rows) > len(plan_entries):
        lp.problems.append(f"{ledger}: {len(extra_rows)} filas nuevas, más que el plan ({len(plan_entries)})")
    for n, row in enumerate(extra_rows):
        if n >= len(plan_entries) or comparable(row) != comparable(plan_entries[n]):
            lp.problems.append(f"{ledger}: la fila {len(base_rows) + n + 1} no es la fila {n + 1} del plan "
                               "(ajena, editada o reordenada)")
            break
    if not lp.problems:
        lp.already = [dict(e) for e in plan_entries[:len(extra_rows)]]
        lp.to_append = [dict(e) for e in plan_entries[len(extra_rows):]]
    return lp


@dataclass
class ApplyPlan:
    ledgers: dict[str, LedgerPlan]
    problems: list[str]

    @property
    def writes(self) -> int:
        return sum(len(lp.to_append) for lp in self.ledgers.values())

    @property
    def ok(self) -> bool:
        return not self.problems and not any(lp.problems for lp in self.ledgers.values())


def plan_apply(
    plan_entries: Sequence[Mapping[str, Any]],
    recomputed_entries: Sequence[Mapping[str, Any]],
    resolved_numbers: Mapping[str, str | None],
    base_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    extra_rows: Mapping[str, Sequence[Mapping[str, Any]]],
) -> ApplyPlan:
    """The whole decision. `recomputed_entries` is the frozen resolution run again over the audited
    ledgers; it must reproduce the plan row for row (time stamp aside)."""
    problems: list[str] = []
    if [comparable(e) for e in plan_entries] != [comparable(e) for e in recomputed_entries]:
        problems.append("el plan no coincide con la resolución recalculada sobre los ledgers auditados")
    for e in plan_entries:
        problems.extend(entry_problems(e, resolved_numbers))
    problems.extend(g1_problems(base_rows.get(DOCUMENT_LEDGER, ()), plan_entries))
    ledgers = {
        name: plan_ledger(name, [e for e in plan_entries if ledger_of(e) == name],
                          base_rows.get(name, ()), extra_rows.get(name, ()))
        for name in LEDGER_ORDER
    }
    return ApplyPlan(ledgers=ledgers, problems=problems)
