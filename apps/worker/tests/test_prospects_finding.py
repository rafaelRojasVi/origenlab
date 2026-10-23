"""The prospect finding contract (`origenlab_worker.prospects.finding`): what a loader line must
carry before it may become evidence. Every value is invented."""

from __future__ import annotations

import pytest

from origenlab_worker.prospects.finding import FindingRefused, dedupe_key, parse_finding


def _raw(**over):
    base = {
        "source": "fondequip", "external_id": "EQM-0000", "person_name": "Ana Prueba",
        "organization_name": "Instituto Ficticio", "why_now": "Ganó un fondo de equipamiento",
        "email": "ana@example.test", "email_source_url": "https://example.test/paper",
        "equipment": ["Centrífuga"], "sources": [["fondo", "https://example.test/fondo"]],
        "confidence": "alta", "score": 80,
    }
    base.update(over)
    return base


def test_parse_finding_normalises_email():
    f = parse_finding(_raw(email=" MAILTO:Ana@Example.TEST "))
    assert f.email == "ana@example.test"


def test_parse_refuses_email_without_source():
    with pytest.raises(FindingRefused) as exc:
        parse_finding(_raw(email_source_url=None))
    assert exc.value.reason == "email_without_source"


def test_parse_refuses_non_linkedin_url_in_linkedin_field():
    with pytest.raises(FindingRefused) as exc:
        parse_finding(_raw(linkedin_person="https://example.test/in/ana"))
    assert exc.value.reason == "linkedin_not_linkedin"


def test_parse_refuses_non_http_link():
    with pytest.raises(FindingRefused) as exc:
        parse_finding(_raw(website="javascript:alert(1)"))
    assert exc.value.reason == "link_not_http"


def test_parse_refuses_unknown_source():
    with pytest.raises(FindingRefused) as exc:
        parse_finding(_raw(source="linkedin_scrape"))
    assert exc.value.reason == "unknown_source"


def test_parse_keeps_links_and_score():
    f = parse_finding(_raw(website="https://example.test", linkedin_org="https://www.linkedin.com/company/x",
                           score=120))
    assert f.website == "https://example.test"
    assert f.linkedin_org == "https://www.linkedin.com/company/x"
    assert f.score == 100  # clamped to 0–100
    assert f.equipment == ("Centrífuga",)
    assert f.sources == (("fondo", "https://example.test/fondo"),)


def test_dedupe_key_folds_accents():
    a = parse_finding(_raw(person_name=None, organization_name="Universidad de Concepción"))
    b = parse_finding(_raw(person_name=None, organization_name="universidad  de concepcion"))
    assert dedupe_key(a) == dedupe_key(b) == "prospect:fondequip:EQM-0000:universidad de concepcion"


def test_dedupe_key_prefers_person():
    assert dedupe_key(parse_finding(_raw())) == "prospect:fondequip:EQM-0000:ana prueba"
