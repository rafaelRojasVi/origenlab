"""Unit tests for `intro_email.py` — pure functions, no DB, no network. Invented values only."""

from __future__ import annotations

import pytest

from origenlab_api.v2.prospect_findings.intro_email import (
    build_html,
    build_mime_message,
    build_saludo,
    build_subject,
    build_text,
    load_catalogue_pdf,
    strip_html,
)


# ── saludo ────────────────────────────────────────────────────────────────────

class TestBuildSaludo:
    def test_mujer_two_tokens(self) -> None:
        assert build_saludo("Ana Ficticia", "MUJER") == "Estimada Dra. Ficticia:"

    def test_hombre_three_tokens(self) -> None:
        # 3 tokens → tokens[1] (paternal surname)
        assert build_saludo("Carlos Ficticio Laboratorio", "HOMBRE") == "Estimado Dr. Ficticio:"

    def test_mujer_four_tokens(self) -> None:
        # 4 tokens: First Second Paternal Maternal → tokens[-2] = Paternal
        assert build_saludo("Ana Belen Ficticia Laboratorio", "MUJER") == "Estimada Dra. Ficticia:"

    def test_unknown_sex_with_name(self) -> None:
        result = build_saludo("Ana Ficticia", None)
        assert result == "Estimado/a Ana Ficticia:"

    def test_unknown_sex_with_name_string(self) -> None:
        result = build_saludo("Ana Ficticia", "OTRO")
        assert result == "Estimado/a Ana Ficticia:"

    def test_no_name(self) -> None:
        assert build_saludo(None, None) == "Estimados:"

    def test_no_name_with_sex(self) -> None:
        assert build_saludo(None, "MUJER") == "Estimados:"

    def test_single_token_name(self) -> None:
        result = build_saludo("Ficticia", "MUJER")
        assert result == "Estimada Dra. Ficticia:"


# ── subject ───────────────────────────────────────────────────────────────────

class TestBuildSubject:
    def test_with_fund_label(self) -> None:
        assert build_subject("Fondecyt Regular 2025") == "Equipamiento para su proyecto Fondecyt Regular 2025 — OrigenLab"

    def test_without_fund_label(self) -> None:
        assert build_subject(None) == "Equipamiento de laboratorio — OrigenLab"

    def test_empty_fund_label(self) -> None:
        assert build_subject("") == "Equipamiento de laboratorio — OrigenLab"


# ── strip_html ────────────────────────────────────────────────────────────────

class TestStripHtml:
    def test_strips_tags(self) -> None:
        result = strip_html("<p>Hola <strong>mundo</strong></p>")
        assert "Hola mundo" in result
        assert "<" not in result

    def test_decodes_entities(self) -> None:
        result = strip_html("&lt;test&gt; &amp; más")
        assert "<test>" in result
        assert "&" in result

    def test_empty_string(self) -> None:
        assert strip_html("") == ""

    def test_plain_text(self) -> None:
        assert strip_html("Hola mundo") == "Hola mundo"


# ── build_text ────────────────────────────────────────────────────────────────

class TestBuildText:
    def _text(self, **over):
        defaults = dict(
            person_name="Ana Ficticia",
            salutation_sex="MUJER",
            project_title="Estudio ficticio de suelos",
            fund_label="Fondecyt Regular 2025",
            equipment=["Balanza analítica", "Agitador orbital"],
            email_source_note="ResearchGate ficticio",
            email_source_url=None,
            email_source_year=None,
        )
        defaults.update(over)
        return build_text(**defaults)

    def test_saludo_appears(self) -> None:
        assert "Estimada Dra. Ficticia:" in self._text()

    def test_origenlab_intro_appears(self) -> None:
        text = self._text()
        assert "Le escribo de OrigenLab" in text

    def test_project_title_in_body(self) -> None:
        assert "Estudio ficticio de suelos" in self._text()

    def test_fund_label_in_body(self) -> None:
        assert "Fondecyt Regular 2025" in self._text()

    def test_equipment_joined(self) -> None:
        assert "con balanzas analíticas y agitadores y otros equipos" in self._text()

    def test_equipment_in_spanish_keeps_brand_capitals(self) -> None:
        text = self._text(equipment=["OHAUS/Adam balances analíticas", "IKA stirrers y homogenizers"])
        assert "con balanzas analíticas, agitadores y homogeneizadores y otros equipos" in text
        assert "(OHAUS, Adam e IKA, entre otras)" in text
        assert "oHAUS" not in text and "iKA" not in text and "stirrers" not in text

    def test_equipment_categories_deduplicated_in_order(self) -> None:
        text = self._text(equipment=["Ortoalresa centrifuges", "OHAUS moisture analyzers",
                                     "Adam Equipment balances", "IKA rotary evaporators o stirrers"])
        assert ("con centrífugas, analizadores de humedad, balanzas, evaporadores rotatorios y "
                "agitadores y otros equipos") in text
        assert "(Ortoalresa, OHAUS, Adam e IKA, entre otras)" in text

    def test_brand_matched_as_a_whole_word(self) -> None:
        text = self._text(equipment=["Balanzas para conservación de muestras"])
        assert "SERVA" not in text.split("entre otras")[0].split("(")[-1]

    def test_unknown_equipment_falls_back(self) -> None:
        text = self._text(equipment=["Equipo ficticio sin categoría"])
        assert "con balanzas, agitadores, centrífugas y otros equipos" in text
        assert "(OHAUS, IKA, Hielscher, entre otras)" in text

    def test_fuente_from_note(self) -> None:
        assert "ResearchGate ficticio" in self._text()

    def test_fuente_from_doi(self) -> None:
        text = self._text(email_source_note=None,
                          email_source_url="https://doi.org/10.9999/x",
                          email_source_year=2024)
        assert "su publicación de 2024" in text

    def test_fuente_fallback(self) -> None:
        text = self._text(email_source_note=None, email_source_url=None)
        assert "un sitio público" in text

    def test_no_project_title(self) -> None:
        text = self._text(project_title=None)
        # With fund_label but no project_title: "Vimos que fue adjudicado en ..."
        assert "fue adjudicado en Fondecyt Regular 2025" in text

    def test_no_project_title_no_fund(self) -> None:
        text = self._text(project_title=None, fund_label=None)
        # No project/fund: starts the lab para directly
        assert "Si en esta etapa está equipando" in text
        assert "fue adjudicado" not in text

    def test_empty_equipment_uses_defaults(self) -> None:
        text = self._text(equipment=[])
        assert "balanzas, agitadores, centrífugas" in text
        assert "(OHAUS, IKA, Hielscher, entre otras)" in text

    def test_gmail_signature_replaces_fallback(self) -> None:
        text = self._text(gmail_signature_html="<p>Tatiana Ficticia<br>OrigenLab</p>")
        assert "Tatiana Ficticia" in text
        # Fallback must NOT appear when a real signature is given
        assert "origenlab.cl" not in text or "Tatiana Ficticia" in text

    def test_fallback_signature_when_no_gmail_sig(self) -> None:
        text = self._text(gmail_signature_html=None)
        assert "OrigenLab · Valdivia, Chile" in text

    def test_unsubscribe_line_present(self) -> None:
        assert "responda «no»" in self._text()

    def test_saludos_cordiales_present(self) -> None:
        assert "Saludos cordiales," in self._text()


# ── build_html ────────────────────────────────────────────────────────────────

class TestBuildHtml:
    def _html(self, **over):
        defaults = dict(
            person_name="Ana Ficticia",
            salutation_sex="MUJER",
            project_title="Estudio ficticio de suelos",
            fund_label="Fondecyt Regular 2025",
            equipment=["Balanza analítica"],
            email_source_note="ResearchGate ficticio",
            email_source_url=None,
            email_source_year=None,
        )
        defaults.update(over)
        return build_html(**defaults)

    def test_is_html(self) -> None:
        html = self._html()
        assert "<html>" in html and "</html>" in html

    def test_saludo_in_html(self) -> None:
        assert "Estimada Dra. Ficticia:" in self._html()

    def test_no_raw_xss_in_title(self) -> None:
        # HTML special chars in project_title must be escaped
        html = self._html(project_title="<script>alert(1)</script>")
        assert "<script>" not in html

    def test_gmail_sig_inserted_raw(self) -> None:
        html = self._html(gmail_signature_html="<p>Firma ficticia</p>")
        assert "<p>Firma ficticia</p>" in html

    def test_fallback_sig_used(self) -> None:
        html = self._html(gmail_signature_html=None)
        assert "OrigenLab" in html


# ── build_mime_message ────────────────────────────────────────────────────────

class TestBuildMimeMessage:
    def _msg(self, **over):
        defaults = dict(
            to="ana@ficticio.invalid",
            subject="Equipamiento ficticio — OrigenLab",
            text_body="Hola Ficticia",
            html_body="<p>Hola Ficticia</p>",
            pdf_bytes=b"%PDF-1.4 ficticio",
        )
        defaults.update(over)
        return build_mime_message(**defaults)

    def test_builds_without_error(self) -> None:
        assert isinstance(self._msg(), bytes)

    def test_recipient_in_headers(self) -> None:
        import email as _email
        msg = _email.message_from_bytes(self._msg())
        assert "ana@ficticio.invalid" in msg["To"]

    def test_has_pdf_attachment(self) -> None:
        import email as _email
        msg = _email.message_from_bytes(self._msg())
        parts = [p for p in msg.walk() if p.get_content_type() == "application/pdf"]
        assert len(parts) == 1
        assert "Catalogo-OrigenLab-2026.pdf" in parts[0].get("Content-Disposition", "")

    def test_invalid_recipient_raises(self) -> None:
        with pytest.raises(ValueError):
            self._msg(to="not-an-email")

    def test_sender_is_contacto(self) -> None:
        import email as _email
        msg = _email.message_from_bytes(self._msg())
        assert "contacto@origenlab.cl" in msg["From"]


# ── load_catalogue_pdf ────────────────────────────────────────────────────────

class TestLoadCataloguePdf:
    def test_returns_bytes_starting_with_pdf_magic(self) -> None:
        data = load_catalogue_pdf()
        assert isinstance(data, bytes)
        assert data[:4] == b"%PDF"

    def test_size_within_large_file_limit(self) -> None:
        data = load_catalogue_pdf()
        # Large-file limit is 2 MB (2048 KB); the PDF is ~571 KB
        assert len(data) < 2 * 1024 * 1024
