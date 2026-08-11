"""Cálculo del flete DHL Express Import desde la guía de tarifas de Chile.

Funciones puras, sin ORM, para poder verificarlas contra la tabla impresa.
El algoritmo de tramos reproduce **exactamente** las 144 filas publicadas
(6 zonas × 24 pesos sobre 10 kg); si algún día no lo hace, es que cambió la
guía y hay que reparsearla con `scripts/parse_dhl_rate_guide.py`.
"""

import json
import math
import unicodedata
from functools import lru_cache

from odoo.tools import file_open

RATES_PATH = "origenlab_commercial/data/dhl_rates_cl_2026.json"
EPS = 1e-6

# Los reactivos entran por aquí. Cuál aplica lo decide el proveedor al declarar
# la mercancía, no nosotros: entre "exentas" y "completamente reguladas" hay 7×.
DANGEROUS_GOODS = [
    ("none", "Sin mercancías peligrosas"),
    ("exempt", "Peligrosas en cantidades exentas"),
    ("limited", "Cantidades limitadas"),
    ("id8000", "Artículos de consumo ID8000"),
    ("dry_ice", "Hielo seco UN1845"),
    ("fully_regulated", "Peligrosas completamente reguladas"),
]
DANGEROUS_SURCHARGE_KEY = {
    "exempt": "mercancias_peligrosas_cantidades_exentas",
    "limited": "cantidades_limitadas",
    "id8000": "articulos_de_consumo_id8000",
    "dry_ice": "hielo_seco_un1845",
    "fully_regulated": "mercancias_peligrosas_completamente_reguladas",
}


@lru_cache(maxsize=1)
def load_rates():
    with file_open(RATES_PATH, "r") as handle:
        return json.load(handle)


@lru_cache(maxsize=1)
def _weight_rows():
    rates = load_rates()
    rows = {float(k): v for k, v in rates["rates_by_weight"]["non_documents"].items()}
    return rows, sorted(rows)


@lru_cache(maxsize=1)
def _bands():
    bands = []
    for key, band in load_rates()["increments_over_10kg"].items():
        low, high = (float(part) for part in key.split("-"))
        bands.append((low, high, band["step_kg"], band["per_step_usd"]))
    return sorted(bands)


def _normalize(text):
    stripped = unicodedata.normalize("NFKD", (text or "").strip().lower())
    return "".join(char for char in stripped if not unicodedata.combining(char))


@lru_cache(maxsize=1)
def _zones_normalized():
    return {
        _normalize(country): zone
        for country, zone in load_rates()["zones_by_country"].items()
    }


def zone_for_country(name):
    """Zona DHL a partir del nombre del país. None si no se reconoce."""
    return _zones_normalized().get(_normalize(name))


def volumetric_weight(length_cm, width_cm, height_cm, pieces=1):
    """Peso volumétrico IATA: largo × ancho × alto / 5000, por pieza."""
    divisor = load_rates()["volumetric_divisor"]
    per_piece = (length_cm or 0) * (width_cm or 0) * (height_cm or 0) / divisor
    return per_piece * max(pieces or 1, 1)


def chargeable_weight(actual_kg, volumetric_kg):
    """Se cobra el mayor de los dos, redondeado hacia arriba.

    La guía dice "al 0.5 kg más cercano hasta 30 kg y al kilo después". Aquí se
    redondea **hacia arriba**, que es lo que DHL hace en la práctica y lo
    prudente para un estimado: quedarse corto en el flete se come el margen en
    silencio, que es justo lo que este sistema existe para evitar.
    """
    weight = max(actual_kg or 0.0, volumetric_kg or 0.0)
    if weight <= 0:
        return 0.0
    step = 0.5 if weight <= 30 else 1.0
    return math.ceil(weight / step - EPS) * step


def _band_at(weight):
    """Primer tramo cuyo techo supera el peso.

    Los tramos se publican como 10.1-20, 20.1-30… así que un peso de
    exactamente 10.0 no cae en ninguno: corresponde el siguiente, no el último.
    """
    for _low, high, step, per_step in _bands():
        if high > weight + EPS:
            return high, step, per_step
    last = _bands()[-1]
    return last[1], last[2], last[3]


def base_rate_usd(weight_kg, zone):
    """Tarifa base del tramo, en USD, para una zona 1-6."""
    if not 1 <= zone <= 6:
        raise ValueError("La zona DHL debe estar entre 1 y 6")
    rows, weights = _weight_rows()
    index = zone - 1
    if weight_kg <= weights[0]:
        return rows[weights[0]][index]
    if weight_kg in rows:
        return rows[weight_kg][index]

    lower = max(w for w in weights if w <= weight_kg)
    total = rows[lower][index]
    current = lower
    while weight_kg - current > EPS:
        band_high, step, per_step = _band_at(current + EPS)
        chunk = min(weight_kg - current, band_high - current)
        if chunk <= EPS:  # red de seguridad: nunca quedarse sin avanzar
            break
        total += math.ceil(chunk / step - EPS) * per_step[index]
        current += chunk
    return total


def quote_freight(
    *,
    zone,
    actual_kg,
    length_cm=0.0,
    width_cm=0.0,
    height_cm=0.0,
    pieces=1,
    dangerous="none",
    remote_delivery=False,
    formal_clearance=False,
    fuel_pct=0.0,
):
    """Estimar el flete DHL Express Import. Devuelve el desglose en USD."""
    rates = load_rates()
    surcharges = rates["surcharges_usd"]

    volumetric = volumetric_weight(length_cm, width_cm, height_cm, pieces)
    chargeable = chargeable_weight(actual_kg, volumetric)
    base = base_rate_usd(chargeable, zone) if chargeable else 0.0

    extras = []
    key = DANGEROUS_SURCHARGE_KEY.get(dangerous)
    if key:
        extras.append((dict(DANGEROUS_GOODS)[dangerous], surcharges[key]))

    # Por pieza, según peso y dimensiones declaradas.
    per_piece = max(pieces or 1, 1)
    piece_weight = (actual_kg or 0.0) / per_piece
    piece_volumetric = volumetric / per_piece if per_piece else 0.0
    if max(piece_weight, piece_volumetric) > 70:
        extras.append(("Pieza con sobrepeso", surcharges["pieza_con_sobrepeso"] * per_piece))
    dims = sorted([length_cm or 0, width_cm or 0, height_cm or 0], reverse=True)
    if dims[0] > 100 or dims[1] > 80:
        extras.append(
            ("Pieza excedida de tamaño", surcharges["pieza_excedida_de_tamano"] * per_piece)
        )

    if remote_delivery:
        remote = max(
            surcharges["entrega_areas_remotas_per_kg"] * chargeable,
            surcharges["entrega_areas_remotas_min"],
        )
        extras.append(("Entrega en área remota", remote))
    if formal_clearance:
        extras.append(("Liberación formal de aduana", surcharges["aduana_liberacion_formal"]))

    extras_total = sum(amount for _label, amount in extras)
    # El recargo por combustible se aplica sobre transporte **y** recargos.
    fuel = (base + extras_total) * (fuel_pct or 0.0) / 100.0

    return {
        "volumetric_kg": volumetric,
        "chargeable_kg": chargeable,
        "base_usd": base,
        "extras": extras,
        "extras_usd": extras_total,
        "fuel_usd": fuel,
        "total_usd": base + extras_total + fuel,
        "excludes": rates["excludes"],
    }
