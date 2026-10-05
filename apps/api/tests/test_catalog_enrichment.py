"""Claude-based Spanish enrichment: the request, the validator, image downloads and the CLI.

No test reaches the Anthropic API or any network: the model is a fake client returning fixed JSON,
and every image download goes through an `httpx.MockTransport` with a fake resolver. Every brand,
model, number and domain is invented (ACME, `.example`).
"""
from __future__ import annotations

import json
import socket
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import psycopg
import pytest

from origenlab_api.v2.catalog import enrichment
from origenlab_api.v2.catalog.keys import LabdeliveryRefused
from origenlab_api.v2.catalog.storage import FakeStorage
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "catalog"
sys.path.insert(0, str(_SCRIPTS))
import enrich_products as ep  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
DATASHEET = """ACME SONIC-100 Ultrasonic Bath
Capacity: 2.5 L
Frequency: 40 kHz
Weight: 3.2 kg
Dimensions: 30 x 18 x 22 cm
Made in Germany
List price: 1,234.00 EUR
Image: https://media.acme.example/img/sonic-100.png
"""


def _product(**extra) -> dict:
    return {"id": str(uuid.uuid4()), "brand": "ACME Instruments", "model_number": "SONIC-100",
            "name": "Ultrasonic bath 2.5 L", "product_kind": "equipment", "category_es": None, **extra}


def _raw(**overrides) -> dict:
    raw = {
        "name_es": "Baño ultrasónico SONIC-100 de 2,5 L",
        "description_es": "Baño ultrasónico de 40 kHz para limpieza de material de laboratorio.",
        "category_es": "Baños ultrasónicos",
        "product_kind": "equipment",
        "specs": [{"label_es": "Capacidad", "value": "2.5", "unit": "L"},
                  {"label_es": "Frecuencia", "value": "40", "unit": "kHz"}],
        "weight_kg": 3.2,
        "dims": {"length_cm": 30, "width_cm": 18, "height_cm": 22},
        "images": [{"url": "https://media.acme.example/img/sonic-100.png", "found_in": "page"}],
    }
    raw.update(overrides)
    return raw


def _validate(raw: dict, *, source: str = DATASHEET, domains=("acme.example",)) -> enrichment.EnrichmentResult:
    return enrichment.validate_response(raw, source_text=enrichment.source_text(_product(), source, None),
                                        manufacturer_domains=domains)


# ------------------------------------------------------------------ the request

def test_request_carries_only_product_data_never_prices_costs_or_client_fields() -> None:
    product = _product(price="98765.43", cost="45678.90", supplier_terms="NET-30-SECRET",
                       client_name="Cliente Secreto Ltda", note="nota interna privada",
                       document_lines=[{"description": "linea de cotizacion", "unit_price": "11223.34"}],
                       list_price="55555.55", discount_pct="0.37")
    request = enrichment.build_request(product, DATASHEET, "Contact sales: price on request USD 999.99")
    body = json.dumps(request, ensure_ascii=False)
    for forbidden in ("98765.43", "45678.90", "NET-30-SECRET", "Cliente Secreto", "nota interna", "linea de cotizacion",
                      "11223.34", "55555.55", "0.37", "1,234.00", "List price", "999.99", "price on request"):
        assert forbidden not in body, forbidden
    for word in ("price", "cost", "precio", "costo", "discount", "descuento"):
        # The system prompt may say what is excluded; the product payload never carries it.
        assert word not in json.dumps(request["messages"], ensure_ascii=False).lower(), word
    assert "SONIC-100" in body and "ACME Instruments" in body and "40 kHz" in body
    assert request["output_config"]["format"]["type"] == "json_schema"


def test_the_default_model_is_haiku_4_5_with_structured_output_and_nothing_it_rejects() -> None:
    assert enrichment.DEFAULT_MODEL == "claude-haiku-4-5-20251001"
    request = enrichment.build_request(_product(), DATASHEET, None)
    assert request["model"] == "claude-haiku-4-5-20251001"
    assert request["output_config"] == {"format": {"type": "json_schema", "schema": enrichment.OUTPUT_SCHEMA}}
    # Haiku 4.5 takes neither adaptive thinking, effort nor the server-side refusal fallback.
    assert not {"thinking", "betas", "fallbacks"} & request.keys()


def test_a_model_override_is_honoured_with_what_that_model_supports() -> None:
    request = enrichment.build_request(_product(), DATASHEET, None, model="claude-sonnet-5-5")
    assert request["model"] == "claude-sonnet-5-5"
    assert request["thinking"] == {"type": "adaptive"} and request["output_config"]["effort"] == "medium"
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert request["fallbacks"] == "default" and request["betas"] == ["server-side-fallback-2026-07-01"]
    with pytest.raises(ValueError):
        enrichment.build_request(_product(), DATASHEET, None, model="gpt-4o")


def test_scrub_removes_commercial_lines_and_counts_them() -> None:
    text, removed = enrichment.scrub_commercial_lines(DATASHEET + "Precio neto: $ 120.000\nDescuento 10%\n")
    assert removed == 3
    assert "1,234.00" not in text and "120.000" not in text and "Capacity: 2.5 L" in text


# ------------------------------------------------------------------ the validator

def test_validator_keeps_what_the_source_states() -> None:
    result = _validate(_raw())
    assert result.name_es == "Baño ultrasónico SONIC-100 de 2,5 L"
    assert result.weight_kg == Decimal("3.2")
    assert (result.length_cm, result.width_cm, result.height_cm) == (Decimal("30"), Decimal("18"), Decimal("22"))
    assert [s["value"] for s in result.specs] == ["2.5", "40"]
    assert all(s["source"] == "machine" for s in result.specs)
    assert result.images == [("https://media.acme.example/img/sonic-100.png", "manufacturer_site")]
    assert result.rejected == []


def test_validator_rejects_an_invented_weight() -> None:
    result = _validate(_raw(weight_kg=4.75))
    assert result.weight_kg is None
    assert "weight_kg" in {r["field"] for r in result.rejected}


def test_validator_rejects_a_converted_dimension_and_an_invented_spec_number() -> None:
    result = _validate(_raw(dims={"length_cm": 300, "width_cm": 18, "height_cm": 22},
                            specs=[{"label_es": "Capacidad", "value": "2.5", "unit": "L"},
                                   {"label_es": "Potencia", "value": "120", "unit": "W"}]))
    assert result.length_cm is None and result.width_cm is None and result.height_cm is None
    assert [s["label_es"] for s in result.specs] == ["Capacidad"]
    fields = {r["field"] for r in result.rejected}
    assert "dims" in fields and "specs[1]" in fields


def test_validator_rejects_a_description_with_an_invented_number() -> None:
    result = _validate(_raw(description_es="Baño de 55 kHz."))
    assert result.description_es is None
    assert "description_es" in {r["field"] for r in result.rejected}


def test_origin_country_is_not_part_of_this_tool() -> None:
    """Ruling I2: a country cannot be verified from the source, so it is never asked for or kept."""
    assert "origin_country" not in enrichment.OUTPUT_SCHEMA["properties"]
    assert "origin_country" not in json.dumps(enrichment.build_request(_product(), DATASHEET, None))
    result = _validate(_raw(origin_country="DE", origin_country_quote="Made in Germany"))
    assert "origin_country" not in result.product_fields()
    assert not hasattr(result, "origin_country")


def test_weight_needs_its_number_next_to_kg_in_the_source() -> None:
    for line in ("Capacity: 2.5 L", "Weight: 2.5 lb", "Weight 2.5", "2.5 kgf force"):
        source = f"ACME SONIC-100\n{line}\n"
        result = _validate(_raw(weight_kg=2.5, dims=None), source=source)
        assert result.weight_kg is None, line
        assert "weight_kg" in {r["field"] for r in result.rejected}
    for line in ("Weight: 2.5 kg", "Peso 2,5 kg", "net weight 2.5kg", "2.5 kilograms"):
        assert _validate(_raw(weight_kg=2.5, dims=None), source=f"ACME\n{line}\n").weight_kg == Decimal("2.5"), line


def test_dimensions_need_cm_or_mm_next_to_their_numbers() -> None:
    dims = {"length_cm": 30, "width_cm": 18, "height_cm": 22}
    ok = ("30 x 18 x 22 cm", "300 x 180 x 220 mm", "L 30 cm, W 18 cm, H 22 cm", "30×18×22cm")
    for line in ok:
        result = _validate(_raw(weight_kg=None, dims=dims), source=f"ACME\n{line}\n")
        assert (result.length_cm, result.width_cm, result.height_cm) == (30, 18, 22), line
    for line in ("30 x 18 x 22 in", "30 x 18 x 22", "Capacity 30 L, 18 kHz, 22 W", "30 x 18 x 22 m"):
        result = _validate(_raw(weight_kg=None, dims=dims), source=f"ACME\n{line}\n")
        assert result.length_cm is None, line


def test_validator_enforces_lengths_kinds_and_spec_count() -> None:
    result = _validate(_raw(name_es="x" * 161, product_kind="robot", description_es="y" * 1201))
    assert result.name_es is None and result.product_kind is None and result.description_es is None
    result = _validate(_raw(specs=[{"label_es": "Capacidad", "value": "2.5", "unit": "L"}] * 31))
    assert len(result.specs) == 30


def test_images_from_a_non_manufacturer_domain_are_dropped() -> None:
    source = DATASHEET + ("https://cdn.reseller.example/sonic.png\nhttp://media.acme.example/a.png\n"
                          "https://acme.example.evil.example/b.png\nhttps://acme.example/c.jpg\n")
    result = _validate(_raw(images=[{"url": "https://cdn.reseller.example/sonic.png", "found_in": "page"},
                                    {"url": "http://media.acme.example/a.png", "found_in": "page"},
                                    {"url": "https://acme.example.evil.example/b.png", "found_in": "page"},
                                    {"url": "https://acme.example/c.jpg", "found_in": "datasheet"},
                                    {"url": "https://acme.example/not-in-source.png", "found_in": "page"}]),
                       source=source)
    assert result.images == [("https://acme.example/c.jpg", "datasheet")]
    assert sum(1 for r in result.rejected if r["field"].startswith("images")) == 4


def test_no_manufacturer_domain_means_no_images() -> None:
    assert _validate(_raw(), domains=()).images == []


def test_labdelivery_in_any_written_string_refuses_the_item() -> None:
    with pytest.raises(LabdeliveryRefused):
        _validate(_raw(description_es="Distribuido por Lab Delivery."))
    with pytest.raises(LabdeliveryRefused):
        _validate(_raw(specs=[{"label_es": "Proveedor", "value": "labdelivery", "unit": None}]))


def test_parse_message_reads_the_json_text_and_refuses_a_refusal() -> None:
    ok = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(_raw()))])
    assert enrichment.parse_message(ok)["name_es"].startswith("Baño")
    refused = SimpleNamespace(stop_reason="refusal", content=[])
    with pytest.raises(enrichment.EnrichmentRefused):
        enrichment.parse_message(refused)
    cut = SimpleNamespace(stop_reason="max_tokens", content=[SimpleNamespace(type="text", text="{")])
    with pytest.raises(enrichment.EnrichmentRefused):
        enrichment.parse_message(cut)


# ------------------------------------------------------------------ image downloads

def _resolver(*ips: str):
    def resolve(host, port, *args, **kwargs):
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]
    return resolve


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_image_downloads_a_png_from_the_manufacturer() -> None:
    client = _client(lambda req: httpx.Response(200, headers={"content-type": "image/png"}, stream=httpx.ByteStream(PNG)))
    data, ctype = ep.fetch_image("https://media.acme.example/a.png", ("acme.example",), client=client,
                                 resolve=_resolver("93.184.216.34"))
    assert data == PNG and ctype == "image/png"


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "192.168.1.5", "169.254.169.254", "::1", "fe80::1", "0.0.0.0"])
def test_fetch_image_refuses_private_loopback_and_link_local_addresses(ip) -> None:
    client = _client(lambda req: pytest.fail("no request may be made"))
    with pytest.raises(ep.ImageFetchRefused):
        ep.fetch_image("https://media.acme.example/a.png", ("acme.example",), client=client, resolve=_resolver(ip))


def test_fetch_image_refuses_redirects_http_and_foreign_hosts() -> None:
    client = _client(lambda req: httpx.Response(302, headers={"location": "https://169.254.169.254/"}))
    with pytest.raises(ep.ImageFetchRefused):
        ep.fetch_image("https://media.acme.example/a.png", ("acme.example",), client=client,
                       resolve=_resolver("93.184.216.34"))
    never = _client(lambda req: pytest.fail("no request may be made"))
    for url in ("http://media.acme.example/a.png", "https://evil.example/a.png", "https://user@acme.example/a.png",
                "https://acme.example:8443/a.png", "https://93.184.216.34/a.png"):
        with pytest.raises(ep.ImageFetchRefused):
            ep.fetch_image(url, ("acme.example",), client=never, resolve=_resolver("93.184.216.34"))


def test_fetch_image_caps_the_stream_and_checks_magic_bytes() -> None:
    big = _client(lambda req: httpx.Response(200, headers={"content-type": "image/png"},
                                             stream=httpx.ByteStream(PNG + b"\x00" * (8 * 1024 * 1024))))
    with pytest.raises(ep.ImageFetchRefused):
        ep.fetch_image("https://acme.example/a.png", ("acme.example",), client=big, resolve=_resolver("93.184.216.34"))
    html = _client(lambda req: httpx.Response(200, headers={"content-type": "image/png"}, stream=httpx.ByteStream(b"<html>hi</html>")))
    with pytest.raises(ep.ImageFetchRefused):
        ep.fetch_image("https://acme.example/a.png", ("acme.example",), client=html, resolve=_resolver("93.184.216.34"))


# ------------------------------------------------------------------ CLI guards (no database)

def test_apply_refuses_a_hosted_or_non_disposable_target(tmp_path, capsys) -> None:
    out = tmp_path / "out"
    hosted = "postgresql://origenlab_api:pw@db.abcdefgh.supabase.co:5432/postgres"
    assert ep.main(["--target-dsn", hosted, "--operator-email", "x@example.test", "--select", "all-missing",
                    "--out", str(out), "--apply"], client=_fail_client(), storage=FakeStorage()) == ep.EXIT_REFUSED
    local = "postgresql://origenlab_api:pw@127.0.0.1:5432/origenlab_dev"
    assert ep.main(["--target-dsn", local, "--operator-email", "x@example.test", "--select", "all-missing",
                    "--out", str(out), "--apply"], client=_fail_client(), storage=FakeStorage()) == ep.EXIT_REFUSED
    assert "pw" not in capsys.readouterr().err


def test_missing_api_key_is_refused_without_a_client(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    local = "postgresql://origenlab_api:pw@127.0.0.1:5432/origenlab_test_0123abcd"
    assert ep.main(["--target-dsn", local, "--operator-email", "x@example.test", "--select", "all-missing",
                    "--out", str(tmp_path / "o")]) == ep.EXIT_REFUSED


def test_select_parsing() -> None:
    assert ep.parse_select("quoted") == ("quoted", ())
    assert ep.parse_select("all-missing") == ("all-missing", ())
    assert ep.parse_select("model_keys:sonic-100, ACME 2") == ("model_keys", ("SONIC100", "ACME2"))
    for bad in ("everything", "model_keys:", "model_keys: , "):
        with pytest.raises(ValueError):
            ep.parse_select(bad)


def test_manufacturer_domain_parsing() -> None:
    parsed = ep.parse_domains(["acme.example", "ACME Instruments=media.acme.example"])
    assert parsed.for_brand("anything") == ("acme.example",)
    assert parsed.for_brand("acme instruments") == ("acme.example", "media.acme.example")
    for bad in ("https://acme.example", "acme", "10.0.0.1", "acme.example/path", "*.acme.example"):
        with pytest.raises(ValueError):
            ep.parse_domains([bad])


# ------------------------------------------------------------------ CLI against a disposable database

class _FakeMessages:
    def __init__(self, answers: dict[str, dict]) -> None:
        self.answers = answers
        self.requests: list[dict] = []

    def create(self, **request):
        self.requests.append(request)
        text = request["messages"][0]["content"]
        for model, raw in self.answers.items():
            if f'"model_number": "{model}"' in text:
                return SimpleNamespace(stop_reason="end_turn",
                                       content=[SimpleNamespace(type="text", text=json.dumps(raw))])
        raise AssertionError("unexpected product")


def _fake_client(answers: dict[str, dict]):
    messages = _FakeMessages(answers)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages), messages=messages)


def _fail_client():
    return _fake_client({})


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


def _owner(dsn: str, sql: str, params: tuple = ()):
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


@pytest.fixture(scope="module")
def operator_email(disposable_database) -> str:
    email = f"sales-{uuid.uuid4().hex[:8]}@example.test"
    _owner(disposable_database, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                                "values (gen_random_uuid(), %s, 'Vendedora Prueba', 'sales', 'active')", (email,))
    return email


def _make_products(dsn: str, *models: str, origin: str = "import", **fields) -> tuple[str, list[str]]:
    maker = f"ACME Instruments {uuid.uuid4().hex[:6]}"
    org = _owner(dsn, "insert into crm.organization (kind, name, confirmation) values ('supplier', %s, "
                      "'machine_proposed') returning id::text", (maker,))[0][0]
    ids = []
    for model in models:
        ids.append(_owner(dsn, "insert into catalog.product (manufacturer_organization_id, model_number, name, "
                               "content_origin, name_es) values (%s, %s, 'Ultrasonic bath 2.5 L', %s, %s) "
                               "returning id::text", (org, model, origin, fields.get("name_es")))[0][0])
    return maker, ids


def _source_dir(tmp_path: Path, *models: str) -> Path:
    d = tmp_path / "sources"
    d.mkdir(exist_ok=True)
    for model in models:
        key = model.replace("-", "").upper()
        (d / f"{key}.datasheet.txt").write_text(DATASHEET.replace("SONIC-100", model), encoding="utf-8")
    return d


def _argv(dsn: str, email: str, tmp_path: Path, select: str, *extra: str) -> list[str]:
    return ["--target-dsn", runtime_dsn(dsn), "--operator-email", email, "--select", select,
            "--out", str(tmp_path / f"out-{uuid.uuid4().hex[:6]}"), *extra]


def _answer(model: str, **overrides) -> dict:
    raw = _raw(**overrides)
    raw["name_es"] = raw["name_es"].replace("SONIC-100", model)
    return raw


def _state(dsn: str, product_id: str) -> tuple:
    return _owner(dsn, "select name_es, description_es, content_origin, version, weight_kg from catalog.product "
                       "where id = %s", (product_id,))[0]


@needs_db
def test_dry_run_writes_nothing(disposable_database, operator_email, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-SECRETKEYVALUE")
    dsn = disposable_database
    model = f"DRY-{uuid.uuid4().hex[:4].upper()}"
    _, [pid] = _make_products(dsn, model)
    before = _state(dsn, pid)
    events_before = _owner(dsn, "select count(*) from crm.domain_event")[0][0]
    client = _fake_client({model: _answer(model)})
    storage = FakeStorage()
    rc = ep.main(_argv(dsn, operator_email, tmp_path, f"model_keys:{model}", "--source-dir",
                       str(_source_dir(tmp_path, model)), "--manufacturer-domain", "acme.example"),
                 client=client, storage=storage, http=_client(lambda r: pytest.fail("dry-run downloads nothing")))
    assert rc == ep.EXIT_OK
    assert _state(dsn, pid) == before
    assert _owner(dsn, "select count(*) from crm.domain_event")[0][0] == events_before
    assert _owner(dsn, "select count(*) from catalog.product_image where product_id = %s", (pid,))[0][0] == 0
    assert storage.puts == 0 and len(client.beta.messages.requests) == 1
    assert client.beta.messages.requests[0]["model"] == "claude-haiku-4-5-20251001"


@needs_db
def test_the_cli_model_flag_is_honoured(disposable_database, operator_email, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-SECRETKEYVALUE")
    dsn = disposable_database
    model = f"MDL-{uuid.uuid4().hex[:4].upper()}"
    _make_products(dsn, model)
    client = _fake_client({model: _answer(model)})
    out = tmp_path / "model"
    argv = ["--target-dsn", runtime_dsn(dsn), "--operator-email", operator_email, "--select", f"model_keys:{model}",
            "--out", str(out), "--source-dir", str(_source_dir(tmp_path, model)), "--model", "claude-sonnet-5-5"]
    assert ep.main(argv, client=client, storage=FakeStorage()) == ep.EXIT_OK
    assert [r["model"] for r in client.beta.messages.requests] == ["claude-sonnet-5-5"]
    assert json.loads((out / "enrich-report.json").read_text(encoding="utf-8"))["model"] == "claude-sonnet-5-5"


def test_a_bad_model_flag_is_refused(tmp_path) -> None:
    local = "postgresql://origenlab_api:pw@127.0.0.1:5432/origenlab_test_0123abcd"
    assert ep.main(["--target-dsn", local, "--operator-email", "x@example.test", "--select", "all-missing",
                    "--out", str(tmp_path / "o"), "--model", "gpt-4o"], client=_fail_client()) == ep.EXIT_REFUSED


@needs_db
def test_apply_writes_machine_content_an_event_and_a_proposed_image(disposable_database, operator_email, tmp_path,
                                                                     monkeypatch, capsys) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-SECRETKEYVALUE")
    dsn = disposable_database
    model = f"APL-{uuid.uuid4().hex[:4].upper()}"
    _, [pid] = _make_products(dsn, model)
    storage = FakeStorage()
    http = _client(lambda req: httpx.Response(200, headers={"content-type": "image/png"}, stream=httpx.ByteStream(PNG)))
    rc = ep.main(_argv(dsn, operator_email, tmp_path, f"model_keys:{model}", "--source-dir",
                       str(_source_dir(tmp_path, model)), "--manufacturer-domain", "acme.example", "--apply"),
                 client=_fake_client({model: _answer(model)}), storage=storage, http=http,
                 resolve=_resolver("93.184.216.34"))
    assert rc == ep.EXIT_OK
    name_es, description_es, origin, version, weight = _state(dsn, pid)
    assert name_es.endswith("de 2,5 L") and description_es and origin == "machine" and version == 2
    assert weight == Decimal("3.200")
    [(etype, payload, actor_kind)] = _owner(dsn, "select event_type, payload, actor_kind from crm.domain_event "
                                                 "where aggregate_id = %s and event_type = 'product.updated'", (pid,))
    assert payload["content_origin"] == "machine" and "name_es" in payload["changed"]
    [(status, creator, source, ctype)] = _owner(dsn, "select status, created_by_operator_id, source, content_type "
                                                     "from catalog.product_image where product_id = %s", (pid,))
    assert (status, creator, source, ctype) == ("proposed", None, "manufacturer_site", "image/png")
    assert storage.puts == 1
    captured = capsys.readouterr()
    assert "SECRETKEYVALUE" not in captured.out + captured.err


@needs_db
def test_operator_content_is_never_overwritten(disposable_database, operator_email, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-SECRETKEYVALUE")
    dsn = disposable_database
    model = f"OPR-{uuid.uuid4().hex[:4].upper()}"
    _, [pid] = _make_products(dsn, model, origin="operator", name_es="Nombre del operador")
    before = _state(dsn, pid)
    client = _fake_client({model: _answer(model)})
    rc = ep.main(_argv(dsn, operator_email, tmp_path, f"model_keys:{model}", "--source-dir",
                       str(_source_dir(tmp_path, model)), "--apply"),
                 client=client, storage=FakeStorage(), http=_client(lambda r: pytest.fail("no image")))
    assert rc == ep.EXIT_OK
    assert _state(dsn, pid) == before
    assert client.beta.messages.requests == []  # never even asked


@needs_db
def test_import_content_is_filled_only_where_empty(disposable_database, operator_email, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-SECRETKEYVALUE")
    dsn = disposable_database
    model = f"IMP-{uuid.uuid4().hex[:4].upper()}"
    _, [pid] = _make_products(dsn, model, name_es="Nombre importado")
    rc = ep.main(_argv(dsn, operator_email, tmp_path, f"model_keys:{model}", "--source-dir",
                       str(_source_dir(tmp_path, model)), "--apply"),
                 client=_fake_client({model: _answer(model)}), storage=FakeStorage(),
                 http=_client(lambda r: pytest.fail("no image without a domain")))
    assert rc == ep.EXIT_OK
    name_es, description_es, origin, _, _ = _state(dsn, pid)
    assert name_es == "Nombre importado" and description_es and origin == "machine"


@needs_db
def test_a_labdelivery_item_is_refused_and_the_others_go_on(disposable_database, operator_email, tmp_path,
                                                             monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-SECRETKEYVALUE")
    dsn = disposable_database
    bad, good = f"BAD-{uuid.uuid4().hex[:4].upper()}", f"GOD-{uuid.uuid4().hex[:4].upper()}"
    _, [bad_id, good_id] = _make_products(dsn, bad, good)
    answers = {bad: _answer(bad, description_es="Importado por Lab-Delivery."), good: _answer(good)}
    out = tmp_path / "lab"
    argv = ["--target-dsn", runtime_dsn(dsn), "--operator-email", operator_email, "--select",
            f"model_keys:{bad},{good}", "--out", str(out), "--source-dir", str(_source_dir(tmp_path, bad, good)),
            "--apply"]
    assert ep.main(argv, client=_fake_client(answers), storage=FakeStorage(),
                   http=_client(lambda r: pytest.fail("no image"))) == ep.EXIT_OK
    assert _state(dsn, bad_id)[2] == "import" and _state(dsn, good_id)[2] == "machine"
    report = json.loads((out / "enrich-report.json").read_text(encoding="utf-8"))
    outcomes = {i["model_key"]: i["outcome"] for i in report["items"]}
    assert outcomes[bad.replace("-", "")] == "refused_labdelivery"
    assert outcomes[good.replace("-", "")] == "updated"


# ------------------------------------------------------------------ fix round 1: downloads

def test_fetch_image_pins_the_connection_to_the_checked_address() -> None:
    seen = {}
    calls = []

    def resolve(host, port, *args, **kwargs):
        calls.append(host)
        # A rebinding resolver: the second answer would be private. There must be no second lookup.
        ip = "93.184.216.34" if len(calls) == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(host=request.url.host, header=request.headers["host"],
                    sni=request.extensions.get("sni_hostname"), encoding=request.headers.get("accept-encoding"))
        return httpx.Response(200, headers={"content-type": "image/png"}, stream=httpx.ByteStream(PNG))

    data, _ = ep.fetch_image("https://media.acme.example/a.png", ("acme.example",), client=_client(handler),
                             resolve=resolve)
    assert data == PNG and calls == ["media.acme.example"]
    assert seen == {"host": "93.184.216.34", "header": "media.acme.example", "sni": "media.acme.example",
                    "encoding": "identity"}


def test_fetch_image_has_a_wall_clock_deadline() -> None:
    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0])
    client = _client(lambda req: httpx.Response(200, headers={"content-type": "image/png"}, stream=httpx.ByteStream(PNG)))
    with pytest.raises(ep.ImageFetchRefused, match="deadline"):
        ep.fetch_image("https://acme.example/a.png", ("acme.example",), client=client,
                       resolve=_resolver("93.184.216.34"), clock=lambda: next(ticks))


def test_fetch_image_does_not_decompress() -> None:
    import gzip

    client = _client(lambda req: httpx.Response(200, headers={"content-type": "image/png", "content-encoding": "gzip"},
                                                stream=httpx.ByteStream(gzip.compress(PNG))))
    with pytest.raises(ep.ImageFetchRefused, match="not_an_image"):
        ep.fetch_image("https://acme.example/a.png", ("acme.example",), client=client,
                       resolve=_resolver("93.184.216.34"))


@pytest.mark.parametrize("ip", ["::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:93.184.216.34",
                                "64:ff9b::a00:1", "64:ff9b::7f00:1", "64:ff9b::a9fe:a9fe", "2002:a00:1::1"])
def test_fetch_image_refuses_ipv4_embedded_in_ipv6(ip) -> None:
    with pytest.raises(ep.ImageFetchRefused, match="non_public_address"):
        ep.fetch_image("https://acme.example/a.png", ("acme.example",),
                       client=_client(lambda r: pytest.fail("no request")), resolve=_resolver(ip))


def test_nat64_of_a_public_address_is_allowed() -> None:
    client = _client(lambda req: httpx.Response(200, headers={"content-type": "image/png"}, stream=httpx.ByteStream(PNG)))
    data, _ = ep.fetch_image("https://acme.example/a.png", ("acme.example",), client=client,
                             resolve=_resolver("64:ff9b::5db8:d822"))
    assert data == PNG


# ------------------------------------------------------------------ fix round 1: storage target

def test_apply_refuses_a_remote_storage_url_without_the_flag(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("ORIGENLAB_V2_STORAGE_URL", "https://abcdefgh.supabase.co")
    monkeypatch.setenv("ORIGENLAB_V2_STORAGE_SECRET_KEY", "sb_secret_TESTONLY")
    local = "postgresql://origenlab_api:pw@127.0.0.1:1/origenlab_test_0123abcd"
    argv = ["--target-dsn", local, "--operator-email", "x@example.test", "--select", "all-missing",
            "--out", str(tmp_path / "o"), "--apply"]
    assert ep.main(argv, client=_fail_client()) == ep.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "--allow-remote-storage" in err and "sb_secret" not in err  # refused before any database access
    with pytest.raises(ep._common.Refused, match="allow-remote-storage"):
        ep._storage_from_env(allow_remote=False)
    assert ep._storage_from_env(allow_remote=True) is not None
    monkeypatch.setenv("ORIGENLAB_V2_STORAGE_URL", "http://127.0.0.1:54321")
    assert ep._storage_from_env(allow_remote=False) is not None


# ------------------------------------------------------------------ fix round 1: failures mid-run

class _BrokenStorage(FakeStorage):
    def __init__(self, broken_for: str) -> None:
        super().__init__()
        self.broken_for = broken_for

    def put_if_absent(self, path, data, content_type):
        if self.broken_for in path:
            from origenlab_api.v2.catalog.storage import StorageUnavailable
            raise StorageUnavailable("storage upload answered HTTP 503")
        super().put_if_absent(path, data, content_type)


def _two(dsn, tmp_path, prefix):
    a, b = f"{prefix}A-{uuid.uuid4().hex[:4].upper()}", f"{prefix}B-{uuid.uuid4().hex[:4].upper()}"
    _, ids = _make_products(dsn, a, b)
    return (a, b), ids


def _run(dsn, email, tmp_path, models, out, **kwargs):
    argv = ["--target-dsn", runtime_dsn(dsn), "--operator-email", email, "--select",
            f"model_keys:{','.join(models)}", "--out", str(out), "--source-dir", str(_source_dir(tmp_path, *models)),
            "--manufacturer-domain", "acme.example", "--apply"]
    return ep.main(argv, client=_fake_client({m: _answer(m) for m in models}),
                   http=_client(lambda req: httpx.Response(200, headers={"content-type": "image/png"}, stream=httpx.ByteStream(PNG))),
                   resolve=_resolver("93.184.216.34"), **kwargs)


def _report_items(out):
    report = json.loads((out / "enrich-report.json").read_text(encoding="utf-8"))
    return report, {i["model_key"]: i for i in report["items"]}


@needs_db
def test_a_storage_failure_fails_only_its_item(disposable_database, operator_email, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-SECRETKEYVALUE")
    dsn = disposable_database
    (a, b), (a_id, b_id) = _two(dsn, tmp_path, "STO")
    out = tmp_path / "storage"
    assert _run(dsn, operator_email, tmp_path, (a, b), out, storage=_BrokenStorage(a_id)) == ep.EXIT_ITEM_FAILED
    _, items = _report_items(out)
    assert items[a.replace("-", "")]["outcome"] == "failed"
    assert items[a.replace("-", "")]["detail"] == "StorageUnavailable"
    assert items[b.replace("-", "")]["outcome"] == "updated"
    assert _state(dsn, a_id)[2] == "import" and _state(dsn, b_id)[2] == "machine"


@needs_db
def test_a_source_read_failure_fails_only_its_item(disposable_database, operator_email, tmp_path,
                                                   monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-SECRETKEYVALUE")
    dsn = disposable_database
    (a, b), (a_id, b_id) = _two(dsn, tmp_path, "SRC")
    real = ep._read_source

    def broken(directory, key, kind):
        if key == a.replace("-", ""):
            raise PermissionError(13, "Permission denied", str(directory / f"{key}.{kind}.txt"))
        return real(directory, key, kind)

    monkeypatch.setattr(ep, "_read_source", broken)
    out = tmp_path / "source"
    assert _run(dsn, operator_email, tmp_path, (a, b), out, storage=FakeStorage()) == ep.EXIT_ITEM_FAILED
    _, items = _report_items(out)
    assert items[a.replace("-", "")] ["outcome"] == "failed"
    assert items[a.replace("-", "")]["detail"] == "PermissionError"
    assert items[b.replace("-", "")]["outcome"] == "updated"
    assert _state(dsn, b_id)[2] == "machine"


@needs_db
def test_a_refusal_mid_run_still_writes_the_report(disposable_database, operator_email, tmp_path,
                                                   monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-SECRETKEYVALUE")
    dsn = disposable_database
    (a, b), (a_id, _) = _two(dsn, tmp_path, "REF")
    real = ep.propose

    def refusing(client, product, *args, **kwargs):
        if product["model_key"] == b.replace("-", ""):
            raise ep._common.Refused("the target stopped being what it was")
        return real(client, product, *args, **kwargs)

    monkeypatch.setattr(ep, "propose", refusing)
    out = tmp_path / "refused"
    assert _run(dsn, operator_email, tmp_path, (a, b), out, storage=FakeStorage()) == ep.EXIT_REFUSED
    report, items = _report_items(out)
    assert report["refused"] == "the target stopped being what it was"
    assert items[a.replace("-", "")]["outcome"] == "updated" and b.replace("-", "") not in items


@needs_db
def test_origin_country_is_never_written(disposable_database, operator_email, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-SECRETKEYVALUE")
    dsn = disposable_database
    model = f"CTY-{uuid.uuid4().hex[:4].upper()}"
    _, [pid] = _make_products(dsn, model)
    answers = {model: _answer(model, origin_country="DE", origin_country_quote="Made in Germany")}
    argv = ["--target-dsn", runtime_dsn(dsn), "--operator-email", operator_email, "--select", f"model_keys:{model}",
            "--out", str(tmp_path / "cty"), "--source-dir", str(_source_dir(tmp_path, model)), "--apply"]
    assert ep.main(argv, client=_fake_client(answers), storage=FakeStorage()) == ep.EXIT_OK
    assert _owner(dsn, "select content_origin, origin_country from catalog.product where id = %s",
                  (pid,)) == [("machine", None)]
