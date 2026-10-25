"""One prospect finding: the contract a loader line must meet before it may become evidence.

Never-guessed contact data is the rule here: an email without the page or paper that published it
is refused, as is a LinkedIn field that does not point at linkedin.com (it may only come from an
ordinary search result, never from scraping)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal
from urllib.parse import urlparse

Source = Literal["fondequip", "fondecyt", "fondef", "industria_sag", "industria_inn", "manual"]
SOURCES: frozenset[str] = frozenset({"fondequip", "fondecyt", "fondef", "industria_sag", "industria_inn", "manual"})
SALUTATIONS: frozenset[str] = frozenset({"HOMBRE", "MUJER", ""})
CONFIDENCES: frozenset[str] = frozenset({"alta", "media", "baja"})
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_LINK_FIELDS = ("email_source_url", "website", "linkedin_person", "linkedin_org", "orcid", "scholar")


class FindingRefused(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ProspectFinding:
    source: Source
    external_id: str
    person_name: str | None
    role: str | None
    organization_name: str
    unit: str | None
    email: str | None
    email_source_url: str | None
    email_source_year: int | None
    phone: str | None
    website: str | None
    linkedin_person: str | None
    linkedin_org: str | None
    orcid: str | None
    scholar: str | None
    fund_label: str | None
    project_title: str | None
    amount_label: str | None
    awarded_on: date | None
    why_now: str
    equipment: tuple[str, ...]
    sources: tuple[tuple[str, str], ...]
    confidence: Literal["alta", "media", "baja"]
    score: int
    # Optional ANID-specific fields (present only for daily-search findings)
    salutation_sex: str | None = None       # HOMBRE / MUJER from ANID SEXO column
    program: str | None = None             # PROGRAMA (fondecyt/fondef/fondequip)
    keywords: tuple[str, ...] = ()          # PALABRAS_CLAVES (|'-separated)
    hidden_reason: str | None = None        # 'no_calza' when fit < threshold


def _text(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    value = re.sub(r"\s+", " ", str(value)).strip()
    return value or None


def _is_http(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _email(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip().lower()
    if value.startswith("mailto:"):
        value = value[len("mailto:"):].strip()
    if not _EMAIL.fullmatch(value):
        raise FindingRefused("email_malformed")
    return value


def parse_finding(raw: dict[str, Any]) -> ProspectFinding:
    source = _text(raw, "source")
    if source not in SOURCES:
        raise FindingRefused("unknown_source")
    external_id = _text(raw, "external_id")
    organization_name = _text(raw, "organization_name")
    why_now = _text(raw, "why_now")
    if not external_id or not organization_name or not why_now:
        raise FindingRefused("missing_required_field")
    links = {key: _text(raw, key) for key in _LINK_FIELDS}
    for value in links.values():
        if value is not None and not _is_http(value):
            raise FindingRefused("link_not_http")
    for key in ("linkedin_person", "linkedin_org"):
        host = urlparse(links[key]).hostname if links[key] else None
        if host is not None and not (host == "linkedin.com" or host.endswith(".linkedin.com")):
            raise FindingRefused("linkedin_not_linkedin")
    email = _email(_text(raw, "email"))
    if email is not None and links["email_source_url"] is None:
        raise FindingRefused("email_without_source")
    confidence = _text(raw, "confidence") or "media"
    if confidence not in CONFIDENCES:
        raise FindingRefused("unknown_confidence")
    sources = tuple((str(fact), str(url)) for fact, url in raw.get("sources") or ())
    if any(not _is_http(url) for _, url in sources):
        raise FindingRefused("link_not_http")
    awarded = _text(raw, "awarded_on")
    year = raw.get("email_source_year")
    salutation = (_text(raw, "salutation_sex") or "").upper()
    if salutation and salutation not in SALUTATIONS:
        salutation = None  # ignore unknown values
    hidden_reason = _text(raw, "hidden_reason")
    return ProspectFinding(
        source=source, external_id=external_id, person_name=_text(raw, "person_name"), role=_text(raw, "role"),
        organization_name=organization_name, unit=_text(raw, "unit"), email=email,
        email_source_url=links["email_source_url"], email_source_year=int(year) if year else None,
        phone=_text(raw, "phone"), website=links["website"], linkedin_person=links["linkedin_person"],
        linkedin_org=links["linkedin_org"], orcid=links["orcid"], scholar=links["scholar"],
        fund_label=_text(raw, "fund_label"), project_title=_text(raw, "project_title"),
        amount_label=_text(raw, "amount_label"), awarded_on=date.fromisoformat(awarded) if awarded else None,
        why_now=why_now, equipment=tuple(str(e).strip() for e in raw.get("equipment") or () if str(e).strip()),
        sources=sources, confidence=confidence, score=max(0, min(int(raw.get("score") or 0), 100)),
        salutation_sex=salutation or None,
        program=_text(raw, "program"),
        keywords=tuple(str(k).strip() for k in raw.get("keywords") or () if str(k).strip()),
        hidden_reason=hidden_reason,
    )


def fold(text: str) -> str:
    """Lower case, accents stripped, whitespace collapsed: the identity part of a dedupe key."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", plain).strip().lower()


def dedupe_key(f: ProspectFinding) -> str:
    return f"prospect:{f.source}:{f.external_id}:{fold(f.person_name or f.organization_name)}"
