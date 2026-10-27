"""Intro email template for «Preparar correo» (prospect introduction mail).

Pure functions — no DB, no HTTP here. All inputs are plain strings / None.
The caller (command handler) fetches the Gmail signature and passes it in.

Template (owner spec 2026-10-25 rev):
  Subject  : «Equipamiento para su proyecto {fund_label} — OrigenLab»
             (no fund_label → «Equipamiento de laboratorio — OrigenLab»)
  Body     : Spanish, personal, Tatiana's plain words.
  Closing  : «Saludos cordiales,» + Gmail signature HTML (or fallback plain text).
  Fuente   : source attribution + unsubscribe line at the bottom.

Saludo rules (Chilean name order: First [Second] Paternal Maternal):
  MUJER        → «Estimada Dra. {apellido}:»
  HOMBRE       → «Estimado Dr. {apellido}:»
  unknown/None → «Estimado/a {person_name}:» or «Estimados:» (no name)

Apellido rule (token index into the display name):
  2 tokens → tokens[-1]          (First Paternal)
  3 tokens → tokens[1]           (First Paternal Maternal → Paternal)
  4+ tokens → tokens[-2]         (First Second Paternal Maternal → Paternal)
"""

from __future__ import annotations

import re
import unicodedata
from html.parser import HTMLParser
from typing import Any


# ── saludo ────────────────────────────────────────────────────────────────────

def _apellido(person_name: str) -> str:
    """Extract the paternal surname from a Chilean display name."""
    parts = person_name.strip().split()
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[-1]
    if len(parts) == 3:
        return parts[1]
    # 4+: First Second Paternal Maternal → index -2
    return parts[-2]


def build_saludo(
    person_name: str | None,
    salutation_sex: str | None,
) -> str:
    """Return the greeting line (no trailing newline)."""
    sex = (salutation_sex or "").strip().upper()
    if person_name:
        apellido = _apellido(person_name)
        if sex == "MUJER":
            return f"Estimada Dra. {apellido}:"
        if sex == "HOMBRE":
            return f"Estimado Dr. {apellido}:"
        return f"Estimado/a {person_name}:"
    return "Estimados:"


# ── equipment list ────────────────────────────────────────────────────────────

# The finding's equipment lines are machine-written and often English («OHAUS/Adam balances analíticas»,
# «IKA stirrers y homogenizers»): the email names Spanish categories and the brands, never the raw lines.
_CATEGORIES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("moisture", "humedad"), "analizadores de humedad"),
    (("balance", "balanza"), "balanzas"),
    (("rotary evaporator", "rotavap", "evaporador"), "evaporadores rotatorios"),
    (("stirrer", "agitador", "shaker"), "agitadores"),
    (("homogeniz", "homogeneiz", "ultra-turrax"), "homogeneizadores"),
    (("centrifug", "centrífug"), "centrífugas"),
    (("sonica", "ultrason", "ultrasonic"), "sonicadores"),
    (("electrophoresis", "electroforesis"), "equipos de electroforesis"),
    (("osmom", "osmóm"), "osmómetros"),
)
_BRANDS: tuple[str, ...] = ("OHAUS", "Adam", "IKA", "Hielscher", "Ortoalresa", "Löser", "SERVA")
_DEFAULT_EQUIPMENT = ("balanzas", "agitadores", "centrífugas")
_DEFAULT_BRANDS = "OHAUS, IKA, Ortoalresa"
_HIELSCHER = "sonicadores Hielscher"  # named in every intro, so never in the brand list


def _join_es(items: list[str]) -> str:
    """«a, b y c» — «e» before a word that starts with the sound /i/ («Adam e IKA»)."""
    if len(items) == 1:
        return items[0]
    last = items[-1]
    conj = "e" if last.lower().startswith(("i", "hi")) and not last.lower().startswith(("hie", "hia")) else "y"
    return ", ".join(items[:-1]) + f" {conj} " + last


def _equipment_phrase(equipment: list[str]) -> str:
    """The Spanish categories the equipment lines name, in order and once each, always ending with
    «sonicadores Hielscher» (owner 2026-10-27: every intro mentions them); the default categories when
    none is recognised («balanzas analíticas, agitadores y sonicadores Hielscher»)."""
    found: list[str] = []
    for line in equipment:
        low = line.lower()
        for keys, label in _CATEGORIES:
            if any(k in low for k in keys):
                if label == "balanzas" and ("analít" in low or "analytic" in low):
                    label = "balanzas analíticas"
                if label not in found and not (label == "balanzas" and "balanzas analíticas" in found):
                    found.append(label)
    found = [c for c in found if c != "sonicadores"] or list(_DEFAULT_EQUIPMENT)
    return _join_es([*found, _HIELSCHER])


def _brands_phrase(equipment: list[str]) -> str:
    """The brands the equipment lines name, in order of first mention (Hielscher is already named with
    its sonicadores); the default trio otherwise."""
    seen: list[tuple[int, str]] = []
    text = " ".join(equipment).lower()
    for brand in _BRANDS:
        if brand == "Hielscher":
            continue
        match = re.search(rf"\b{re.escape(brand.lower())}\b", text)  # «conservación» is not SERVA
        if match:
            seen.append((match.start(), brand))
    return _join_es([b for _, b in sorted(seen)]) if seen else _DEFAULT_BRANDS


# ── source attribution ────────────────────────────────────────────────────────

def _fuente(
    email_source_note: str | None,
    email_source_url: str | None,
    email_source_year: int | None,
) -> str:
    if email_source_note:
        return email_source_note
    if email_source_url and re.search(r"doi\.org|europepmc", email_source_url, re.I):
        return f"su publicación de {email_source_year}" if email_source_year else "su publicación"
    return "un sitio público de su institución"


# ── HTML → plain text (strip tags, decode entities) ──────────────────────────

class _StripTagsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._block_tags = {"p", "br", "div", "li", "tr", "td", "th", "h1", "h2", "h3",
                            "h4", "h5", "h6"}

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def handle_starttag(self, tag: str, _attrs: Any) -> None:
        if tag.lower() in self._block_tags:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._block_tags:
            self._parts.append("\n")

    def text(self) -> str:
        raw = "".join(self._parts)
        # Collapse runs of more than two newlines; strip trailing spaces per line
        lines = [line.rstrip() for line in raw.splitlines()]
        result = "\n".join(lines)
        result = re.sub(r"\n{3,}", "\n\n", result)
        return result.strip()


def strip_html(html: str) -> str:
    """Convert HTML to readable plain text by stripping tags and decoding entities."""
    parser = _StripTagsParser()
    parser.feed(html)
    return parser.text()


# ── subject ───────────────────────────────────────────────────────────────────

def build_subject(fund_label: str | None) -> str:
    if fund_label:
        return f"Equipamiento para su proyecto {fund_label} — OrigenLab"
    return "Equipamiento de laboratorio — OrigenLab"


# ── body builders ─────────────────────────────────────────────────────────────

_FALLBACK_SIGNATURE_PLAIN = "OrigenLab · Valdivia, Chile\ncontacto@origenlab.cl · origenlab.cl"
_FALLBACK_SIGNATURE_HTML = (
    "<p style=\"font-size:0.875em;color:#6b7280\">"
    "OrigenLab · Valdivia, Chile<br>"
    "contacto@origenlab.cl · origenlab.cl"
    "</p>"
)


def build_text(
    *,
    person_name: str | None,
    salutation_sex: str | None,
    project_title: str | None,
    fund_label: str | None,
    equipment: list[str],
    email_source_note: str | None,
    email_source_url: str | None,
    email_source_year: int | None,
    gmail_signature_html: str | None = None,
) -> str:
    """Build the plain-text email body."""
    saludo = build_saludo(person_name, salutation_sex)
    equipos = _equipment_phrase(equipment)
    marcas = _brands_phrase(equipment)
    fuente = _fuente(email_source_note, email_source_url, email_source_year)

    # Opening paragraph (no signer name — uses contacto@'s Gmail signature)
    p1 = (
        "Le escribo de OrigenLab, empresa de Valdivia que vende equipos de laboratorio "
        "a universidades, centros de investigación y laboratorios de servicio, con despacho "
        "a todo Chile. Llevo más de 20 años en el equipamiento de laboratorios, trabajando "
        "directamente con fabricantes en el extranjero y en Chile."
    )

    # Project paragraph
    if project_title and fund_label:
        p2 = (
            f"Vimos que su proyecto «{project_title}» fue adjudicado en {fund_label}, "
            f"felicitaciones. Si en esta etapa está equipando su laboratorio, podemos "
            f"ayudarle con {equipos}, y otros equipos de las marcas con que trabajamos "
            f"({marcas}, entre otras)."
        )
    elif fund_label:
        p2 = (
            f"Vimos que fue adjudicado en {fund_label}, felicitaciones. "
            f"Si en esta etapa está equipando su laboratorio, podemos "
            f"ayudarle con {equipos}, y otros equipos de las marcas con que trabajamos "
            f"({marcas}, entre otras)."
        )
    else:
        p2 = (
            f"Si en esta etapa está equipando su laboratorio, podemos "
            f"ayudarle con {equipos}, y otros equipos de las marcas con que trabajamos "
            f"({marcas}, entre otras)."
        )

    p3 = (
        "Le adjunto nuestro catálogo. Si me cuenta qué necesita, le preparo una cotización "
        "sin compromiso y le asesoro para elegir el equipo según su aplicación y presupuesto."
    )

    signature_text = (
        strip_html(gmail_signature_html) if gmail_signature_html
        else _FALLBACK_SIGNATURE_PLAIN
    )

    footer = (
        f"Encontramos su correo en {fuente}. "
        "Si prefiere no recibir más correos nuestros, responda «no» y no le volveremos a escribir."
    )

    return "\n\n".join([saludo, p1, p2, p3, f"Saludos cordiales,\n{signature_text}", footer])


def build_html(
    *,
    person_name: str | None,
    salutation_sex: str | None,
    project_title: str | None,
    fund_label: str | None,
    equipment: list[str],
    email_source_note: str | None,
    email_source_url: str | None,
    email_source_year: int | None,
    gmail_signature_html: str | None = None,
) -> str:
    """Build the HTML email body (simple paragraphs, no external CSS)."""
    saludo = build_saludo(person_name, salutation_sex)
    equipos = _equipment_phrase(equipment)
    marcas = _brands_phrase(equipment)
    fuente = _fuente(email_source_note, email_source_url, email_source_year)

    import html as _html  # stdlib

    def p(text: str) -> str:
        return f"<p>{_html.escape(text)}</p>"

    def p_raw(html_text: str) -> str:
        return f"<p>{html_text}</p>"

    p1 = (
        "Le escribo de OrigenLab, empresa de Valdivia que vende equipos de laboratorio "
        "a universidades, centros de investigación y laboratorios de servicio, con despacho "
        "a todo Chile. Llevo más de 20 años en el equipamiento de laboratorios, trabajando "
        "directamente con fabricantes en el extranjero y en Chile."
    )

    if project_title and fund_label:
        p2 = (
            f"Vimos que su proyecto «{_html.escape(project_title)}» fue adjudicado en "
            f"{_html.escape(fund_label)}, felicitaciones. "
            f"Si en esta etapa está equipando su laboratorio, podemos "
            f"ayudarle con {_html.escape(equipos)}, y otros equipos de las marcas con que trabajamos "
            f"({marcas}, entre otras)."
        )
    elif fund_label:
        p2 = (
            f"Vimos que fue adjudicado en {_html.escape(fund_label)}, felicitaciones. "
            f"Si en esta etapa está equipando su laboratorio, podemos "
            f"ayudarle con {_html.escape(equipos)}, y otros equipos de las marcas con que trabajamos "
            f"({marcas}, entre otras)."
        )
    else:
        p2 = (
            f"Si en esta etapa está equipando su laboratorio, podemos "
            f"ayudarle con {_html.escape(equipos)}, y otros equipos de las marcas con que trabajamos "
            f"({marcas}, entre otras)."
        )

    p3 = (
        "Le adjunto nuestro catálogo. Si me cuenta qué necesita, le preparo una cotización "
        "sin compromiso y le asesoro para elegir el equipo según su aplicación y presupuesto."
    )

    sig_block = gmail_signature_html if gmail_signature_html else _FALLBACK_SIGNATURE_HTML

    footer = (
        f"Encontramos su correo en {_html.escape(fuente)}. "
        "Si prefiere no recibir más correos nuestros, responda «no» y no le volveremos a escribir."
    )

    parts = [
        "<html><body>",
        p(saludo),
        p(p1),
        f"<p>{p2}</p>",
        p(p3),
        "<p>Saludos cordiales,</p>",
        sig_block,
        f"<p style=\"font-size:0.875em;color:#6b7280\">{_html.escape(footer)}</p>",
        "</body></html>",
    ]
    return "\n".join(parts)


# ── MIME message (multipart/alternative + PDF attachment) ─────────────────────

def build_mime_message(
    *,
    to: str,
    subject: str,
    text_body: str,
    html_body: str,
    pdf_bytes: bytes,
    pdf_filename: str = "Catalogo-OrigenLab-2026.pdf",
) -> bytes:
    """Build a multipart MIME message (text + HTML + PDF attachment).

    Returns the raw bytes ready for base64url encoding in the Gmail API.
    Raises ValueError if `to` is not a valid plain mailbox address.
    """
    import base64
    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.utils import formataddr

    from origenlab_api.v2.gmail_send import SENDER_ADDRESS, SENDER_NAME, _ADDRESS, MAX_ADDRESS_LENGTH

    to = (to or "").strip()
    if len(to) > MAX_ADDRESS_LENGTH or not _ADDRESS.fullmatch(to):
        raise ValueError(f"invalid recipient address: {to!r}")

    msg = MIMEMultipart("mixed")
    msg["From"] = formataddr((SENDER_NAME, SENDER_ADDRESS))
    msg["To"] = to
    msg["Subject"] = subject
    msg["X-OrigenLab-Origin"] = "prospect-intro/v1"

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(text_body, "plain", "utf-8"))
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt)

    pdf_part = MIMEApplication(pdf_bytes, _subtype="pdf")
    pdf_part.add_header("Content-Disposition", "attachment", filename=pdf_filename)
    msg.attach(pdf_part)

    return msg.as_bytes()


# ── catalogue asset ────────────────────────────────────────────────────────────

def load_catalogue_pdf() -> bytes:
    """Return the bytes of Catalogo-OrigenLab-2026.pdf bundled with this package."""
    from importlib.resources import files
    return files("origenlab_api.v2.prospect_findings.assets").joinpath(
        "Catalogo-OrigenLab-2026.pdf"
    ).read_bytes()


__all__ = [
    "build_html",
    "build_mime_message",
    "build_saludo",
    "build_subject",
    "build_text",
    "load_catalogue_pdf",
    "strip_html",
]
