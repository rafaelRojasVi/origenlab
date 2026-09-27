"""The equipment taxonomy is the website catalogue, and matching reports what it read.

Every value here is a catalogue name or an invented sentence; no real correspondence.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from origenlab_api.v2.equipment_taxonomy import load_taxonomy

REPO_ROOT = Path(__file__).resolve().parents[3]
SITE_PUBLIC = REPO_ROOT / "apps" / "web" / "public"
BRANDS = {"hielscher", "ortoalresa", "ika", "adam-equipment", "loeser", "serva"}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_json_is_what_the_website_catalogue_generates() -> None:
    """Drift guard: a brand, model or image changed on the site must be re-exported here."""
    result = subprocess.run(
        ["node", "apps/api/scripts/export_equipment_taxonomy.mjs", "--check"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_exactly_the_six_approved_brands_each_with_a_family_and_models() -> None:
    t = load_taxonomy()
    assert set(t.brands) == BRANDS
    for brand in t.brands.values():
        assert brand["family_id"] in t.families
        assert any(m["brand_id"] == brand["id"] for m in t.models.values()), brand["id"]


def test_every_model_image_is_a_verified_file_the_site_serves() -> None:
    t = load_taxonomy()
    for model in t.models.values():
        image = model["image"]
        assert image is not None, model["id"]
        assert image["url"].startswith("https://origenlab.cl/products/"), image["url"]
        # No AVIF in email: most clients cannot render it.
        assert image["format"] in {"jpg", "jpeg", "png", "webp"}, image["url"]
        local = SITE_PUBLIC / image["url"].removeprefix("https://origenlab.cl/")
        assert local.is_file(), local


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Cotización sonicador UP200St", {("hielscher", "hielscher-up200st")}),
        ("dispersor T25 digital", {("ika", "ika-t25-digital")}),
        ("Osmómetro Löser", {("loeser", None)}),
        ("equipo Loeser", {("loeser", None)}),
        ("centrífuga Biocen 22R", {("ortoalresa", "ortoalresa-biocen-22-r")}),
        ("i Osmometer basic", {("loeser", "loeser-i-osmometer-basic")}),
        ("BlueVertical PRiME y HPE BlueHorizon", {("serva", "serva-bluevertical-prime"), ("serva", "serva-hpe-bluehorizon")}),
        ("Balanza Adam Equipment", {("adam-equipment", None)}),
    ],
)
def test_matching_names_the_catalogue_entry(text: str, expected: set) -> None:
    got = {(m.brand_id, m.model_id) for m in load_taxonomy().match(text)}
    assert got == expected


@pytest.mark.parametrize(
    "text",
    ["mecánica de fluidos", "Adam Smith escribió", "Consulta general de laboratorio", "", None],
)
def test_no_match_is_no_match(text) -> None:
    """A first name, a substring or a general word is not a brand."""
    assert load_taxonomy().match(text) == []


def test_a_match_carries_the_alias_and_the_text_it_read() -> None:
    [m] = load_taxonomy().match("Solicitud dispersor T-25 digital")
    assert m.term == "T 25 digital"
    assert m.text == "t-25 digital"
