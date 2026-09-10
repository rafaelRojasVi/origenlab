# Lead pipeline scripts (v1)

File-based ingest and normalization for Chile external leads. Run from **repo root** with `uv run python scripts/leads/<script>.py` (or `scripts/leads/advanced/…` / `scripts/leads/campaigns/…` where noted).

## Layout

- **`scripts/leads/`** (Python files in this directory root): default operator and pipeline entrypoints — normalize, score, match, canonical outbound CLIs, weekly focus, operational stack drivers.
- **`advanced/`:** hunt merge/import, lead-account rollup, exploratory exports, deeper audits — **not** the default outbound surface; see [`advanced/README.md`](advanced/README.md).
- **`campaigns/`:** DR50 / ready-8 cohort automation and versioned payload JSON; see [`campaigns/README.md`](campaigns/README.md).

## Order of execution

**Preferred routine** — full order in [`run_leads_operational_stack.sh`](run_leads_operational_stack.sh): generate `run_id` → optional ingest → ensure schema → normalize → reconcile upstream → score → match → exports → weekly focus → client pack → publish gate → write run manifest.

```bash
bash scripts/leads/run_leads_operational_stack.sh --skip-fetch
```

- Does **not** build the business mart; run [`scripts/pipeline/run_aligned_stack.sh`](../pipeline/run_aligned_stack.sh) first when matches matter.
- **`--reconcile-dry-run`:** reconcile step only (no `--apply`); **all other steps still write** DB/reports.
- **`--skip-gate`:** no `publish_gate.py`; final output says the run is **not publish-safe by default** — run the gate before external handoff.
- **`--skip-focus`**, **`--skip-pack`**: skip weekly focus or client pack; see script `--help`.
- **Run manifest / `run_id`:** the stack sets `ORIGENLAB_LEADS_OPERATIONAL_RUN_ID` and writes `operational_stack_last_run.json` plus `operational_run_manifests/<run_id>.json` after publish gate (even if gate fails — manifest records `publish_gate.passed=false`). Pack `summary.json` includes `provenance.operational_run_id` and always `publish_gate_validated_this_artifact: false` (pack is emitted before the gate). See [`lead_provenance.py`](../../src/origenlab_email_pipeline/lead_provenance.py).

---

1. **Ensure schema** — `normalize_leads.py --ensure-schema-only` (creates lead tables if missing).
2. **Ingest** — `fetch_chilecompra.py --file <path>`, `fetch_inn_labs.py --file <path>`, `fetch_corfo_centers.py --file <path>` (each optional if you have no file).
3. **Normalize** — `normalize_leads.py` (raw → lead_master).
4. **Score** — `leads_score.py` (priority_score, priority_reason).
5. **Match** — `match_leads_to_mart.py` (lead_master vs organization_master → lead_matches_existing_orgs).
6. **Export** — `export_leads_csv.py --out <path>`.
7. **Shortlist (weekly)** — `export_leads_shortlist.py --out <path>` (high_fit/medium_fit prioritized).
8. **QA/inspection** — `advanced/inspect_leads_quality.py` (counts + top leads).
9. **Client review CSV** — `advanced/export_client_review_csv.py --out <path>` (includes archive comparison + existing contacts).
10. **Contact-hunt sheet (v1.2)** — `advanced/export_contact_hunt_sheet.py --out <path>` (estructura para hunting de contactos en español).
11. **Merge + import hunt CSV** — `advanced/merge_contact_hunt_enrichment.py` (Deep Research → mismo CSV), luego validar con `advanced/validate_contact_hunt_alignment.py` (misma población de `id_lead` que `leads_contact_hunt_current.csv`), después `advanced/import_contact_hunt_to_sqlite.py` (recomendado: `--require-aligned-with` apuntando al hunt actual).
12. **Weekly canonical focus (safe mode)** — `run_weekly_focus.py` (genera CSV operativo + resumen ES con clasificación USAR/REFERENCIA/NO OPERATIVO).
13. **Limpiar `active/` + deepsearch + CSV unificado** — `advanced/prepare_active_workspace.py` (legacy weekly lead focus; **plan-only by default**; **`--apply`** to archive/move or write `--deepsearch` / `--unified` outputs). Daily outbound `active/current/` prep: `scripts/qa/prepare_outbound_campaign_workspace.py`.
14. **Paquete cliente (HTML + MD + anexo)** — `uv run python scripts/reports/build_leads_client_pack.py` → `reports/out/client_pack_latest/`. Ver `docs/REPORTING.md`.
15. **DR50 ready-8 → hunt + top20 informe** — `campaigns/apply_ready8_contact_patch.py` (**plan-only by default**; pass **`--apply`** to update `leads_contact_hunt_current.csv`, write `leads_contact_hunt_current_ready8_patch.csv`, `leads_top20_for_client_report.csv`, `docs/generated/READY8_AND_TOP20_REPORTING_PLAN.md`). Not daily outbound / not send approval. Luego import + `advanced/audit_contact_readiness.py`.
16. **Reconciliación DR 50 filas (solo análisis)** — `campaigns/reconcile_deepresearch_50_with_current_cohort.py` → CSVs `leads_dr50_*` y `docs/generated/DEEP_RESEARCH_RECONCILIATION.md`.

Or run the full pipeline.

<a id="m-leads-dr50-payload"></a>
### DR50 payload (versionado, no hardcoded)

[`reconcile_deepresearch_50_with_current_cohort.py`](campaigns/reconcile_deepresearch_50_with_current_cohort.py) carga las filas de contacto del lote DR50 desde JSON versionado bajo [`scripts/leads/campaigns/data/`](campaigns/data/) (no están embebidas en el script):

| Archivo | Rol |
|---------|-----|
| [`campaigns/data/dr50_manifest_v1.json`](campaigns/data/dr50_manifest_v1.json) | Apunta al fichero payload, `row_count`, y `expected_sha256` sobre **bytes** del JSON |
| [`campaigns/data/dr50_payload_v1.json`](campaigns/data/dr50_payload_v1.json) | Array de objetos fila (p. ej. `id_lead`, contactos DR). **Contactos sintéticos** (`*.example.invalid`): este repositorio es público y no almacena datos de contacto reales. Los `id_lead`/`id_licitacion` sí son los originales. |

Carga verificada: [`origenlab_email_pipeline/dr50_payload_loader.py`](../../src/origenlab_email_pipeline/dr50_payload_loader.py) — rechaza checksum incorrecto, conteo distinto o `id_lead` duplicado. **Por qué:** reproducibilidad y trazabilidad cuando el CSV de DR cambia (actualizar payload + manifest; recalcular SHA256 del fichero tal cual en disco).

**Gate QA:** la capa operational trust no valida el payload DR50; solo documenta aquí el contrato de datos del script de reconciliación. Publicación / coherencia leads: [`docs/RUNBOOK.md`](../../docs/RUNBOOK.md#m-eprun-publish-qa). Sample CSVs in `samples/` let you run without real data:

```bash
export LEADS_CHILECOMPRA_FILE=scripts/leads/samples/chilecompra_sample.csv
export LEADS_INN_FILE=scripts/leads/samples/inn_labs_sample.csv
export LEADS_CORFO_FILE=scripts/leads/samples/corfo_centers_sample.csv
export LEADS_EXPORT_PATH=reports/out/leads_export.csv
bash scripts/leads/run_leads_pipeline.sh
```

If a `LEADS_*_FILE` path is set but the file does not exist, that source is skipped and a “file not found” message is printed (the pipeline continues). Use `--skip-fetch` to run normalize → score → match → export without re-reading input files.

## Lead source-key audit (read-only)

Uniqueness is enforced on `(source_name, canonical source_record_id)` (empty/whitespace → `''`). The audit reports **blank canonical IDs** (count + % per source), **duplicate key groups** per source, a **short-numeric-ID heuristic** (1–3 digits) to surface possible **ChileCompra row-index fallback** from `fetch_chilecompra.py`, **warnings** (non-fatal by default), and **small samples**. It does not modify the DB.

```bash
uv run python scripts/leads/audit_lead_master_duplicates.py
uv run python scripts/leads/audit_lead_master_duplicates.py --sample-limit 6
uv run python scripts/leads/audit_lead_master_duplicates.py --fail-on-duplicates   # exit 1 only if duplicate key groups exist
```

`--db` must point to an **existing** file (exit 2 if missing). The audit opens SQLite **read-only** and does not create parent directories.

See `docs/leads/LEAD_PIPELINE.md` (Lead identity) for why blank/unstable source IDs matter.

## Upstream reconciliation (stale leads / source shrink)

`external_leads_raw` is upsert-only; `normalize_leads.py` does not delete `lead_master` rows when upstream files shrink. **Soft retire** marks rows missing from the current raw snapshot as `upstream_sync_state = 'retired_no_raw'` (no hard delete). Operational exports, scoring, matching, client pack, `db_lead_totals`, and `v_lead_match_summary` **exclude** retired rows. The next `normalize_leads.py` run **reactivates** a row when its raw key exists again.

**Conservative rule:** if a `source_name` has **zero** rows in `external_leads_raw`, no `lead_master` rows for that source are retired (avoids mass retire when a fetch was skipped). Use `--sources a,b` to limit scope.

```bash
# Default: dry-run (prints candidates, no DB writes)
uv run python scripts/leads/reconcile_lead_upstream.py
uv run python scripts/leads/reconcile_lead_upstream.py --json-out /tmp/upstream_reconcile.json

# Apply soft retire + append lead_upstream_reconcile_log
uv run python scripts/leads/reconcile_lead_upstream.py --apply
uv run python scripts/leads/reconcile_lead_upstream.py --apply --sources chilecompra,inn_labs
```

See `docs/leads/LEAD_PIPELINE.md` (Upstream lifecycle).

## Shortlist + QA

```bash
# Weekly shortlist (exclude low_fit by default)
uv run python scripts/leads/export_leads_shortlist.py --out reports/out/leads_shortlist.csv --limit 200

# Include low_fit (if you want to browse everything, still ordered)
uv run python scripts/leads/export_leads_shortlist.py --out reports/out/leads_shortlist_all.csv --include-low --limit 500

# QA counts + top leads
uv run python scripts/leads/advanced/inspect_leads_quality.py --top 20

# Client-friendly review file (best for emailing/sharing)
uv run python scripts/leads/advanced/export_client_review_csv.py --out reports/out/leads_client_review.csv --limit 250

# Contact-hunt sheet (v1.2), Spanish headers for manual/semi-assisted enrichment
uv run python scripts/leads/advanced/export_contact_hunt_sheet.py --out reports/out/leads_contact_hunt_es.csv --limit 200
```

## Local Web UI (CSV download) — advanced / parked

**Parked advanced helper** — not daily outbound, not send approval, not `apps/api`. Binds to **127.0.0.1** by default (localhost only). Pass **`--lan`** intentionally to expose on your WiFi/LAN.

1. Set a password (required — no default):
```bash
export LEADS_WEB_USER="cliente"
export LEADS_WEB_PASS="cambia-este-password"
uv run python scripts/leads/advanced/run_contact_hunt_web_server.py --port 8000
```

2. Open in your browser: `http://127.0.0.1:8000/` (localhost default).

3. For LAN demos (other devices on same WiFi), add **`--lan`** and use your machine's IP:
```bash
export LEADS_WEB_PASS="cambia-este-password"
uv run python scripts/leads/advanced/run_contact_hunt_web_server.py --lan --port 8000
# then http://<tu-ip-de-red>:8000/
```

4. Login with the same username/password. Never commit or log the password.


## Expected CSV formats

- **ChileCompra:** CSV or JSON. Columns often include: id/codigo, titulo/title, comprador/buyer, url/link, description, region, contacto_email, telefono.
- **INN labs:** CSV with columns such as: nombre, lab_name, area, region, ciudad, sitio, website, email, contacto, telefono, id, codigo.
- **CORFO centers:** CSV with columns such as: centro, nombre_centro, organizacion, region, ciudad, sitio, website, email, contacto, director, telefono, area, lineas, id, codigo.

See [docs/leads/LEAD_PIPELINE.md](../../docs/leads/LEAD_PIPELINE.md) for full column lists and [docs/leads/CHILE_LEAD_SOURCES.md](../../docs/leads/CHILE_LEAD_SOURCES.md) for source URLs.

## SOP semanal corto (6 comandos)

```bash
# 1) Normaliza/actualiza leads en DB (si ya hiciste fetch, basta normalize)
uv run python scripts/leads/normalize_leads.py

# 2) Score
uv run python scripts/leads/leads_score.py

# 3) Match contra mart (para net-new vs ya conocido)
uv run python scripts/leads/match_leads_to_mart.py

# 4) Salidas core (truth semanal)
uv run python scripts/leads/export_leads_shortlist.py --out reports/out/leads_shortlist.csv --limit 200
uv run python scripts/leads/advanced/export_client_review_csv.py --out reports/out/leads_client_review.csv --limit 250

# 5) Hoja operativa de hunting (IDs actuales)
uv run python scripts/leads/advanced/export_contact_hunt_sheet.py --out reports/out/leads_contact_hunt_current.csv --limit 200

# 6) Resumen canónico semanal + CSV foco
uv run python scripts/leads/run_weekly_focus.py

# 7) (Opcional) Dejar active/ limpio + slice Deep Search + CSV unificado (ejecutar **después** del paso 6)
uv run python scripts/leads/advanced/prepare_active_workspace.py --deepsearch --unified --apply
```

Qué esperar:
- `reports/out/active/leads_weekly_focus.csv`: lista priorizada para ejecutar semana.
- `reports/out/active/leads_weekly_focus_summary_es.md`: métricas DB + alertas + clasificación de archivos.

**Convención mínima en `active/`:** usar solo `leads_shortlist_es.csv` y `leads_client_review_es.csv` (los `.csv` sin `_es` se archivan con `prepare_active_workspace.py`). Un solo hunt operativo: `leads_contact_hunt_current.csv` → merge → `leads_contact_hunt_current_merged.csv`. Para Deep Search: `leads_contact_hunt_for_deepsearch.csv`. Vista todo-en-uno: `leads_active_unified.csv` (`run_weekly_focus` + hunt por `id_lead`).
