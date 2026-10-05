"""scripts/build_org_suggestions.py — a research run's JSON in, the reviewed file out.

No network, no database; every institution, RUT, domain and URL is invented.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from origenlab_api.v2.org_web_suggestions import validate_file

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_org_suggestions.py"
_SPEC = importlib.util.spec_from_file_location("build_org_suggestions", _PATH)
assert _SPEC and _SPEC.loader
builder = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = builder
_SPEC.loader.exec_module(builder)

ORG_A = "0a000000-0000-4000-8000-00000000000a"
ORG_B = "0b000000-0000-4000-8000-00000000000b"


def _research(org_id: str, **over: Any) -> dict[str, Any]:
    return {
        "organization_id": org_id, "crm_name": "Ficticio", "display_name": "Instituto Ficticio",
        "legal_name": "None", "rut": "None", "website": "https://www.ficticio.example",
        "email_domain": "ficticio.example", "type": "laboratorio", "city": "None", "region": "None",
        "confidence": "high", "sources": [{"url": "https://www.ficticio.example/contacto", "shows": "dirección"}],
        "notes": "", **over,
    }


def test_none_strings_become_null_and_the_output_validates() -> None:
    data, counts = builder.build([_research(ORG_A)])
    entry = data["organizations"][ORG_A]
    assert [entry[k] for k in ("legal_name", "rut", "rut_source", "city", "notes")] == [None] * 5
    assert validate_file(data)[ORG_A]["display_name"] == "Instituto Ficticio"
    assert counts["organizations"] == 1


@pytest.mark.parametrize(
    ("source_url", "expected"),
    [
        ("https://www.ficticio.example/quienes-somos", "official"),
        ("https://ficticio.example/rut", "official"),
        ("https://www.superintendencia.gob.cl/registro", "official"),
        ("https://zeus.sii.cl/cvc/consulta", "official"),
        ("https://www.directorio-empresas.example/ficha", "directory"),
        ("https://ficticio.example.directorio.example/ficha", "directory"),
    ],
)
def test_a_rut_is_official_only_from_the_institution_itself_or_the_state(source_url: str, expected: str) -> None:
    research = _research(ORG_A, rut="12.345.678-5", sources=[{"url": source_url, "shows": "RUT 12.345.678-5"}])
    data, counts = builder.build([research])
    assert (data["organizations"][ORG_A]["rut"], data["organizations"][ORG_A]["rut_source"]) == ("12345678-5", expected)
    assert counts[f"rut_{expected}"] == 1


def test_a_rut_no_source_shows_counts_as_a_directory_rut() -> None:
    data, _ = builder.build([_research(ORG_A, rut="12.345.678-5")])
    assert data["organizations"][ORG_A]["rut_source"] == "directory"


def test_a_wrong_check_digit_or_a_bad_type_is_dropped_and_counted() -> None:
    data, counts = builder.build([_research(ORG_A, rut="12.345.678-9"), _research(ORG_B, type="Universidad Pública")])
    assert data["organizations"][ORG_A]["rut"] is None and counts["rut_dropped_check_digit"] == 1
    assert data["organizations"][ORG_B]["type"] is None and counts["type_dropped_shape"] == 1


def test_the_output_must_live_outside_the_repository(tmp_path: Path) -> None:
    research = tmp_path / "suggestions.json"
    research.write_text(json.dumps([_research(ORG_A)]), encoding="utf-8")
    inside = builder.REPO_ROOT / "apps" / "api" / "org-suggestions.json"
    assert builder.main(["--in", str(research), "--out", str(inside)]) == 2
    assert not inside.exists()
    out = tmp_path / "out" / "org-suggestions.json"
    assert builder.main(["--in", str(research), "--out", str(out)]) == 0
    assert ORG_A in json.loads(out.read_text(encoding="utf-8"))["organizations"]
    assert out.stat().st_mode & 0o077 == 0
