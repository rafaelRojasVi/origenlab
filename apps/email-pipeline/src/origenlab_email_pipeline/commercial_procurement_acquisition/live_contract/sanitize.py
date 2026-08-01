"""Deterministic sanitization of live procurement responses for committed fixtures."""

from __future__ import annotations

import copy
import hashlib
import re
from typing import Any

from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.identity import (
    normalize_mercado_publico_codigo,
)

SANITIZER_VERSION = "live_contract_sanitizer_v1"

_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
# Chilean mobile/landline-ish; avoid matching ISO timestamps (YYYY-MM-DDTHH:MM:SS).
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?56[\s-]*)?(?:\(?\d{1,2}\)?[\s-]*)?\d{3,4}[\s-]\d{4}(?!\d)"
)
_RUT_RE = re.compile(r"\b\d{1,2}\.?\d{3}\.?\d{3}-[\dkK]\b")
_URL_RE = re.compile(r"https?://[^\s\"']+", re.I)
_PATH_RE = re.compile(r"/(?:home|Users|mnt)/[^\s\"']+")
_ISO_TS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?$"
)

# Exact key matches (casefolded) → synthetic identity rewrite.
_CODE_KEYS = {"codigoexterno", "codigo"}
_OCID_KEYS = {"ocid"}
_ORG_NAME_KEYS = {
    "nombre",
    "name",
    "nombreorganismo",
    "nombreador",
    "buyer",
}
_PERSON_NAME_KEYS = {"nombreusuario", "nombreunidad", "cargousuario"}
_GEO_KEYS = {
    "comunaunidad",
    "comuna",
    "regionunidad",
    "region",
    "ciudad",
    "ciudadunidad",
}
_ADDRESS_KEYS = {"direccion", "address", "direccionunidad"}
_EMAIL_KEYS = {"email", "correo"}
_PHONE_KEYS = {"fono", "telefono", "phone", "fonousuario"}
_RUT_KEYS = {"rut", "rutunidad", "rutproveedor", "rutusuario"}
_ORG_ID_KEYS = {
    "codigoorganismo",
    "codigounidad",
    "codigousuario",
    "organizationid",
    "orgid",
}
# Preserve status / schema vocabulary keys (do not rewrite).
_PRESERVE_KEYS = {
    "codigoestado",
    "estado",
    "nombreetado",
    "nombretipo",
    "codigotipo",
    "codigoestadotender",
    "version",
    "cantidad",
    "cantidadreclamos",
}


def _stable_token(kind: str, value: str) -> str:
    digest = hashlib.sha256(f"{kind}|{value}".encode("utf-8")).hexdigest()[:10]
    return f"{kind}_{digest}"


def _synthetic_codigo(seed: str, ordinal: int) -> str:
    # Structurally valid Mercado Público shape.
    n = int(hashlib.sha256(seed.encode()).hexdigest()[:6], 16) % 9000 + 1000
    return f"{n}-{ordinal + 1}-LE26"


def _shift_timestamp(value: str, seed: str) -> str:
    """Preserve type/shape of timestamps while removing exact live instants."""
    if not _ISO_TS_RE.match(value.strip()):
        return value
    digest = hashlib.sha256(f"ts|{seed}|{value}".encode()).hexdigest()
    hour = int(digest[:2], 16) % 24
    minute = int(digest[2:4], 16) % 60
    second = int(digest[4:6], 16) % 60
    # Keep calendar day from seed contract day when present; else fixed synthetic day.
    day_match = re.match(r"^(\d{4}-\d{2}-\d{2})", value.strip())
    day = day_match.group(1) if day_match else "2026-01-15"
    if "T" in value or " " in value.strip()[10:]:
        frac = ""
        if "." in value:
            frac = ".000"
        tz = "Z" if value.strip().endswith("Z") else ""
        return f"{day}T{hour:02d}:{minute:02d}:{second:02d}{frac}{tz}"
    return day


def _scrub_string(value: str, *, seed: str) -> str:
    if _ISO_TS_RE.match(value.strip()):
        return _shift_timestamp(value, seed)
    text = _EMAIL_RE.sub("<redacted_email>", value)
    text = _URL_RE.sub("<redacted_url>", text)
    text = _PATH_RE.sub("<redacted_path>", text)
    text = _RUT_RE.sub("<redacted_rut>", text)
    text = _PHONE_RE.sub("<redacted_phone>", text)
    return text


def _key_looks_like_url(key_l: str) -> bool:
    return any(tok in key_l for tok in ("url", "uri", "link", "enlace", "href"))


def sanitize_live_payload(
    payload: Any,
    *,
    source_kind: str,
    seed: str = "live_contract",
) -> dict[str, Any]:
    """Preserve structure/types; replace identifiers with synthetic stable values."""
    code_map: dict[str, str] = {}
    id_map: dict[str, str] = {}
    counters = {"codigo": 0, "ocid": 0, "id": 0, "org": 0, "orgid": 0}

    def map_codigo(raw: str) -> str:
        norm = normalize_mercado_publico_codigo(raw) or raw.strip().casefold()
        if norm not in code_map:
            code_map[norm] = _synthetic_codigo(f"{seed}|{norm}", counters["codigo"])
            counters["codigo"] += 1
        return code_map[norm]

    def map_id(kind: str, raw: str) -> str:
        key = f"{kind}|{raw.strip().casefold()}"
        if key not in id_map:
            counters[kind] = counters.get(kind, 0) + 1
            id_map[key] = _stable_token(kind, raw)
        return id_map[key]

    def walk(node: Any, path: tuple[str, ...] = ()) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for k, v in node.items():
                key = str(k)
                key_l = key.casefold()
                child_path = path + (key,)
                if key_l in _PRESERVE_KEYS:
                    out[key] = walk(v, child_path)
                    continue
                if key_l in _CODE_KEYS and isinstance(v, str) and v.strip():
                    out[key] = map_codigo(v)
                    continue
                if key_l in _OCID_KEYS and isinstance(v, str) and v.strip():
                    out[key] = f"ocds-synth-{map_id('ocid', v)}"
                    continue
                if _key_looks_like_url(key_l) and isinstance(v, str):
                    # Never retain path segments that embed tender/OCID codes.
                    out[key] = "<redacted_url>"
                    continue
                if key_l in _ORG_NAME_KEYS and isinstance(v, str):
                    out[key] = f"Synthetic Org {map_id('org', v)[-6:].upper()}"
                    continue
                if key_l in _PERSON_NAME_KEYS and isinstance(v, str):
                    out[key] = f"Synthetic Label {map_id('org', v)[-6:].upper()}"
                    continue
                if key_l in _GEO_KEYS and isinstance(v, str):
                    out[key] = f"Synthetic Place {map_id('org', v)[-6:].upper()}"
                    continue
                if key_l in _ADDRESS_KEYS and isinstance(v, str):
                    out[key] = "Synthetic Address"
                    continue
                if key_l in _EMAIL_KEYS and isinstance(v, str):
                    out[key] = "synthetic@example.invalid"
                    continue
                if key_l in _PHONE_KEYS and isinstance(v, str):
                    out[key] = "+56-9-0000-0000"
                    continue
                if key_l in _RUT_KEYS and isinstance(v, str):
                    out[key] = "00.000.000-0"
                    continue
                if key_l in _ORG_ID_KEYS and v is not None and str(v).strip():
                    out[key] = f"org-{map_id('orgid', str(v))[-8:]}"
                    continue
                if key_l == "id" and isinstance(v, str) and v.strip():
                    if len(v) > 8 or "ocds" in v.casefold() or "-" in v:
                        out[key] = map_id("id", v)
                        continue
                if key_l in {"fechacreacion", "fecha", "fechas"} or "fecha" in key_l:
                    if isinstance(v, str):
                        out[key] = _shift_timestamp(v, seed) if _ISO_TS_RE.match(v.strip()) else _scrub_string(v, seed=seed)
                        continue
                    out[key] = walk(v, child_path)
                    continue
                out[key] = walk(v, child_path)
            return out
        if isinstance(node, list):
            return [walk(v, path + (f"[{i}]",)) for i, v in enumerate(node)]
        if isinstance(node, str):
            return _scrub_string(node, seed=seed)
        return copy.deepcopy(node)

    sanitized = walk(payload)
    if not isinstance(sanitized, dict):
        raise TypeError("sanitize_live_payload expects a JSON object root")
    meta = {
        "sanitizer_version": SANITIZER_VERSION,
        "source_kind": source_kind,
        "sanitized_payload_digest": canonical_json_digest(sanitized),
    }
    sanitized = {**sanitized, "_sanitizer_meta": meta}
    return sanitized


def strip_sanitizer_meta(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k != "_sanitizer_meta"}


def collect_leak_candidates(payload: Any) -> list[str]:
    """Flatten string values for leak scanning."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            found.append(node)
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            found.append(str(node))

    walk(payload)
    return found


def assert_no_identifier_leaks(
    sanitized: dict[str, Any],
    *,
    forbidden_substrings: list[str],
) -> None:
    blob = "\n".join(collect_leak_candidates(sanitized)).casefold()
    for item in forbidden_substrings:
        token = (item or "").strip()
        if len(token) < 4:
            # Avoid false positives on tiny status vocabulary / ordinals.
            continue
        if token.casefold() in blob:
            # Never include the offending value, prefix/suffix, length, or hash.
            raise AssertionError("identifier_leak_detected")
