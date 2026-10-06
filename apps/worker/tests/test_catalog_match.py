"""Cheap product candidates: model-number tokens and full-text words, before any model."""

from __future__ import annotations

from origenlab_worker.catalog_match import match_products, model_key, model_tokens, search_words


def test_model_key_matches_the_catalogs_generated_column() -> None:
    assert model_key("ms-h280 pro") == "MSH280PRO"
    assert model_key("UP200Ht") == "UP200HT"


def test_model_tokens_need_letters_and_digits_and_skip_our_numbers_and_units() -> None:
    text = "Cotizar UP200Ht y MS-H280-Pro, 500ml, CN 1234, OC4500123, 2026-10-06, ISO 9001, 220V"
    assert model_tokens(text) == ["UP200HT", "MSH280PRO"]


def test_model_tokens_are_distinct_in_first_seen_order() -> None:
    assert model_tokens("UP200Ht", "up-200ht otra vez, RV10") == ["UP200HT", "RV10"]


def test_search_words_drop_greetings_and_filler() -> None:
    words = search_words("Solicitud de cotización", "Estimados, necesitamos un agitador magnético con calefacción.")
    assert words == ["agitador", "magnetico", "calefaccion"]


class _Cur:
    def __init__(self, by_key, by_text) -> None:
        self.by_key, self.by_text, self.calls, self._rows = by_key, by_text, [], []

    def execute(self, sql, params) -> None:
        self.calls.append(params)
        self._rows = self.by_key if "model_key = any" in sql else self.by_text

    def fetchall(self):
        return self._rows


def test_an_exact_model_match_skips_the_full_text_query() -> None:
    cur = _Cur([("p1", "UP200Ht", "UP200HT", "Homogeneizador")], [])
    found = match_products(cur, "Cotizar UP200Ht", "")
    assert [(c.product_id, c.how) for c in found] == [("p1", "model_key")]
    assert len(cur.calls) == 1


def test_without_a_model_match_the_full_text_index_is_asked_with_an_or_query() -> None:
    cur = _Cur([], [("p2", None, None, "Agitador magnético", 0.25)])
    found = match_products(cur, "Agitador magnético", "con calefacción")
    assert [(c.product_id, c.how, c.evidence) for c in found] == [("p2", "text", "250")]
    assert cur.calls[-1][0] == "agitador | magnetico | calefaccion"
