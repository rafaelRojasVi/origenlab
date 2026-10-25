"""The case email reader: one stored `.eml` as plain text, people by name or domain, the quoted
earlier conversation split off; over HTTP only for sales/admin and only for an email on the case.
Every name, address and byte here is invented.
"""
from __future__ import annotations

from email.message import EmailMessage

from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.case_mail import read_message, split_quoted
from origenlab_api.v2.catalog.storage import FakeStorage
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup

LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
HEADERS = {OPERATOR_EMAIL_HEADER: "op@example.test"}
CASE = "00000000-0000-4000-8000-0000000000aa"
MSG = "18f0a1b2c3d4e5f6"


def _eml(*, html: str | None = None, text: str | None = None, attach: bool = False) -> bytes:
    m = EmailMessage()
    m["From"] = "Ana Ficticia <ana@lab-ficticio.example>"
    m["To"] = "Ventas <ventas@origenlab.invalid>, sinnombre@otro-lab.example"
    m["Subject"] = "Consulta balanza"
    m["Date"] = "Mon, 20 Oct 2026 10:15:00 -0300"
    if text is not None:
        m.set_content(text)
        if html is not None:
            m.add_alternative(html, subtype="html")
    elif html is not None:
        m.set_content(html, subtype="html")
    if attach:
        m.add_attachment(b"%PDF-1.7 ficticio", maintype="application", subtype="pdf", filename="Especificacion.pdf")
    return m.as_bytes()


def test_a_message_reads_as_plain_text_with_people_by_name_or_domain() -> None:
    out = read_message(_eml(text="Hola,\n\nNecesito cotizar una balanza.\n\nSaludos", attach=True))
    assert out["from"] == "Ana Ficticia"
    assert out["to"] == ["Ventas", "otro-lab.example"]
    assert out["subject"] == "Consulta balanza"
    assert out["sent_at"].startswith("2026-10-20T10:15:00")
    assert out["text"] == "Hola,\n\nNecesito cotizar una balanza.\n\nSaludos"
    assert out["quoted"] is None
    assert out["attachments"] == [{"filename": "Especificacion.pdf", "size": 17}]


def test_html_only_mail_becomes_text_and_no_markup_survives() -> None:
    out = read_message(_eml(html='<p>Hola <b>equipo</b></p><img src="https://track.example/p.gif"><p>Gracias</p>'))
    assert "<" not in out["text"] and "track.example" not in out["text"]
    assert "Hola equipo" in out["text"] and "Gracias" in out["text"]


def test_the_quoted_conversation_is_split_off() -> None:
    main, quoted = split_quoted("Sí, confirmo.\n\nEl lun, 20 oct 2026 a las 10:15, Ventas escribió:\n> ¿Confirma?")
    assert main == "Sí, confirmo." and quoted.startswith("El lun, 20 oct 2026")
    main, quoted = split_quoted("Adjunto OC.\n\nDe: Ventas\nEnviado: lunes\nPara: Ana\nAsunto: Cotización")
    assert main == "Adjunto OC." and quoted.startswith("De: Ventas")
    main, quoted = split_quoted("Gracias\n> línea citada\n> otra")
    assert main == "Gracias" and quoted == "> línea citada\n> otra"
    # A forward with nothing above the quote shows it all as the message.
    assert split_quoted("---------- Forwarded message ---------\nDe: X") == (
        "---------- Forwarded message ---------\nDe: X", None)
    assert split_quoted("Sin cita") == ("Sin cita", None)


# ──────────────────────────────────────────────────────────────── route ──


class _Lookup(OperatorLookup):
    def __init__(self, operator):
        self._operator = operator

    def by_email(self, email_norm):
        return self._operator


class _Repo:
    def __init__(self, source):
        self.source = source
        self.asked: list[tuple[str, str]] = []

    def opportunity_message_source(self, opportunity_id, message_id):
        self.asked.append((opportunity_id, message_id))
        return self.source


def _client(role="sales", source=None, stored: bytes | None = None):
    from origenlab_api.v2.crm_workspace_routes import workspace_router

    app = FastAPI()
    app.include_router(workspace_router)
    app.state.crm_workspace = _Repo(source)
    mail = FakeStorage("mail")
    if stored is not None:
        mail.put_if_absent("2026/10/m.eml", stored, "message/rfc822")
    app.state.mail_storage = mail
    op = OperatorIdentity(operator_id="00000000-0000-4000-8000-000000000001", email_norm="op@example.test",
                          display_name="Op", role=role, status="active")
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(op))
    return app, TestClient(app)


def _url(message_id: str = MSG) -> str:
    return f"/v2/workspace/opportunities/{CASE}/messages/{message_id}"


def test_the_reader_returns_the_stored_email() -> None:
    app, client = _client(source={"eml_storage_path": "mail/2026/10/m.eml"}, stored=_eml(text="Hola"))
    r = client.get(_url(), headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["text"] == "Hola" and r.json()["message_id"] == MSG
    assert app.state.crm_workspace.asked == [(CASE, MSG)]


def test_the_reader_is_for_roles_that_see_addresses() -> None:
    app, client = _client(role="viewer", source={"eml_storage_path": "mail/2026/10/m.eml"}, stored=_eml(text="Hola"))
    assert client.get(_url(), headers=HEADERS).status_code == 403
    assert app.state.crm_workspace.asked == []


def test_an_email_outside_the_case_or_not_stored_is_refused() -> None:
    _, client = _client(source=None)
    assert client.get(_url(), headers=HEADERS).status_code == 404
    assert client.get(_url("../../etc"), headers=HEADERS).status_code == 404
    _, client = _client(source={"eml_storage_path": None})
    assert client.get(_url(), headers=HEADERS).status_code == 409
    _, client = _client(source={"eml_storage_path": "mail/2026/10/missing.eml"})
    assert client.get(_url(), headers=HEADERS).status_code == 409
