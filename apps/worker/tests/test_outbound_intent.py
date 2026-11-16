"""Our own closing email («lo dejo en pausa», «quedo atenta para cuando…») and the day it names
(owner 2026-11-16: pause the case until that day, else 30 days)."""

from __future__ import annotations

import datetime as dt

import pytest

from origenlab_worker.outbound_intent import OUTBOUND_INTENT_VALUE_NORM, read_outbound_intent

SENT = dt.date(2026, 11, 16)  # a Monday


@pytest.mark.parametrize("text", [
    "Estimada Paula: lo dejo en pausa por ahora, cualquier cosa me escribe.",
    "Dejamos la cotización en pausa hasta que tengan el presupuesto.",
    "Quedo atenta para cuando tengan novedades del proyecto.",
    "Quedo atento a cuando definan la compra.",
    "Entiendo, no le volveré a escribir por esta cotización.",
    "Doy por cerrado este seguimiento; si más adelante lo necesitan, me avisan.",
    "Lo retomamos en marzo cuando salgan los fondos.",
    "Cuando tengan el presupuesto aprobado me avisan y actualizo la cotización.",
])
def test_a_closing_email_is_read_as_closing(text: str) -> None:
    reading = read_outbound_intent(text, SENT)
    assert reading is not None and reading["closing"] is True and reading["phrase"]


@pytest.mark.parametrize("text", [
    "Quedo atenta a sus comentarios.",
    "Adjunto la cotización. Quedo atento a cualquier consulta.",
    "Le escribo para saber si pudo revisar la cotización que le envié.",
    "¿Le parece si lo llamo el jueves para revisar dudas?",
    "",
])
def test_an_ordinary_email_is_not_closing(text: str) -> None:
    assert read_outbound_intent(text, SENT) is None


def test_quoted_client_text_is_not_ours() -> None:
    text = ("Muchas gracias por la información.\n\n"
            "El lun, 10 nov 2026 a las 10:00, Cliente <cliente@ejemplo.invalid> escribió:\n"
            "> Lo dejo en pausa hasta marzo, quedo atento para cuando tengamos fondos.")
    assert read_outbound_intent(text, SENT) is None


@pytest.mark.parametrize(("text", "until"), [
    ("Lo dejo en pausa hasta marzo.", dt.date(2027, 3, 1)),
    ("Lo retomamos en diciembre, quedo atenta para cuando decidan.", dt.date(2026, 12, 1)),
    ("Quedo atenta para cuando tengan fondos el próximo año.", dt.date(2027, 1, 4)),
    ("Lo dejo en pausa y le escribo en dos semanas.", dt.date(2026, 11, 30)),
    ("Lo dejo en pausa y le escribo en 3 meses.", dt.date(2027, 2, 16)),
    ("Lo dejo en pausa por ahora.", None),
    ("Lo dejo en pausa hasta noviembre.", dt.date(2027, 11, 1)),
])
def test_the_day_it_names(text: str, until) -> None:
    reading = read_outbound_intent(text, SENT)
    assert reading is not None and reading["until"] == (until.isoformat() if until else None)


def test_the_reading_is_versioned() -> None:
    assert OUTBOUND_INTENT_VALUE_NORM == "outbound_intent:v1"
