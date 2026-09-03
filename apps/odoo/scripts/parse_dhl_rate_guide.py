"""Parse the DHL Express Chile 2026 rate guide text into structured JSON."""
import json
import re

TXT = "dhl_cl_2026.txt"
lines = open(TXT, encoding="utf-8").read().splitlines()


def num(tok: str) -> float:
    return float(tok.replace(",", ""))


# --- zones: "País  Zona" pairs, two columns per printed line ------------------
zones = {}
zone_re = re.compile(r"([A-Za-zÁÉÍÓÚÜÑáéíóúüñ'’\.\,\-\(\) ]{3,45}?)\s{2,}([1-6])(?:\s|$)")
in_zone_section = False
for line in lines:
    if "ZONAS Y OPCIONES DE SERVICIO" in line:
        in_zone_section = True
        continue
    if in_zone_section and ("Tarifas de" in line or "TARIFAS DE" in line):
        in_zone_section = False
    if not in_zone_section:
        continue
    for country, zone in zone_re.findall(line):
        name = country.strip()
        if not name or name.lower().startswith(("país", "peso", "zona")):
            continue
        if name in ("DHL Express", "DHL"):
            continue
        zones[name] = int(zone)

# --- import rates: DHL EXPRESS WORLDWIDE IMPORT ------------------------------
rate_rows = {}
increments = {}
section = None
capture = False
for i, line in enumerate(lines):
    if "DHL EXPRESS WORLDWIDE IMPORT" in line:
        capture = True
        continue
    if capture and "DHL EXPRESS DOMÉSTICO" in line:
        capture = False
    if not capture:
        continue
    if "Documentos hasta 2kg" in line:
        section = "documents"
        continue
    if "no documentos desde" in line:
        section = "non_documents"
        continue
    if "Por cada 0.5kg adicional" in line:
        section = "inc_half"
        continue
    if "Por cada 1kg adicional" in line:
        section = "inc_one"
        continue

    m = re.match(r"^\s*([\d.]+)\s+((?:[\d,]+\.\d{2}\s+){5}[\d,]+\.\d{2})\s*$", line)
    if m and section in ("documents", "non_documents"):
        weight = float(m.group(1))
        prices = [num(x) for x in m.group(2).split()]
        rate_rows.setdefault(section, {})[weight] = prices
        continue

    m = re.match(r"^\s*De\s+([\d.,]+)\s+a\s+([\d.,]+)\*?\s+((?:[\d,]+\.\d{2}\s+){5}[\d,]+\.\d{2})\s*$", line)
    if m and section in ("inc_half", "inc_one"):
        increments[f"{num(m.group(1))}-{num(m.group(2))}"] = {
            "step_kg": 0.5 if section == "inc_half" else 1.0,
            "per_step_usd": [num(x) for x in m.group(3).split()],
        }

out = {
    "source": "DHL Express Chile — Guía de Servicios y Tarifas 2026",
    "source_url": (
        "https://mydhl.express.dhl/content/dam/downloads/cl/es/rate-guide/"
        "service_and_rate_guide_cl_es_2026.pdf.coredownload.pdf"
    ),
    "currency": "USD",
    "product": "DHL Express Worldwide Import",
    "excludes": [
        "impuestos y aranceles",
        "costo del despacho de aduanas",
        "IVA",
        "cargos adicionales",
    ],
    "volumetric_divisor": 5000,
    "rounding": "0.5 kg hasta 30 kg; 1 kg sobre 30 kg (cada pieza al 0.5 kg)",
    "max_shipment_kg": 3000,
    "max_piece_kg": 1000,
    "max_piece_length_cm": 300,
    "zones_by_country": dict(sorted(zones.items())),
    "rates_by_weight": {
        k: {str(w): v for w, v in sorted(rows.items())} for k, rows in rate_rows.items()
    },
    "increments_over_10kg": increments,
    "surcharges_usd": {
        "palet_no_apilable": 340.00,
        "pieza_con_sobrepeso": 126.00,
        "pieza_excedida_de_tamano": 23.00,
        "pieza_procesamiento_manual": 23.00,
        "mercancias_peligrosas_cantidades_exentas": 17.50,
        "hielo_seco_un1845": 26.50,
        "lithium_ion_pi966_seccion_ii": 10.00,
        "lithium_metal_pi969_seccion_ii": 11.00,
        "mercancias_peligrosas_completamente_reguladas": 133.00,
        "articulos_de_consumo_id8000": 32.00,
        "cantidades_limitadas": 42.00,
        "correccion_de_direccion": 22.00,
        "guias_manuales": 12.50,
        "seguridad_riesgo_elevado": 42.00,
        "seguridad_destino_restringido": 53.00,
        "aduana_liberacion_formal": 185.00,
        "aduana_proceso_de_liberacion": 34.00,
        "entrega_areas_remotas_per_kg": 0.80,
        "entrega_areas_remotas_min": 40.00,
        "recoleccion_areas_remotas_per_kg": 0.70,
        "recoleccion_areas_remotas_min": 40.00,
    },
    "fuel_surcharge_pct": None,
    "fuel_surcharge_note": (
        "Mensual, fijado por DHL sobre transporte y cargos adicionales. "
        "No viene en la guía: hay que cargarlo cada mes. Pesa del orden de 20-30%."
    ),
}

json.dump(out, open("dhl_cl_2026_rates.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print("ZONES:", len(out["zones_by_country"]))
for c in ("España", "Alemania", "Italia", "Estados Unidos de América", "China, República Popular de"):
    print(f"  {c}: zona {out['zones_by_country'].get(c)}")
print("DOC ROWS:", len(out["rates_by_weight"].get("documents", {})))
print("NON-DOC ROWS:", len(out["rates_by_weight"].get("non_documents", {})))
print("INCREMENTS:", list(out["increments_over_10kg"]))
nd = out["rates_by_weight"]["non_documents"]
print("SPOT CHECK 30kg:", nd.get("30.0"))
print("SPOT CHECK 70kg:", nd.get("70.0"))
