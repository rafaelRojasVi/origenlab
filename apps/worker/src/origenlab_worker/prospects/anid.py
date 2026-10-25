"""ANID open-data drip: download, filter and rank grant winners as prospect candidates.

Source: github.com/ANID-GITHUB/Historico-de-Proyectos-Adjudicados / BDH_HISTORICA.csv
Licence: CC0. Downloaded once per run with a conditional-get against GitHub's ETag / Last-Modified.
The CSV has a UTF-8 BOM and CRLF line endings; the delimiter is sniffed from the header line.

Pool filter (2025 awards, matching programmes and disciplines) yields ~970 rows. The year is a
parameter (default = max AGNO_FALLO present) so next year's file works without a code change.

Ranking:
  programme: FONDEQUIP > FONDEF > FONDECYT REGULAR > FONDECYT INICIACION
  discipline bonus: lab-heavy keywords (food, microbio, chemistry, …) move a row up
  year: newer first (within same rank tier)

Dedupe against known findings: same external_id (CODIGO_PROYECTO) or same folded
person_name + institution among existing prospect_finding payloads, including lote 1 rows.

This module is pure (no DB calls, no network) except `download_csv`, which takes the requests
session as an argument so tests can inject a fake. No real name, project code or institution
from the CSV is used in tests — fixtures are entirely fictitious.
"""

from __future__ import annotations

import csv
import io
import unicodedata
from dataclasses import dataclass
from typing import Any

CSV_URL = "https://raw.githubusercontent.com/ANID-GITHUB/Historico-de-Proyectos-Adjudicados/master/BDH_HISTORICA.csv"
UA = "OrigenLab-Prospectos/1 (+https://origenlab.cl)"

# ── Pool filter ───────────────────────────────────────────────────────────────────────────────
_PROGRAMA_POOL = frozenset({"FONDECYT", "FONDEF", "FONDEQUIP"})
_INSTRUMENTO_EXCLUDE = frozenset({"POSTDOCTORADO"})
_AREA_POOL = frozenset({
    "CIENCIAS NATURALES",
    "INGENIERIA Y TECNOLOGIA",
    "CIENCIAS MEDICAS Y DE LA SALUD",
    "CIENCIAS AGRICOLAS",
})

# ── Programme rank (higher is better) ────────────────────────────────────────────────────────
_PROGRAMME_RANK: dict[tuple[str, str], int] = {
    ("FONDEQUIP", ""): 4,       # any FONDEQUIP
    ("FONDEF", ""): 3,          # any FONDEF
    ("FONDECYT", "REGULAR"): 2,
    ("FONDECYT", "INICIACION"): 1,
}


def _prog_rank(programa: str, instrumento: str) -> int:
    if programa == "FONDEQUIP":
        return 4
    if programa == "FONDEF":
        return 3
    if programa == "FONDECYT" and "REGULAR" in instrumento.upper():
        return 2
    if programa == "FONDECYT" and "INICIACION" in instrumento.upper():
        return 1
    return 0


# ── Lab-keyword bonus ─────────────────────────────────────────────────────────────────────────
_LAB_KEYWORDS = (
    "aliment", "microbiolog", "quimic", "bioquim", "biotecnolog",
    "agua", "suelo", "acuicultura", "salmon", "vino", "enolog",
    "farmac", "clinic", "materiales", "nanopart",
    "sonic", "ultrason", "cromatograf",
)


def _lab_bonus(row: dict[str, str]) -> bool:
    text = " ".join([
        row.get("DISCIPLINA_DETALLE", ""),
        row.get("AREA_OCDE", ""),
        row.get("PALABRAS_CLAVES", ""),
        row.get("NOMBRE_PROYECTO", ""),
    ]).lower()
    return any(kw in text for kw in _LAB_KEYWORDS)


# ── Name helpers ──────────────────────────────────────────────────────────────────────────────
def _fold(text: str) -> str:
    """Lower-case, accent-stripped, whitespace-collapsed."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    import re
    return re.sub(r"\s+", " ", plain).strip().lower()


def _title_case(name: str) -> str:
    """Sensible title-case for a Chilean name in ALL CAPS (de/del/la/las/los stay lower)."""
    _LOWER = frozenset({"de", "del", "la", "las", "los", "el", "y"})
    words = name.strip().title().split()
    return " ".join(w if w.lower() not in _LOWER or i == 0 else w.lower()
                    for i, w in enumerate(words))


def _name_variants(nombre: str) -> list[str]:
    """Try 'PATERNAL-MATERNAL I' and 'PATERNAL I' from 'FIRST [SECOND] PATERNAL MATERNAL'."""
    parts = nombre.strip().split()
    if len(parts) < 3:
        return [nombre.strip()] if nombre.strip() else []
    first_initial = parts[0][0].upper()
    # ANID order: FIRST [SECOND] PATERNAL MATERNAL → last two are paternal, maternal
    if len(parts) == 3:
        # FIRST PATERNAL MATERNAL
        paternal, maternal = parts[1], parts[2]
    elif len(parts) == 4:
        # FIRST SECOND PATERNAL MATERNAL
        paternal, maternal = parts[2], parts[3]
    else:
        # 5+ parts: take second-to-last and last as paternal/maternal
        paternal, maternal = parts[-2], parts[-1]
    return [f"{paternal}-{maternal} {first_initial}", f"{paternal} {first_initial}"]


# ── Institution domain table (fixed, only domains we are sure of) ─────────────────────────────
_INSTITUTION_DOMAINS: dict[str, str] = {
    "universidad de chile": "uchile.cl",
    "u. de chile": "uchile.cl",
    "pontificia universidad catolica de chile": "uc.cl",
    "puc": "uc.cl",
    "universidad de concepcion": "udec.cl",
    "universidad tecnica federico santa maria": "usm.cl",
    "utfsm": "usm.cl",
    "universidad de santiago de chile": "usach.cl",
    "usach": "usach.cl",
    "universidad austral de chile": "uach.cl",
    "uach": "uach.cl",
    "pontificia universidad catolica de valparaiso": "pucv.cl",
    "pucv": "pucv.cl",
    "universidad de valparaiso": "uv.cl",
    "universidad de antofagasta": "uantof.cl",
    "universidad de la frontera": "ufro.cl",
    "universidad de talca": "utalca.cl",
    "universidad de la serena": "userena.cl",
    "universidad de atacama": "uda.cl",
    "universidad del bio-bio": "ubiobio.cl",
    "universidad de los lagos": "ulagos.cl",
    "universidad de magallanes": "umag.cl",
    "universidad de tarapaca": "uta.cl",
    "universidad arturo prat": "unap.cl",
    "universidad metropolitana de ciencias de la educacion": "umce.cl",
    "universidad tecnologica metropolitana": "utem.cl",
    "universidad de playa ancha": "upla.cl",
    "universidad de o'higgins": "uoh.cl",
}


def institution_website(institution_name: str) -> str | None:
    """A known institution's website from the fixed domain table; None when unknown."""
    key = _fold(institution_name)
    for k, domain in _INSTITUTION_DOMAINS.items():
        if k in key or key in k:
            return f"https://www.{domain}"
    return None


@dataclass
class AnidCandidate:
    codigo: str           # CODIGO_PROYECTO (external_id)
    programa: str         # FONDECYT / FONDEF / FONDEQUIP
    instrumento: str      # REGULAR / INICIACION / etc.
    agno_fallo: str       # e.g. '2025'
    nombre_proyecto: str
    nombre_responsable: str   # ALL CAPS, ANID format
    institucion: str
    area_ocde: str
    disciplina: str
    monto_m: str          # MONTO_ADJUDICADO in M$
    palabras_clave: list[str]
    sexo: str             # HOMBRE / MUJER
    # derived
    person_name: str      # title-cased
    fund_label: str       # e.g. «Fondecyt Regular 2025»
    amount_label: str     # e.g. «$ 180 M»
    website: str | None
    prog_rank: int
    lab_bonus: bool
    name_variants: list[str]  # for Europe PMC search


def _amount_label(monto_str: str) -> str:
    try:
        val = int(float(monto_str.replace(",", ".").strip() or "0"))
    except (ValueError, AttributeError):
        return ""
    return f"$ {val:,} M".replace(",", ".")


def _fund_label(programa: str, instrumento: str, agno: str) -> str:
    inst = instrumento.strip()
    if inst:
        return f"{programa.title()} {inst.title()} {agno}"
    return f"{programa.title()} {agno}"


def parse_csv(content: str, *, year: str | None = None) -> list[AnidCandidate]:
    """Parse the ANID CSV, filter to the pool and return candidates. `year` defaults to the
    maximum AGNO_FALLO present (so next year's file works without a code change)."""
    # Strip BOM if present
    if content.startswith("﻿"):
        content = content[1:]

    first_line = content.split("\n", 1)[0]
    dialect = csv.Sniffer().sniff(first_line, delimiters=";,\t")
    reader = csv.DictReader(io.StringIO(content), dialect=dialect)
    rows: list[dict[str, str]] = []
    for row in reader:
        # Normalise keys: strip whitespace
        rows.append({k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()})

    if not rows:
        return []

    if year is None:
        years = [r.get("AGNO_FALLO", "") for r in rows if r.get("AGNO_FALLO", "").isdigit()]
        if not years:
            return []
        year = max(years)

    candidates = []
    for row in rows:
        if row.get("AGNO_FALLO", "") != year:
            continue
        programa = row.get("PROGRAMA", "").strip().upper()
        instrumento = row.get("INSTRUMENTO", "").strip().upper()
        area = row.get("AREA_OCDE", "").strip().upper()
        if programa not in _PROGRAMA_POOL:
            continue
        if programa == "FONDECYT" and instrumento in _INSTRUMENTO_EXCLUDE:
            continue
        if area not in _AREA_POOL:
            continue
        codigo = row.get("CODIGO_PROYECTO", "").strip()
        nombre_resp = row.get("NOMBRE_RESPONSABLE", "").strip()
        institucion = row.get("INSTITUCION_PRINCIPAL", "").strip()
        if not codigo or not institucion:
            continue
        monto_str = row.get("MONTO_ADJUDICADO", "").strip()
        palabras_raw = row.get("PALABRAS_CLAVES", "").strip()
        palabras = [p.strip() for p in palabras_raw.split("|") if p.strip()] if palabras_raw else []
        rank = _prog_rank(programa, instrumento)
        bonus = _lab_bonus(row)
        candidates.append(AnidCandidate(
            codigo=codigo,
            programa=programa,
            instrumento=instrumento,
            agno_fallo=year,
            nombre_proyecto=row.get("NOMBRE_PROYECTO", "").strip(),
            nombre_responsable=nombre_resp,
            institucion=institucion,
            area_ocde=row.get("AREA_OCDE", "").strip(),
            disciplina=row.get("DISCIPLINA_DETALLE", "").strip(),
            monto_m=monto_str,
            palabras_clave=palabras,
            sexo=row.get("SEXO", "").strip().upper(),
            person_name=_title_case(nombre_resp) if nombre_resp else "",
            fund_label=_fund_label(programa, instrumento, year),
            amount_label=_amount_label(monto_str),
            website=institution_website(institucion),
            prog_rank=rank,
            lab_bonus=bonus,
            name_variants=_name_variants(nombre_resp) if nombre_resp else [],
        ))

    # Sort: programme rank desc, lab bonus first (within rank), then year desc (all same here)
    candidates.sort(key=lambda c: (-c.prog_rank, not c.lab_bonus))
    return candidates


def filter_new(candidates: list[AnidCandidate], existing_codes: frozenset[str],
               existing_person_keys: frozenset[str]) -> list[AnidCandidate]:
    """Drop candidates whose project code or folded (person_name, institution) is already known."""
    out = []
    for c in candidates:
        if c.codigo in existing_codes:
            continue
        person_key = f"{_fold(c.person_name)}|{_fold(c.institucion)}"
        if person_key in existing_person_keys:
            continue
        out.append(c)
    return out


def download_csv(session: Any, *, timeout: int = 30) -> str:
    """Download the ANID CSV and return its text content. `session` is a `requests.Session`."""
    headers = {"User-Agent": UA}
    resp = session.get(CSV_URL, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.text


__all__ = [
    "CSV_URL", "AnidCandidate", "download_csv", "filter_new", "institution_website",
    "parse_csv",
]
