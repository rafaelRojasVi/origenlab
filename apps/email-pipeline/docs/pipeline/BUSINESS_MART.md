# Business mart (client-facing database layer)

Goal: provide **curated, searchable business entities** on top of the email archive without forcing the client to browse raw emails.

Raw archive remains unchanged:
- `emails`
- `attachments`
- `attachment_extracts`

Derived (rebuildable) layer:
- `contact_master`
- `organization_master`
- `document_master`
- `opportunity_signals`

Build script: `scripts/mart/build_business_mart.py`

## 1) Noise exclusions (conservative)
We exclude obvious system/non-business senders from the mart scan:
- `mailer-daemon`, `postmaster`
- delivery status / undeliverable subjects
- common NDR phrases (English/Spanish)

This logic is in `src/origenlab_email_pipeline/business_mart.py` (`is_noise_sender`).

## 2) Internal vs external
The mart focuses on **external contacts**.

Direction identity:
- canonical OriginLab operator domains come from the shared `INTERNAL_OPERATOR_DOMAINS` registry
- sender frequency is **not** treated as an identity signal
- `--internal-domain yourdomain.com` is repeatable and adds explicit operator-approved domains without replacing the canonical set

## 3) Organization name/type guesses
These are transparent heuristics:

- **organization_name_guess**:
  - derived from the second-level domain, normalized and title-cased
  - example: `soviquim.cl` → `Soviquim`

- **organization_type_guess**:
  - `education`: `.edu`, `.ac`, or common Chilean university domains/keywords
  - `government`: `.gov`, `gob.cl`
  - `consumer_email`: gmail/hotmail/outlook/yahoo/live
  - else `business`

## 4) Equipment tags
Equipment tags are keyword-based (same general intent as the report’s equipment section).

Tags:
- `microscopio`
- `centrifuga`
- `espectrofotometro`
- `phmetro`
- `autoclave`
- `balanza`
- `cromatografia_hplc`
- `incubadora`
- `titulador`
- `liofilizador`
- `horno_mufla`
- `pipetas`
- `humedad_granos`

Source text used for tagging:
- email `subject`
- `top_reply_clean` (fallback to `full_body_clean`)
- document preview (`attachment_extracts.text_preview`) for documents

## 5) Document master (`document_master`)
One row per extracted document (Phase 2.4), pulled from `attachment_extracts` where:
- `extract_status = 'success'`

Fields include:
- `doc_type` (rule-based from Phase 2.4)
- `extracted_preview_raw`: preview original/truncado
- `extracted_preview_clean`: preview limpiado para lectura
- `preview_quality_score` (0–1): heurístico de legibilidad
- boolean term signals (`has_*`)
- `equipment_tags` (comma-separated)

### Preview cleaning rules (Phase: UI-focused)
`extracted_preview_clean` intenta:
- colapsar whitespace y líneas vacías repetidas
- eliminar marcadores tipo `sep=,` (CSV)
- recortar líneas extremadamente largas (ruido de tablas XLSX/CSV/XML)
- truncar a un tamaño pequeño para lectura

No es “resumen inteligente”; es **limpieza conservadora**.

## 6) Contact master (`contact_master`)
One row per **external contact email** aggregated from the **archive / mail graph**. Use for exploration, rollups, and joins—not as a guaranteed **buyer** or **procurement truth** layer. Cold-outreach exports that draw from this pool still pass [`candidate_export_gate.py`](../../src/origenlab_email_pipeline/candidate_export_gate.py) (shared with the canonical lead queue), which trims obvious leaks but **does not** certify commercial intent.

Direction:
- outbound email: sender domain is internal → external recipients are counted
- inbound email: sender domain is external → external sender is counted

Counts include:
- total/inbound/outbound interactions
- email-level intent flags (quote/invoice/purchase) from message text
- doc-level counts from `attachment_extracts` aggregated per email

## 7) Organization master (`organization_master`)
Rollup of `contact_master` grouped by domain.

Includes:
- total emails, contacts
- rollups of quote/invoice/purchase/doc metrics
- `key_contacts` = top 5 contact emails by volume (comma-separated)

## 8) Opportunity signals (`opportunity_signals`)
This is a small, explainable table of **heuristic “interesting” signals**.

Current MVP signals:
- `quote_email_plus_quote_doc`: contact has repeated quote-like emails and at least one quote doc
- `education_with_quote_activity`: education org with quote activity
- `dormant_contact`: historic contact (last seen before a cutoff) but high total volume
- `repeated_equipment_theme`: contact repeatedly mentions the same equipment tag

Each row includes:
- `signal_type`
- entity (`contact` / `organization`)
- `score` (0–1)
- `details_json` (why this was triggered)

## Rebuildability
Run with `--rebuild` to truncate and regenerate the mart tables deterministically from the archive + extracts.

## Removed Streamlit UI (historical reference)

Former app: `apps/business_mart_app.py` (**removed 2026-06-04**). The list below records the retired UI for historical/domain context; the active operator UI is [`apps/dashboard`](../../../dashboard/README.md) backed by [`apps/api`](../../../api/README.md).

Former structure:
1. **Resumen** (KPIs + gráficos compactos)
2. **Salud de datos** (conteos, rango `date_iso`, desglose `source_file`, heurística mart vs crudo, `pipeline_kv` / `pipeline_run` si existen)
3. **Actividad contacto Gmail** — correos recientes con `source_file` tipo `gmail:contacto@origenlab.cl/%`, resúmenes 7/30/90 días, documentos y señales unidos por `email_id` si el mart existe; no sustituye un visor de bandeja completo.
4. **Casos para revisar** — cola operativa mensaje a mensaje (Gmail contacto), enriquecimiento opcional con `commercial_email_signal_fact`, entrega a **Borrador comercial**; ver [`CASOS_PARA_REVISAR.md`](CASOS_PARA_REVISAR.md).
5. **Cola outreach marketing** — candidatos desde `lead_master` vía [`compute_next_marketing_recipients()`](../../src/origenlab_email_pipeline/next_marketing_queue.py); mismo módulo [`candidate_export_gate.py`](../../src/origenlab_email_pipeline/candidate_export_gate.py) que [`export_marketing_from_contact_master.py`](../../scripts/leads/advanced/export_marketing_from_contact_master.py), con **ruido de correo más estricto** en la vía `contact_master` (grafico de correo). Sin envío automático; no reemplaza revisión humana ni “verdad comprador”.
6. **Borrador comercial** (revisión OrigenLab, sin envío)
7. **Oportunidades** (señales con explicación en español)
8. **Equipos**
9. **Organizaciones**
10. **Contactos**
11. **Documentos** (preview limpio + texto crudo en expander)
12. **Candidatos comerciales** (si existe la capa commercial intel en el mismo SQLite)

