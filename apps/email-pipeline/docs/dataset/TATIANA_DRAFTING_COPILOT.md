# Tatiana Drafting Copilot (v1, review-first)

Status: canonical  
Owner: email-pipeline-maintainers  
Last reviewed: 2026-03-29

<a id="m-tatiana-scope"></a>
## OrigenLab scope (not a side experiment)

This work lives in the **OrigenLab monorepo** and supports **real commercial operations**: the same company that owns the [marketing site](../../../web/README.md), [business rules](../../../../docs/business/BUSINESS_RULES_QUOTES_AND_SUPPLIERS.md), and the **email archive / reporting** pipeline. The copilot drafts in **Tatiana / Labdelivery** voice using examples derived from that archive; it does **not** replace canonical web copy ([`company-scope.md`](../../../web/docs/company-scope.md)) or autonomous business systems.

- Monorepo entry context: [`docs/PROJECT_CONTEXT.md`](../../../../docs/PROJECT_CONTEXT.md)
- Email pipeline context: [`APP_CONTEXT.md`](../APP_CONTEXT.md#m-epapp-tatiana)
- **Operational pilot** (mandatory human review, file exports only): [`TATIANA_PILOT_WORKFLOW.md`](TATIANA_PILOT_WORKFLOW.md)
- **OrigenLab facts layer** (`--origenlab`, web `src/data` as source of truth): [`ORIGENLAB_COMMERCIAL_DRAFTING_CONTEXT.md`](ORIGENLAB_COMMERCIAL_DRAFTING_CONTEXT.md)

---

This module is a **local/offline-friendly drafting copilot** for Tatiana-style commercial emails.

It is built from curated artifacts:

- `reports/out/tatiana_candidate_cohort_marketing_top200_labeled_final.csv`
- `reports/out/tatiana_candidate_cohort_marketing_top200_seed_style_guide.csv`
- `reports/out/tatiana_candidate_cohort_marketing_top200_seed_retrieval.csv`

## What It Is

- Example normalization into a canonical schema.
- Local retrieval index (default TF-IDF; optional sentence-transformers).
- Draft package builder (case + retrieved style examples + retrieved precedents + guardrails).
- Safe mock generation fallback (works without API keys).
- Optional **OpenAI Chat Completions** generator (`OpenAIChatDraftGenerator`) for real drafts.
- Held-out evaluation loop with human-review CSV outputs.

## What It Is Not

- Not an autonomous sender.
- Not production orchestration.
- No CRM/email side effects.
- No hidden actions.

## Commands

From `apps/email-pipeline`:

### 1) Build index

```bash
python3 scripts/tatiana/build_tatiana_example_index.py --method tfidf
```

Writes: `reports/out/tatiana_copilot_index.json`

### 2) Generate one draft package

Create a case JSON (example):

```json
{
  "case_id": "case_001",
  "subject": "Consulta cotización balanza",
  "body_text": "Estimados, necesitamos cotización para balanza analítica y plazo de entrega.",
  "expected_label": "quote_followup",
  "context_metadata": {"customer_segment": "lab"}
}
```

Run (default: **historical / Tatiana curator** drafting profile — same as pre-change CLI behavior):

```bash
python3 scripts/tatiana/generate_tatiana_draft_package.py \
  --case-json /tmp/case.json \
  --out reports/out/tatiana_draft_package_case001.json
```

**OrigenLab mode** (same **`DRAFTING_PROFILE_ORIGENLAB`** + **`load_origenlab_drafting_context()`** used by the former Streamlit **Borrador comercial** surface and **`run_tatiana_pilot_batch.py --origenlab`**):

```bash
uv run python scripts/tatiana/generate_tatiana_draft_package.py \
  --origenlab \
  --case-json /tmp/case.json \
  --out reports/out/tatiana_draft_package_origenlab.json
```

Use a real model (requires `ORIGENLAB_TATIANA_OPENAI_API_KEY` or `OPENAI_API_KEY`; see `.env.example`):

```bash
uv run python scripts/tatiana/generate_tatiana_draft_package.py \
  --origenlab \
  --generator openai_chat \
  --case-json /tmp/case.json \
  --out reports/out/tatiana_draft_package_openai.json
```

### 3) Run held-out evaluation

```bash
python3 scripts/tatiana/run_tatiana_draft_eval.py --max-cases 30
```

OpenAI-backed eval (same CSV/JSON outputs; costs API usage per case):

```bash
uv run python scripts/tatiana/run_tatiana_draft_eval.py \
  --generator openai_chat \
  --max-cases 30
```

Writes:

- `reports/out/<timestamp>_tatiana_draft_eval/eval_cases.csv`
- `reports/out/<timestamp>_tatiana_draft_eval/eval_summary.json`
- `reports/out/<timestamp>_tatiana_draft_eval/eval_*.json` per-case packages

### 4) Manual scoring + summary

After you fill reviewer scores in `eval_cases.csv`, run:

```bash
uv run python scripts/tatiana/summarize_tatiana_eval_review.py \
  --eval-dir reports/out/<timestamp>_tatiana_draft_eval
```

See `docs/dataset/TATIANA_EVAL_REVIEW.md` for the scoring rubric and interpretation.

### 5) Provider pilot batch (real cases + mandatory human review)

Small **file-driven** batches: generate drafts into `reports/out/YYYYMMDD_HHMMSS_tatiana_pilot_batch/`, fill **`pilot_review.csv`**, then summarize. **No send integration.** Default cohort-based input is **historical / marketing-curator style**, not a live inbox loop — see the pilot doc for the mock → provider → cohort milestone and that distinction.

See **`docs/dataset/TATIANA_PILOT_WORKFLOW.md`** for input formats, CLI, and safety rules.

### Borrador comercial (revisión — library)

Human-in-the-loop draft review helpers live in [`tatiana_copilot/draft_review_helpers.py`](../../src/origenlab_email_pipeline/tatiana_copilot/draft_review_helpers.py) (Streamlit UI **removed** 2026-06-04; use pilot batch or future dashboard wiring):

- **No sending** (no Gmail/Titan send, no CRM). Review fields and exports are for workflow only; `approved_for_send` in exported CSV is always **`n`**.
- **OrigenLab mode always** — same stack as the pilot (`build_draft_package` + `DRAFTING_PROFILE_ORIGENLAB` + `load_origenlab_drafting_context()`), cohort TF-IDF index from the usual `reports/out/tatiana_candidate_cohort_marketing_top200_*.csv` seeds.
- **Intake for the exact customer message** is either typed manually (`draft_case_from_manual`) or loaded from the raw **`emails`** table (`draft_case_from_email_row`; `source_file` like `gmail:contacto@origenlab.cl/%`).
- **Generator choice:** `resolve_draft_review_generator` — mock only when explicitly requested; no silent fallback when OpenAI is selected but misconfigured.
- **Optional export** via `export_draft_review_artifact` writes under `reports/out/<timestamp>_streamlit_borrador_comercial/` (legacy folder suffix unchanged). Same column conventions as pilot review CSVs.

## Guardrails

- Human approval required before any send.
- Retrieval examples are precedent/style context, not factual truth.
- No invented pricing, stock, lead time, product specs, or guarantees.
- Abstain when context is insufficient.

## Limitations (v1)

- Default generator is `MockDraftGenerator` (template-like, safe fallback).
- `OpenAIChatDraftGenerator` needs network + API key; API failures abstain with `system_notes` starting with `openai_error:` so eval runs can still finish.
- TF-IDF retrieval is simple and robust but less semantic than embeddings.
- Signature stripping and phrase extraction are intentionally conservative.

## Suggested Phase 2

- Add embedding retrieval mode in CI-tested path (sentence-transformers).
- Add richer eval scoring aggregation and per-label retrieval quality dashboards.
