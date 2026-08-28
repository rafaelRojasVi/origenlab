"""Pure / side-effect–free helpers for the volume marketing (broad) lane.

``process_broad_marketing_contacts`` CLI (scripts) composes I/O, readonly SQLite, and
gate context; this module implements row processing and output shaping only.

For operator entrypoint and I/O, see :mod:`scripts.leads.process_broad_marketing_contacts`.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from origenlab_email_pipeline.candidate_export_gate import GateContext, evaluate_export_eligibility
from origenlab_email_pipeline.csv_contracts import (
    extract_email_from_aliases,
    normalize_confidence,
    sanitize_csv_text,
    source_is_official_registry_exception,
    source_looks_third_party,
    source_host_matches_domain,
    validate_confidence,
    validate_email_syntax,
    validate_source_url,
)

REQUIRED_INPUT_COLUMNS: tuple[str, ...] = (
    "institution_name",
    "region",
    "city",
    "type",
    "contact_email",
    "contact_label",
    "source_url",
    "confidence",
)

SEND_READY_FIELDS: tuple[str, ...] = (
    "case_id",
    "contact_email",
    "email_source",
    "institution_name",
    "region",
    "city",
    "type",
    "contact_label",
    "source_url",
    "confidence",
    "fit_signal",
    "variant_type",
    "quality_decision",
    "quality_reasons",
)

_GENERIC_LABELS: frozenset[str] = frozenset(
    {
        "",
        "contact",
        "contacto",
        "email",
        "general",
        "n/a",
        "na",
        "s/a",
        "info",
        "informacion",
        "información",
        "solicitud",
        "admin",
    }
)
_WEAK_LABEL_TOKENS: tuple[str, ...] = (
    "office",
    "oficina",
    "routing",
    "mesa",
    "recepcion",
    "recepción",
    "oirs",
)
_INSTITUTION_STOPWORDS: frozenset[str] = frozenset(
    {
        "de",
        "del",
        "la",
        "el",
        "los",
        "las",
        "y",
        "en",
        "para",
        "hospital",
        "universidad",
        "instituto",
        "centro",
        "laboratorio",
        "regional",
        "clinica",
        "clínica",
        "servicio",
        "salud",
    }
)
_WEAK_SOURCE_PATHS: frozenset[str] = frozenset({"", "/", "/index", "/home", "/inicio"})
_UNIVERSITY_TOKENS: tuple[str, ...] = (
    "universidad",
    "university",
    "facultad",
    "faculty",
    "campus",
)
_UNIVERSITY_GENERIC_LOCAL_PARTS: frozenset[str] = frozenset(
    {
        "contacto",
        "contact",
        "info",
        "informacion",
        "información",
        "informaciones",
        "admision",
        "admisiones",
        "comunicaciones",
        "extension",
        "prensa",
        "secretaria",
        "secretarias",
        "rectoria",
        "rectoría",
        "observacion",
        "observación",
        "observaciones",
        "investigacion",
        "investigación",
        "vinculacion",
        "vinculación",
        "mesaayuda",
        "noreply",
        "no-reply",
    }
)

# Email domains treated as “main campus” inboxes — generic local-parts here need manual review.
_MAIN_UNIVERSITY_EMAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "uchile.cl",
        "uv.cl",
        "uach.cl",
        "udec.cl",
        "ubiobio.cl",
        "usach.cl",
        "ucsc.cl",
        "utalca.cl",
        "uct.cl",
        "ufro.cl",
        "puc.cl",
        "uc.cl",
    }
)

# Single URL path segments that are too broad for “send-ready” evidence alone.
_BROAD_SINGLE_PATH_SEGMENTS: frozenset[str] = frozenset(
    {
        "contacto",
        "contact",
        "investigacion",
        "investigación",
        "ciencias",
        "quimica",
        "química",
        "fisica",
        "física",
        "alimentos",
        "extension",
        "postgrado",
        "admision",
        "admisión",
        "comunicaciones",
        "secretaria",
        "secretaría",
        "rectoria",
        "rectoría",
        "inicio",
        "home",
        "index",
        "preguntas",
        "pregunta",
    }
)

_PROCUREMENT_SINGLE_SLUGS: frozenset[str] = frozenset(
    {
        "compras",
        "adquisiciones",
        "proveedores",
        "licitaciones",
        "contratacion",
        "contratación",
        "contrataciones",
        "abastecimiento",
    }
)
_LAB_RELEVANCE_TOKENS: tuple[str, ...] = (
    "laboratorio",
    "laboratory",
    "analisis",
    "análisis",
    "analitica",
    "analítica",
    "microbiologia",
    "microbiología",
    "quimica",
    "química",
    "planta piloto",
    "research",
    "investigacion",
    "investigación",
    "centro de investigacion",
    "centro de investigación",
    "transferencia tecnologica",
    "transferencia tecnológica",
    "compras",
    "adquisiciones",
    "procurement",
    "proveedores",
    "food",
    "alimentos",
    "agua",
    "ambiente",
)
_GENERIC_SOURCE_PATH_TOKENS: tuple[str, ...] = (
    "inicio",
    "home",
    "index",
)


@dataclass(frozen=True, slots=True)
class BroadMarketingProcessResult:
    """Result of classifying and splitting reviewed marketing contact rows (no I/O)."""

    safe_rows: list[dict[str, str]]
    blocked_rows: list[dict[str, str]]
    review_rows: list[dict[str, str]]
    send_ready_rows: list[dict[str, str]]


def load_master_norms_from_csv(path: Path) -> set[str]:
    """Load normalized email keys from do-not-repeat master (email_norm or email)."""
    if not path.is_file():
        return set()
    out: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            em = str(row.get("email_norm") or row.get("email") or "").strip().lower()
            if em:
                out.add(em)
    return out


def row_schema_errors(row: dict[str, str]) -> list[str]:
    err: list[str] = []
    em = validate_email_syntax(extract_email_from_aliases(row, ("contact_email",)))
    if not em:
        err.append("invalid_email")
    conf = normalize_confidence(row.get("confidence", ""))
    if not validate_confidence(conf) or conf not in {"high", "medium", "low"}:
        err.append("invalid_confidence")
    src = str(row.get("source_url") or "").strip()
    if not src:
        err.append("missing_source_url")
    elif not validate_source_url(src):
        err.append("invalid_source_url")
    fs = str(row.get("fit_signal") or "").strip()
    if fs and len(fs) > 2000:
        err.append("fit_signal_too_long")
    return err


def is_generic_label(label: str) -> bool:
    s = str(label or "").strip().lower()
    if s in _GENERIC_LABELS:
        return True
    return any(tok in s for tok in _WEAK_LABEL_TOKENS)


def is_weak_fit(fit_signal: str) -> bool:
    return len(str(fit_signal or "").strip()) < 4


def _email_domain(email: str) -> str:
    e = str(email or "").strip().lower()
    if "@" not in e:
        return ""
    return e.rsplit("@", 1)[1]


def _source_host(source_url: str) -> str:
    if not validate_source_url(source_url):
        return ""
    return (urlparse(source_url).hostname or "").strip().lower()


def _source_path_is_weak(source_url: str) -> bool:
    if not validate_source_url(source_url):
        return True
    p = (urlparse(source_url).path or "").strip().lower()
    if p in _WEAK_SOURCE_PATHS:
        return True
    if len(p) <= 1:
        return True
    return False


def _source_path_is_homepage(source_url: str) -> bool:
    if not validate_source_url(source_url):
        return False
    p = (urlparse(source_url).path or "").strip().lower().rstrip("/")
    return p in {"", "/"}


def _email_local_part(email: str) -> str:
    e = str(email or "").strip().lower()
    if "@" not in e:
        return ""
    return e.split("@", 1)[0]


def _is_university_like(institution_type: str, institution_name: str, source_url: str) -> bool:
    haystack = " ".join(
        [
            str(institution_type or "").strip().lower(),
            str(institution_name or "").strip().lower(),
            str(source_url or "").strip().lower(),
        ]
    )
    return any(tok in haystack for tok in _UNIVERSITY_TOKENS)


def _slug_has_embedded_lab_token(slug: str) -> bool:
    """Compound slug like ``laboratorio-microbiologia`` counts as specific evidence."""
    s = slug.lower().replace("-", "").replace("_", "")
    return any(
        x in s
        for x in (
            "laboratorio",
            "microbiologia",
            "microbiología",
            "analisis",
            "análisis",
            "biologia",
            "biología",
            "doping",
            "ciq",
            "servicios",
        )
    )


def _email_domain_matches_main_university(email_domain: str) -> bool:
    d = (email_domain or "").strip().lower()
    if not d:
        return False
    return any(d == root or d.endswith("." + root) for root in _MAIN_UNIVERSITY_EMAIL_DOMAINS)


def _is_generic_university_mailbox(email: str, institution_type: str, institution_name: str) -> bool:
    """Root mailboxes on main university domains (contacto@, info@, …) are never send-ready."""
    if not _is_university_like(institution_type, institution_name, ""):
        return False
    local = _email_local_part(email)
    if local not in _UNIVERSITY_GENERIC_LOCAL_PARTS:
        return False
    return _email_domain_matches_main_university(_email_domain(email))


def _broken_looking_url(source_url: str) -> bool:
    s = str(source_url or "").strip().lower()
    if "://" in s:
        after_scheme = s.split("://", 1)[1]
        if "//" in after_scheme:
            return True
    if "deepsearch" in s or "generated" in s or "placeholder" in s:
        return True
    return s.count("%20%20") >= 1


def _weak_promotional_source_url(source_url: str) -> bool:
    """Homepage, single broad faculty path, or otherwise non-actionable source."""
    if not validate_source_url(source_url):
        return True
    if _broken_looking_url(source_url):
        return True
    if _source_path_is_homepage(source_url):
        return True
    parsed = urlparse(source_url)
    segs = [x for x in (parsed.path or "").strip("/").split("/") if x]
    if not segs:
        return True
    if len(segs) >= 2:
        return False
    slug = segs[0].lower()
    if slug in _PROCUREMENT_SINGLE_SLUGS:
        return False
    if _slug_has_embedded_lab_token(slug):
        return False
    if slug in _BROAD_SINGLE_PATH_SEGMENTS:
        return True
    return len(slug) < 10


def _label_is_general_contact_poor(label: str) -> bool:
    s = re.sub(r"[\s_\-]+", "", str(label or "").strip().lower())
    if "generalcontact" in s:
        return True
    if s in ("general", "contacto", "contact", "email", "info"):
        return True
    return is_generic_label(label) and "general" in str(label or "").lower()


def _institution_claims_specific_unit(institution_name: str) -> bool:
    n = str(institution_name or "").lower()
    needles = (
        "laboratorio",
        "facultad",
        "centro de",
        "instituto de",
        "departamento",
        "programa de",
        "escuela de",
        "instituto ",
    )
    return any(x in n for x in needles)


def _specific_org_general_contact_mismatch(
    *, contact_label: str, institution_name: str, source_url: str
) -> bool:
    """Specific org name but mailbox/label is a generic «contact» without a deep source."""
    if not _label_is_general_contact_poor(contact_label):
        return False
    if not _institution_claims_specific_unit(institution_name):
        return False
    return _weak_promotional_source_url(source_url) or (not _source_page_is_specific(source_url))


def _has_lab_relevance_signal(*, source_url: str, fit_signal: str, contact_label: str) -> bool:
    """Fit/contact text can establish relevance; URL alone must not be a single broad slug."""
    fl = str(fit_signal or "").strip().lower()
    cl = str(contact_label or "").strip().lower()
    hay_nc = f"{fl} {cl}"
    if any(tok in hay_nc for tok in _LAB_RELEVANCE_TOKENS):
        return True
    if not validate_source_url(source_url):
        return False
    parsed = urlparse(source_url)
    path = (parsed.path or "").strip().lower()
    if path.endswith(".pdf"):
        return True
    segs = [s for s in path.strip("/").split("/") if s]
    if not segs:
        return False
    if len(segs) >= 2:
        url_low = source_url.lower()
        return any(tok in url_low for tok in _LAB_RELEVANCE_TOKENS)
    slug = segs[0].lower()
    if slug in _PROCUREMENT_SINGLE_SLUGS:
        return True
    if _slug_has_embedded_lab_token(slug):
        return True
    return False


def _source_page_is_specific(source_url: str) -> bool:
    if not validate_source_url(source_url):
        return False
    parsed = urlparse(source_url)
    path = (parsed.path or "").strip().lower()
    if path.endswith(".pdf"):
        return True
    if path in _WEAK_SOURCE_PATHS or len(path.strip("/")) <= 0:
        return False
    segs = [s for s in path.strip("/").split("/") if s]
    if not segs:
        return False
    if len(segs) >= 2:
        return True
    slug = segs[0].lower()
    if slug in _PROCUREMENT_SINGLE_SLUGS:
        return True
    if slug in _GENERIC_SOURCE_PATH_TOKENS:
        return False
    if slug in _BROAD_SINGLE_PATH_SEGMENTS:
        return False
    if _slug_has_embedded_lab_token(slug):
        return True
    return len(slug) >= 12


def _domain_tokens(value: str) -> set[str]:
    parts = [p.strip().lower() for p in str(value or "").split(".") if p.strip()]
    return {p for p in parts if len(p) >= 4}


def _institution_tokens(inst: str) -> set[str]:
    txt = str(inst or "").strip().lower()
    words = re.findall(r"[a-z0-9áéíóúñ]+", txt)
    out: set[str] = set()
    for w in words:
        if len(w) < 5:
            continue
        if w in _INSTITUTION_STOPWORDS:
            continue
        out.add(w)
    return out


def augment_row(base: dict[str, str], **extra: str) -> dict[str, str]:
    o = dict(base)
    for k, v in extra.items():
        o[k] = v
    return o


def _dedupe_preserve(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        t = x.strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _quality_decision_for_blocked(block_reason: str) -> str:
    br = (block_reason or "").lower()
    if "supplier" in br:
        return "skip_supplier"
    return "blocked_do_not_repeat"


def _quality_decision_for_review(review_reasons: list[str]) -> str:
    rs = {r.strip() for r in review_reasons if r.strip()}
    if "low_confidence" in rs:
        return "skip_low_fit"
    return "needs_better_contact"


def quality_review_reasons(
    *,
    email: str,
    institution_name: str,
    institution_type: str,
    source_url: str,
    fit_signal: str,
    contact_label: str,
    confidence: str,
) -> list[str]:
    reasons: list[str] = []
    em_domain = _email_domain(email)
    src_host = _source_host(source_url)
    weak_source = _source_path_is_weak(source_url)
    weak_fit = is_weak_fit(fit_signal)
    generic = is_generic_label(contact_label)
    has_lab_relevance = _has_lab_relevance_signal(
        source_url=source_url, fit_signal=fit_signal, contact_label=contact_label
    )
    source_is_homepage = _source_path_is_homepage(source_url)
    source_is_specific = _source_page_is_specific(source_url)
    conf = normalize_confidence(confidence)

    if src_host and em_domain and not source_host_matches_domain(source_url, em_domain):
        # institutional mismatch signal: source host and email domain diverge.
        # Keep this conservative: only flag when domains have no meaningful token overlap.
        if not (_domain_tokens(src_host) & _domain_tokens(em_domain)):
            reasons.append("domain_mismatch")

    inst_tokens = _institution_tokens(institution_name)
    if inst_tokens and em_domain:
        token_hit = any(t in em_domain or (src_host and t in src_host) for t in inst_tokens)
        if not token_hit and (weak_source or "domain_mismatch" in reasons):
            reasons.append("institution_email_mismatch")
            reasons.append("email_domain_institution_mismatch")

    if generic and (weak_fit or weak_source):
        reasons.append("generic_contact_weak_evidence")

    if weak_source and (
        generic or weak_fit or "domain_mismatch" in reasons or "institution_email_mismatch" in reasons
    ):
        reasons.append("weak_source_match")

    if _is_generic_university_mailbox(email, institution_type, institution_name):
        reasons.append("generic_university_contact")

    if _weak_promotional_source_url(source_url):
        reasons.append("weak_source_url")

    if _specific_org_general_contact_mismatch(
        contact_label=contact_label, institution_name=institution_name, source_url=source_url
    ):
        reasons.append("specific_org_but_general_contact")

    if source_is_homepage and (generic or conf == "low"):
        reasons.append("homepage_source_weak_evidence")

    if (not source_is_specific) and (not has_lab_relevance):
        reasons.append("source_page_not_specific")

    seen: set[str] = set()
    out: list[str] = []
    for r in reasons:
        if r in seen:
            continue
        seen.add(r)
        out.append(r)
    return out


def process_reviewed_marketing_rows(
    rows: list[dict[str, str]],
    *,
    master_email_norms: set[str],
    ctx: GateContext,
    variant_type: str = "broad_marketing",
) -> BroadMarketingProcessResult:
    """
    Classify each input row into safe / blocked / review buckets and build send_ready rows
    (same policy as the legacy in-script loop).
    """
    safe_rows: list[dict[str, str]] = []
    blocked_rows: list[dict[str, str]] = []
    review_rows: list[dict[str, str]] = []
    send_ready: list[dict[str, str]] = []

    seen_batch: dict[str, int] = {}
    case_seq = 0

    for i, raw in enumerate(rows, start=2):
        base = {k: sanitize_csv_text(raw.get(k, "")) for k in raw.keys()}
        line_errors = row_schema_errors(raw)
        em = validate_email_syntax(extract_email_from_aliases(raw, ("contact_email",)))
        inst = str(raw.get("institution_name") or "").strip()

        if line_errors:
            br = ";".join(line_errors)
            blocked_rows.append(
                augment_row(
                    base,
                    block_reason=br,
                    source_line=str(i),
                    quality_decision=_quality_decision_for_blocked(br),
                    quality_reasons=br,
                )
            )
            continue

        assert em is not None
        if em in seen_batch:
            blocked_rows.append(
                augment_row(
                    base,
                    block_reason="duplicate_input",
                    source_line=str(i),
                    duplicate_of_line=str(seen_batch[em]),
                    quality_decision=_quality_decision_for_blocked("duplicate_input"),
                    quality_reasons="duplicate_input",
                )
            )
            continue
        seen_batch[em] = i

        if em in master_email_norms:
            blocked_rows.append(
                augment_row(
                    base,
                    block_reason="do_not_repeat_master",
                    source_line=str(i),
                    quality_decision=_quality_decision_for_blocked("do_not_repeat_master"),
                    quality_reasons="do_not_repeat_master",
                )
            )
            continue

        gate = evaluate_export_eligibility(contact_email=em, institution_name=inst, ctx=ctx)
        if not gate.eligible:
            br = ";".join(gate.reasons)
            blocked_rows.append(
                augment_row(
                    base,
                    block_reason=br,
                    source_line=str(i),
                    quality_decision=_quality_decision_for_blocked(br),
                    quality_reasons=br,
                )
            )
            continue

        src = str(raw.get("source_url") or "").strip()
        conf = normalize_confidence(raw.get("confidence", ""))
        review_reasons: list[str] = []
        preseeded_review = str(raw.get("review_reason") or "").strip()
        if preseeded_review:
            review_reasons.extend([r.strip() for r in preseeded_review.split(";") if r.strip()])
        if conf == "low":
            review_reasons.append("low_confidence")
        if source_looks_third_party(src) and not source_is_official_registry_exception(src):
            review_reasons.append("third_party_source")
        if is_generic_label(str(raw.get("contact_label") or "")) and is_weak_fit(
            str(raw.get("fit_signal") or "")
        ):
            review_reasons.append("generic_label_weak_fit")
        review_reasons.extend(
            quality_review_reasons(
                email=em,
                institution_name=inst,
                institution_type=str(raw.get("type") or ""),
                source_url=src,
                fit_signal=str(raw.get("fit_signal") or ""),
                contact_label=str(raw.get("contact_label") or ""),
                confidence=str(raw.get("confidence") or ""),
            )
        )
        review_reasons = _dedupe_preserve(review_reasons)

        extra: dict[str, str] = {"source_line": str(i)}
        if review_reasons:
            rr_joined = ";".join(review_reasons)
            review_rows.append(
                augment_row(
                    base,
                    review_reason=rr_joined,
                    quality_decision=_quality_decision_for_review(review_reasons),
                    quality_reasons=rr_joined,
                    **extra,
                )
            )
        else:
            case_seq += 1
            case_id = f"MKT-{case_seq:05d}"
            safe_row = augment_row(base, case_id=case_id, **extra)
            safe_rows.append(safe_row)
            send_ready.append(
                {
                    "case_id": case_id,
                    "contact_email": validate_email_syntax(
                        extract_email_from_aliases(safe_row, ("contact_email",))
                    )
                    or "",
                    "email_source": "marketing_contacts",
                    "institution_name": safe_row.get("institution_name", ""),
                    "region": safe_row.get("region", ""),
                    "city": safe_row.get("city", ""),
                    "type": safe_row.get("type", ""),
                    "contact_label": safe_row.get("contact_label", ""),
                    "source_url": safe_row.get("source_url", ""),
                    "confidence": safe_row.get("confidence", ""),
                    "fit_signal": safe_row.get("fit_signal", ""),
                    "variant_type": variant_type,
                    "quality_decision": "pass_quality_gate",
                    "quality_reasons": "",
                }
            )

    return BroadMarketingProcessResult(
        safe_rows=safe_rows,
        blocked_rows=blocked_rows,
        review_rows=review_rows,
        send_ready_rows=send_ready,
    )


def safe_output_fieldnames() -> list[str]:
    return list(
        dict.fromkeys(list(REQUIRED_INPUT_COLUMNS) + ["fit_signal", "case_id", "source_line"])
    )


def blocked_output_fieldnames() -> list[str]:
    return list(
        dict.fromkeys(
            list(REQUIRED_INPUT_COLUMNS)
            + [
                "fit_signal",
                "block_reason",
                "source_line",
                "duplicate_of_line",
                "quality_decision",
                "quality_reasons",
            ]
        )
    )


def review_output_fieldnames() -> list[str]:
    return list(
        dict.fromkeys(
            list(REQUIRED_INPUT_COLUMNS)
            + [
                "fit_signal",
                "review_reason",
                "source_line",
                "quality_decision",
                "quality_reasons",
            ]
        )
    )


def build_marketing_contacts_summary(
    *,
    db_path: Path,
    workspace: Path,
    input_path: Path,
    master_path: Path,
    gmail_user: str,
    sent_folders: list[str] | tuple[str, ...],
    input_row_count: int,
    result: BroadMarketingProcessResult,
    out_safe: Path,
    out_blocked: Path,
    out_review: Path,
    out_send: Path,
    out_summary: Path,
) -> dict[str, Any]:
    """Build the marketing_contacts_summary.json object (keys stable; sort_keys in caller)."""
    review_reason_counts: dict[str, int] = {}
    for row in result.review_rows:
        reasons = str(row.get("review_reason") or "").split(";")
        for reason in reasons:
            rk = reason.strip()
            if not rk:
                continue
            review_reason_counts[rk] = review_reason_counts.get(rk, 0) + 1
    return {
        "schema_version": "1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "db_path": str(db_path.resolve()),
        "workspace": str(workspace.resolve()),
        "input": str(input_path.resolve()),
        "master_path": str(master_path.resolve()),
        "gmail_user": gmail_user,
        "sent_folders": list(sent_folders),
        "counts": {
            "input_rows": input_row_count,
            "safe_to_send": len(result.safe_rows),
            "blocked": len(result.blocked_rows),
            "needs_manual_review": len(result.review_rows),
            "send_ready_marketing": len(result.send_ready_rows),
        },
        "quality_review_reason_counts": review_reason_counts,
        "outputs": {
            "marketing_safe_to_send": str(out_safe.resolve()),
            "marketing_blocked_already_known": str(out_blocked.resolve()),
            "marketing_needs_manual_review": str(out_review.resolve()),
            "send_ready_marketing": str(out_send.resolve()),
            "marketing_contacts_summary": str(out_summary.resolve()),
        },
    }
