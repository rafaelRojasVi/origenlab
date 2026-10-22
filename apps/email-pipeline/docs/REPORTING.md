# Reporting — correo (HTML/JSON) y paquete leads

Status: canonical  
Owner: email-pipeline-maintainers  
Last reviewed: 2026-03-24

Un solo lugar para **cómo generar informes** y **dónde quedan**. El alcance legal/comercial del informe de correo sigue en **[REPORT_SCOPE_CLIENT.md](REPORT_SCOPE_CLIENT.md)** ([`generate_client_report.py`](../scripts/reports/generate_client_report.py) lo copia tal cual a cada carpeta como `ALCANCE_INFORME.md`).

---

<a id="m-eprep-mail"></a>
## Informe del archivo de correo (HTML + JSON)

Cada ejecución genera una **carpeta con fecha** bajo `ORIGENLAB_REPORTS_DIR` (por defecto `~/data/origenlab-email/reports/` — ver [`DATA_LOCATIONS.md`](DATA_LOCATIONS.md#m-epdata-root)).

### Qué incluye

| Artefacto | Contenido |
|-----------|-----------|
| `index.html` | Dashboard con gráficos (año, clasificación, equipos) y tablas |
| `summary.json` | Mismos datos en JSON (para Excel / BI) |
| `clusters.json` | (opcional) clusters de una muestra con embeddings |

### Clasificaciones (heurística sobre asunto + cuerpo)

- **Cotización** — `cotiz`
- **Proveedor** — palabra proveedor
- **Factura / invoice**
- **Pedido / OC** — pedido, purchase order, orden de compra
- **Universidad** — universidad, uchile, puc, utfsm, udec, .edu., etc.
- **Rebote / NDR** — mailer-daemon, delivery status, etc. (para contextualizar volumen)

### Equipamiento (menciones)

Microscopio, centrífuga, espectrofotómetro, pHmetro, autoclave, balanza, HPLC/cromatografía, incubadora, titulador, liofilizador, horno/mufla, pipetas, medidor humedad granos.

### Dominios / “quién más escribe”

- **Dominios que más envían** — dominio del `From` (proveedores / newsletters que entran).
- **Dominios en Para/Cc** — con quién se corresponde más (clientes, universidades, etc.).

En bases **muy grandes**, un barrido completo de todos los `From` tarda mucho. Use muestreo.

**Alcance y límites (ventas vs menciones):** **[REPORT_SCOPE_CLIENT.md](REPORT_SCOPE_CLIENT.md)** — copia en cada carpeta de informe como `ALCANCE_INFORME.md`.

**GPU:** Solo la fase **embeddings** usa CUDA. El escaneo SQL y el muestreo de dominios son CPU/SQLite. Si pide `--embeddings-sample`, esa fase corre **antes** del barrido de dominios para que la GPU no quede ociosa detrás de 400k filas en Python. Si sale `cuda available: False`, use `uv sync --group ml` y el índice CUDA de PyTorch ([`README.md`](../README.md)); comprobar GPU con [`check_torch_cuda.py`](../scripts/tools/check_torch_cuda.py).

### Comandos (informe correo)

```bash
# Rápido: solo totales, año, clasificación y equipos (sin tablas de dominios)
uv run python scripts/reports/generate_client_report.py --fast --name resumen_2025

# Dominios sobre muestra de 400k mensajes (recomendado en DB grande)
uv run python scripts/reports/generate_client_report.py --domain-sample 400000 --name full_mar2025

# Todo el archivo para dominios (puede ser lento)
uv run python scripts/reports/generate_client_report.py --name dominios_completos

# + muestra embeddings (requiere uv sync --group ml)
uv run python scripts/reports/generate_client_report.py --domain-sample 300000 \
  --embeddings-sample 1200 --embeddings-clusters 12 --name con_clusters
```

### Embeddings aparte (más clusters / otro filtro)

```bash
RUN=~/data/origenlab-email/reports/20250314_120000_mi_run
uv run python scripts/ml/explore_email_clusters.py --limit 2000 --filter-any --n-clusters 14 --report-dir "$RUN"
# → escribe explore_clusters.json en esa carpeta
```

### Variable de entorno

`ORIGENLAB_REPORTS_DIR` — carpeta raíz de informes (compartir con el cliente vía zip o enlace).

---

<a id="m-eprep-leads"></a>
## Leads: `active/`, paquete cliente, comandos

### Fuente de verdad

- **SQLite** (`ORIGENLAB_SQLITE_PATH`, p. ej. `~/data/origenlab-email/sqlite/emails.sqlite`) es el registro maestro: `lead_master`, `external_leads_raw`, `lead_matches_existing_orgs`, `lead_outreach_enrichment`, y el mart de correo (`organization_master`, `contact_master`, …). Mapa DDL/propiedad: [`pipeline/SCHEMA_OWNERSHIP.md`](pipeline/SCHEMA_OWNERSHIP.md#m-schema-orchestrated) (p. ej. [capa Leads](pipeline/SCHEMA_OWNERSHIP.md#m-schema-leads), [Mart](pipeline/SCHEMA_OWNERSHIP.md#m-schema-mart)).
- Ningún CSV sustituye a la base; los CSV son **vistas exportadas** o **hojas de trabajo** que pueden quedar desalineadas si no se regeneran.

### Carpetas bajo `reports/out/`

| Ubicación | Propósito |
|-----------|-----------|
| **`reports/out/active/`** | Archivos operativos **mínimos**: foco semanal, resumen MD, hoja de hunting actual, opcional `for_deepsearch`. Al ejecutar [`prepare_active_workspace.py`](../scripts/leads/advanced/prepare_active_workspace.py), otros CSV en esta carpeta se mueven a `archive/`. |
| **`reports/out/client_pack_latest/`** | **Entregable cliente**: informe estático (HTML + MD + anexo CSV). Regenerar con [`build_leads_client_pack.py`](../scripts/reports/build_leads_client_pack.py); puede sobrescribirse en cada ejecución. `summary.json` incluye un bloque **`provenance`** (DB resuelta, revisión git opcional, registro opcional del último `run_leads_operational_stack.sh` en disco). |
| **`reports/out/archive/`** | Históricos, limpiezas, dumps grandes (`leads_export*.csv`, etc.). |
| **`reports/out/reference/`** | Experimentos y recortes (p. ej. Deep Research de prueba). |

### Hojas de cálculo vs informe principal

- El **informe principal** para el cliente debe ser el **paquete** (`index.html` + `resumen_ejecutivo_es.md`), no una carpeta de Excel sueltos.
- **`anexo_leads.csv`** es un anexo **técnico legible** con `id_lead` para trazabilidad con la base.
- La hoja **`leads_contact_hunt_current.csv`** es para **operaciones internas** (hunting); no es el entregable narrativo.

### Comandos (leads)

```bash
# Paquete cliente (desde la raíz del repo)
uv run python scripts/reports/build_leads_client_pack.py

# Validar que merged y current comparten los mismos id_lead antes de importar
uv run python scripts/leads/advanced/validate_contact_hunt_alignment.py

# Limpiar active/ (archivar CSV que no son del núcleo; plan-only by default — add --apply to move/write)
uv run python scripts/leads/advanced/prepare_active_workspace.py
```

Más detalle del pipeline de leads: **[leads/LEAD_PIPELINE.md](leads/LEAD_PIPELINE.md)**. Vista de arquitectura: **[ARCHITECTURE.md](ARCHITECTURE.md)**, contexto de negocio: **[BUSINESS_CONTEXT.md](BUSINESS_CONTEXT.md)**.

<a id="m-eprep-leads-qa"></a>
### QA operativa / publicación (coherencia de artefactos)

Los informes y CSVs de leads son **instantáneas**: pueden quedar desalineados respecto a la base si no se regeneran. La capa de **operational trust** ([paquete `operational_trust`](../src/origenlab_email_pipeline/operational_trust/__init__.py) + [`scripts/qa/`](../scripts/qa/)) contrasta:

- **[`reports/out/client_pack_latest/summary.json`](#m-eprep-leads)** — totales `lead_master_rows` y `fit_bucket` frente al **SQLite** usado por el gate (mismo criterio que [`build_leads_client_pack.py`](../scripts/reports/build_leads_client_pack.py): `fit_bucket` vacío tras `TRIM` cuenta como `low_fit`; la verificación también normaliza claves al comparar). Incluye **`provenance`**: `operational_run_id` si el pack se generó con `ORIGENLAB_LEADS_OPERATIONAL_RUN_ID` (stack), **`publish_gate_validated_this_artifact` siempre false** en el pack, rutas de DB, `generated_at_utc`, revisión git, copia del último [`operational_stack_last_run.json`](../reports/out/README.md) si está presente (ver `caveat`). Si el gate corre con el mismo env `ORIGENLAB_LEADS_OPERATIONAL_RUN_ID`, [`verify_client_pack_consistency.py`](../scripts/qa/verify_client_pack_consistency.py) exige que `provenance.operational_run_id` coincida (**crítico**). Puede marcar **no crítico** si `provenance.db_path_resolved` no coincide con el SQLite de la sesión del gate.
- **[`reports/out/active/leads_top20_for_client_report.csv`](../reports/out/README.md)** — filas, `id_lead` únicos, alineación con `leads_ready_to_contact.csv`, subconjunto del hunt, existencia en `lead_master`, campos mínimos.
- **Cohorte hunt + readiness** — [`leads_contact_hunt_current.csv`](../reports/out/README.md), [`leads_ready_to_contact.csv`](../reports/out/README.md), [`leads_needs_contact_research.csv`](../reports/out/README.md), [`leads_not_ready.csv`](../reports/out/README.md): partición de IDs, sumas, duplicados en hunt.

**Scorecards generados** (al ejecutar [`audit_operational_trust.py`](../scripts/qa/audit_operational_trust.py), también vía [`publish_gate.py`](../scripts/qa/publish_gate.py)):

- JSON: [`reports/out/active/operational_trust_scorecard.json`](../reports/out/README.md) — incluye **`provenance`** (`operational_run_id` si está definido en el entorno, timestamp del audit, SQLite usado, ruta a `summary.json` del pack, copia opcional de `operational_stack_last_run.json`, `caveat` sobre correlación temporal).
- Markdown: `docs/generated/operational_trust_scorecard.md` (gitignored; generated by `audit_operational_trust.py`)

**Frescura del pack (`client_pack_freshness`):** el audit comprueba que `summary.json` tenga `generated_at_utc` parseable y que su antigüedad no supere `--max-pack-age-hours` (por defecto 168 h). Es una comprobación de **snapshot reciente**, no de calidad de negocio.

**Enlaces de evidencia:** [`check_evidence_links.py`](../scripts/qa/check_evidence_links.py) valida formato `http(s)` y, salvo `--skip-evidence-http` en el gate, hace peticiones HTTP con umbrales configurables.

**Flujo recomendado** antes de compartir el paquete cliente: regenerar pack si cambió la DB → ejecutar el gate → solo entregar si **PASS**. Procedimiento: **[RUNBOOK.md §4](RUNBOOK.md#m-eprun-publish-qa)**.

**Provenance (trazabilidad):** los metadatos en `summary.json`, `operational_stack_last_run.json`, `operational_run_manifests/<run_id>.json` y el scorecard JSON **no** sustituyen el gate ni prueban calidad de negocio; documentan `run_id`, resultado del gate en el manifiesto, rutas y flags conocidos por el código.
