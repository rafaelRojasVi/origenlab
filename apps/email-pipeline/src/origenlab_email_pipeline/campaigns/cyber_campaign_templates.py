"""Spanish email templates for Cyber outreach."""

from __future__ import annotations

from origenlab_email_pipeline.campaigns.cyber_campaign_types import OPT_OUT_LINE_ES, CyberCampaignRow


def _greeting_line(contact_name: str) -> str:
    name = (contact_name or "").strip()
    return f"Estimado/a {name}," if name else "Estimado/a,"


def _warm_subject(org: str) -> str:
    short = (org or "su laboratorio").strip()
    if len(short) > 48:
        short = short[:45].rstrip() + "…"
    return f"Seguimiento Cyber — equipos seleccionados para {short}"


def _previous_subject(org: str) -> str:
    short = (org or "su institución").strip()
    if len(short) > 48:
        short = short[:45].rstrip() + "…"
    return f"Beneficio Cyber OrigenLab — {short}"


def _net_new_subject(org: str) -> str:
    short = (org or "laboratorio").strip()
    if len(short) > 48:
        short = short[:45].rstrip() + "…"
    return f"Cyber — equipos de laboratorio para {short}"


def template_warm_follow_up_es(
    *,
    contact_name: str,
    organization: str,
    product_angle: str,
) -> tuple[str, str]:
    greeting = _greeting_line(contact_name)
    org = (organization or "su institución").strip()
    subject = _warm_subject(org)
    body = f"""{greeting}

Retomamos el contacto con {org} en el marco de Cyber: disponemos de una mejora comercial (aprox. 5–10%) en equipos de laboratorio seleccionados, sujeta a marca, disponibilidad y confirmación técnica/comercial.

{product_angle}

Si le interesa revisar alternativas o actualizar una cotización en curso, con gusto coordinamos por este medio. No adjuntamos catálogo por defecto; podemos enviar fichas técnicas puntuales según su necesidad.

{OPT_OUT_LINE_ES}

Saludos cordiales,
Equipo comercial — OrigenLab"""
    return subject, body


def template_previous_buyer_es(
    *,
    contact_name: str,
    organization: str,
    product_angle: str,
) -> tuple[str, str]:
    greeting = _greeting_line(contact_name)
    org = (organization or "su institución").strip()
    subject = _previous_subject(org)
    body = f"""{greeting}

Por su relación previa con OrigenLab ({org}), queremos informarle del beneficio Cyber en equipos de laboratorio seleccionados: mejora comercial en torno a 5–10%, siempre sujeta a marca, stock disponible y validación técnica/comercial.

{product_angle}

No prometemos precios fijos ni stock sin confirmar. Si desea una propuesta acotada a su línea de trabajo, indíquenos equipo o aplicación y respondemos con opciones concretas.

{OPT_OUT_LINE_ES}

Saludos cordiales,
Equipo comercial — OrigenLab"""
    return subject, body


def template_net_new_cyber_es(
    *,
    contact_name: str,
    organization: str,
    product_angle: str,
) -> tuple[str, str]:
    greeting = _greeting_line(contact_name)
    org = (organization or "su laboratorio").strip()
    subject = _net_new_subject(org)
    body = f"""{greeting}

OrigenLab apoya laboratorios en Chile con equipamiento y consumibles. En Cyber ofrecemos una mejora comercial en equipos seleccionados (referencial 5–10%), sujeta a marca, disponibilidad y confirmación técnica/comercial — sin comprometer stock ni precio final hasta revisión formal.

Contexto: {org}. {product_angle}

Si le es útil, podemos orientar por aplicación (p. ej. preparación de muestras, esterilización, análisis) sin enviar catálogo masivo.

{OPT_OUT_LINE_ES}

Saludos cordiales,
Equipo comercial — OrigenLab"""
    return subject, body


def apply_templates_to_row(
    row: "CyberCampaignRow",
) -> "CyberCampaignRow":
    from dataclasses import replace

    from origenlab_email_pipeline.campaigns.cyber_campaign_types import (
        SEGMENT_NET_NEW,
        SEGMENT_PREVIOUS,
        SEGMENT_WARM,
        CyberCampaignRow,
    )

    kwargs = {
        "contact_name": row.contact_name,
        "organization": row.organization,
        "product_angle": row.product_angle,
    }
    if row.segment == SEGMENT_WARM:
        subj, msg = template_warm_follow_up_es(**kwargs)
    elif row.segment == SEGMENT_PREVIOUS:
        subj, msg = template_previous_buyer_es(**kwargs)
    else:
        subj, msg = template_net_new_cyber_es(**kwargs)
    return replace(row, suggested_subject=subj, suggested_message=msg)


def render_email_templates_markdown() -> str:
    _, warm = template_warm_follow_up_es(
        contact_name="María",
        organization="Laboratorio Ejemplo",
        product_angle="Balanzas y equipos de preparación",
    )
    _, prev = template_previous_buyer_es(
        contact_name="Juan",
        organization="Universidad Ejemplo",
        product_angle="Histórico de compras",
    )
    _, net = template_net_new_cyber_es(
        contact_name="",
        organization="Clínica Ejemplo",
        product_angle="Encaje laboratorio clínico",
    )
    return f"""# Plantillas Cyber — OrigenLab (revisión manual)

**Política:** B2B técnico, Chile. Sin envío automático.

## 1. Seguimiento caso abierto / warm

```
{warm.strip()}
```

## 2. Comprador o respondedor previo

```
{prev.strip()}
```

## 3. Prospecto net-new Cyber

```
{net.strip()}
```
"""
