"""Europe PMC enrichment: find a researcher's published email from their papers.

Rule: accept an email ONLY when the author's initial + paternal surname match AND the affiliation
text mentions the institution (accent/case-folded, common short forms allowed) OR the email
domain belongs to a known institution domain for that institution. Otherwise no email — fail
closed, never guess.

Rate limit: ≤ 3 requests/s (100 ms pause between calls). Timeouts and HTTP errors yield no
email rather than an exception (polite fail).

All network calls go through the `session` argument so tests can inject a recorder.
"""

from __future__ import annotations

import re
import time
import unicodedata
from typing import Any

EPMC_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_REQUEST_PAUSE_S = 0.35   # ~3 req/s ceiling
_TIMEOUT_S = 15

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def _fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", plain).strip().lower()


def _institution_matches(aff_text: str, institution_name: str) -> bool:
    """True when the affiliation text mentions the institution — case/accent-folded, common
    short forms allowed (e.g. 'U. de Chile' for 'Universidad de Chile')."""
    aff = _fold(aff_text)
    inst = _fold(institution_name)
    if inst in aff:
        return True
    # Common abbreviated forms
    words = inst.split()
    if len(words) >= 3 and words[0] in ("universidad", "u"):
        # Try matching the distinctive words (drop 'de', 'del', etc.)
        content_words = [w for w in words if w not in ("de", "del", "la", "los", "las", "el", "y")]
        if len(content_words) >= 2 and all(w in aff for w in content_words):
            return True
    return False


def _email_domain_matches(email: str, institution_name: str, known_domain: str | None) -> bool:
    """True when the email's domain is the institution's known domain."""
    if not known_domain:
        return False
    email_domain = email.split("@")[-1].lower().strip()
    clean_known = known_domain.replace("https://www.", "").replace("http://www.", "").rstrip("/")
    return email_domain == clean_known


def _author_name_matches(author: dict[str, Any], variants: list[str]) -> bool:
    """True when the author's full name matches one of the ANID-derived variants."""
    full_name = _fold(author.get("fullName") or "")
    for variant in variants:
        if _fold(variant) == full_name:
            return True
        # partial: 'PATERNAL I' should match 'paternal i.' or 'paternal initial'
        v = _fold(variant)
        if v and full_name.startswith(v.rstrip(".")):
            return True
    return False


def search_epmc(session: Any, name_variants: list[str], institution: str,
                *, pause_s: float = _REQUEST_PAUSE_S) -> dict[str, Any] | None:
    """Search Europe PMC for the PI's most recent paper with an email. Returns a dict with
    email, doi, year, orcid, or None when no confident match is found."""
    if not name_variants:
        return None
    for variant in name_variants[:2]:  # try at most 2 variants
        time.sleep(pause_s)
        result = _one_search(session, variant, institution)
        if result:
            return result
    return None


def _one_search(session: Any, name_variant: str, institution: str) -> dict[str, Any] | None:
    query = f'AUTH:"{name_variant}" AND AFF:"Chile"'
    try:
        resp = session.get(
            EPMC_SEARCH_URL,
            params={"query": query, "resultType": "core", "format": "json",
                    "pageSize": "25", "sort": "P_PDATE_D desc"},
            timeout=_TIMEOUT_S,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception:  # noqa: BLE001 — network failures are silent
        return None

    articles = (data.get("resultList") or {}).get("result") or []
    for article in articles:
        authors = article.get("authorList", {}).get("author") or []
        pub_year = article.get("pubYear")
        doi = article.get("doi")
        pmcid = article.get("pmcid") or article.get("id")
        source_url = f"https://europepmc.org/article/MED/{pmcid}" if pmcid else None
        if doi:
            source_url = f"https://doi.org/{doi}"
        for author in authors:
            if not _author_name_matches(author, [name_variant]):
                continue
            # Extract email from affiliationList
            affs = author.get("affiliationList", {}).get("affiliation") or []
            for aff_text in affs:
                emails = _EMAIL_RE.findall(aff_text)
                for email in emails:
                    email_lc = email.lower()
                    # Validate: institution mentioned in affiliation text or known domain
                    if (_institution_matches(aff_text, institution)
                            or _email_domain_matches(email_lc, institution, None)):
                        orcid_id = None
                        for aid in (author.get("authorId") or []):
                            if isinstance(aid, dict) and aid.get("type") == "ORCID":
                                raw_orcid = aid.get("value") or ""
                                if raw_orcid:
                                    orcid_id = (f"https://orcid.org/{raw_orcid}"
                                                if not raw_orcid.startswith("http") else raw_orcid)
                        return {
                            "email": email_lc,
                            "source_url": source_url,
                            "source_year": int(pub_year) if pub_year else None,
                            "orcid": orcid_id,
                        }
    return None


__all__ = ["search_epmc"]
