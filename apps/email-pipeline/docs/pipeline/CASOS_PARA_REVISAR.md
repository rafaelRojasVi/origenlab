# Casos para revisar (queue contract; former Streamlit v1)

Status: canonical  
Owner: email-pipeline-maintainers

> **Current surface (2026-08):** Streamlit was removed on 2026-06-04. This document keeps the message-level queue/data contract current; UI-specific behavior below is retained only as historical reference. The active operator UI is [`apps/dashboard`](../../../dashboard/README.md) + [`apps/api`](../../../api/README.md).

## Qué es

Cola operativa de mensajes del buzón **Gmail `contacto@origenlab.cl`**, consumida por library code (`cases_review_queue`) y tests. El filtro `source_file` mantiene el alcance Gmail de contacto. Una fila = **`emails.id`**. La antigua página `apps/business_mart_app.py` mostraba esta cola antes del retiro de Streamlit.

- **No** es una bandeja completa, **no** es CRM y **no** envía correos.
- La base SQLite se usa en **solo lectura**.
- La cola **no genera ni envía borradores**. La antigua UI Streamlit solo entregaba el `email_id` elegido al flujo **Borrador comercial**.

## Alcance v1

- Solo correos con `lower(source_file) LIKE 'gmail:contacto@origenlab.cl/%'`.
- Sin agrupación por hilo.
- Sin usar `v_commercial_candidate_queue` como fuente principal de filas (la cola es a nivel mensaje).

## Fuentes de datos

- **Obligatorio:** tabla cruda `emails`.
- **Opcional:** `commercial_email_signal_fact` (inteligencia comercial v1). Si no existe, la cola conserva un **modo reducido** (solo lista reciente + filtros básicos). La antigua UI mostraba además un texto explicativo.
- **Detalle del caso:** misma prioridad de cuerpo que Borrador (`top_reply_clean` → `full_body_clean` → `body_text_clean` → `body`).
- **Conteo de documentos:** si existe `document_master`, se muestra cuántos documentos están ligados al `email_id`.

## Enriquecimiento comercial

Agregación por `email_id` sobre `commercial_email_signal_fact`:

- presencia de señal **positiva** y/o **supresión**
- intensidad máxima entre señales positivas (si aplica)

La antigua UI mostraba una **pista corta en español** y permitía expandir filas de señal; esos detalles se conservan aquí como comportamiento histórico de presentación.

## Filtros v1

- Ventana: 7 / 30 / 90 días (por prefijo `YYYY-MM-DD` de `date_iso`).
- Excluir rebotes / DSN obvios (heurística determinista sobre remitente/asunto).
- Opcional: solo mensajes con señal positiva (solo si existe la tabla CI).

## Former Streamlit handoff to Borrador comercial (historical)

La antigua UI Streamlit guardaba `borrador_handoff_email_id` en `st.session_state`, navegaba a **Borrador comercial**, y esa página:

1. Fija el origen en **Correo reciente (Gmail contacto)**.
2. Selecciona el mismo `id` en el desplegable (y asegura que aparezca en la lista vía `ensure_email_ids` en `load_contacto_gmail_email_choices_df`).
3. Elimina la clave de handoff para no repetir en bucle.

No se duplica `build_draft_package` ni la lógica de generación.

## Comandos

**Streamlit UI removed (2026-06-04).** This queue is consumed by library code (`cases_review_queue`) and tests; active operator UI is [`apps/dashboard`](../../../dashboard/README.md) + [`apps/api`](../../../api/README.md) over the Postgres mirror. Retirement plan: [`audits/ACTIVE_STACK_AND_STREAMLIT_RETIREMENT_PLAN_20260604.md`](../audits/ACTIVE_STACK_AND_STREAMLIT_RETIREMENT_PLAN_20260604.md).

Para enriquecimiento: `uv run python scripts/commercial/build_commercial_intel_v1.py` (ver `COMMERCIAL_INTEL_V1.md`).

## Limitaciones v1

- Mensajes sin `date_iso` parseable (menos de 10 caracteres o fuera del patrón) **no entran** en la ventana de fechas.
- La heurística de ruido no sustituye un clasificador completo.
- Titan IMAP (`imap:contacto@...`) **no** está en el alcance v1.
