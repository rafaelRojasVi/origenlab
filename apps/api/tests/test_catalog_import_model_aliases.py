"""The model-alias importer: models as printed, from a private manifest. Every name and number is invented."""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import psycopg
import pytest

from origenlab_api.v2.catalog import importing
from origenlab_api.v2.catalog.keys import LabdeliveryRefused
from origenlab_api.v2.identity import OperatorIdentity
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "catalog"
sys.path.insert(0, str(_SCRIPTS))
import _common  # noqa: E402
import import_model_aliases as ima  # noqa: E402

ENTRY = {"maker": "Fabricante Ejemplo", "model_number": "30000001", "alias": "XQ 100",
         "reason": "printed on a quote line"}


# ------------------------------------------------------------------ plan (no database)

def test_an_entry_becomes_one_item_keyed_by_maker_model_and_alias_key() -> None:
    [item] = ima.alias_items({"aliases": [ENTRY]})
    assert item["action"] == "add_model_alias"
    assert item["key"] == "alias:fabricante ejemplo:30000001:XQ100"
    assert item["fields"] == ENTRY


def test_the_same_alias_printed_two_ways_is_one_item() -> None:
    plan_items = ima.alias_items({"aliases": [ENTRY, {**ENTRY, "alias": "XQ-100"}]})
    assert len({i["key"] for i in plan_items}) == 1


@pytest.mark.parametrize("bad", [
    {**ENTRY, "maker": " "},
    {**ENTRY, "model_number": None},
    {**ENTRY, "alias": "-./"},
    {**ENTRY, "alias": "X" * 121},
    {**ENTRY, "alias": "3000-0001"},
    {**ENTRY, "reason": 7},
], ids=["no-maker", "no-model", "no-key", "too-long", "own-model", "bad-reason"])
def test_the_plan_refuses_a_bad_entry(bad) -> None:
    with pytest.raises(ValueError):
        ima.alias_items({"aliases": [bad]})


def test_one_maker_never_maps_one_alias_to_two_models() -> None:
    with pytest.raises(ValueError, match="two models"):
        ima.alias_items({"aliases": [ENTRY, {**ENTRY, "model_number": "30000002"}]})
    # Two makers may share a printed code.
    assert len(ima.alias_items({"aliases": [ENTRY, {**ENTRY, "maker": "Otro Fabricante", "model_number": "9"}]})) == 2


def test_labdelivery_is_refused() -> None:
    with pytest.raises(LabdeliveryRefused):
        ima.alias_items({"aliases": [{**ENTRY, "reason": "visto en Labdelivery"}]})


def test_plan_writes_no_path(tmp_path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"aliases": [ENTRY]}), encoding="utf-8")
    assert ima.main(["plan", "--manifest", str(manifest), "--out", str(tmp_path / "o")]) == 0
    text = (tmp_path / "o" / "plan.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in text and json.loads(text)["counts"]["add_model_alias"] == 1


def test_exit_code_for_a_refused_manifest(tmp_path) -> None:
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"documents": []}), encoding="utf-8")
    assert ima.main(["plan", "--manifest", str(manifest), "--out", str(tmp_path / "o")]) == _common.EXIT_REFUSED


# ------------------------------------------------------------------ apply / verify / rollback

@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


def _owner(dsn: str, sql: str, params: tuple = ()):
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


@pytest.fixture(scope="module")
def operator(disposable_database) -> OperatorIdentity:
    email = f"sales-{uuid.uuid4().hex[:8]}@example.test"
    op_id = _owner(disposable_database,
                   "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                   "values (gen_random_uuid(), %s, 'Vendedora Prueba', 'sales', 'active') returning id::text",
                   (email,))[0][0]
    return OperatorIdentity(operator_id=op_id, email_norm=email, display_name="Vendedora Prueba", role="sales",
                            status="active")


def _argv(cmd, dsn, plan, sha, out, operator=None, *extra):
    argv = [cmd, "--target-dsn", runtime_dsn(dsn), "--plan", str(plan), "--plan-sha256", sha, "--out", str(out)]
    if cmd in ("apply", "rollback"):
        argv += ["--admin-dsn", dsn]
    if operator is not None:
        argv += ["--operator-email", operator.email_norm]
    return argv + list(extra)


def _report(out: Path, name: str) -> dict:
    return json.loads((out / name).read_text(encoding="utf-8"))


def _plan(tmp_path: Path, entries: list[dict], name: str = "p") -> tuple[Path, str]:
    manifest = tmp_path / f"{name}.json"
    manifest.write_text(json.dumps({"aliases": entries}), encoding="utf-8")
    assert ima.main(["plan", "--manifest", str(manifest), "--out", str(tmp_path / name)]) == 0
    plan = tmp_path / name / "plan.json"
    return plan, importing.sha256_bytes(plan.read_bytes())


@needs_db
def test_apply_verify_rollback(disposable_database, operator, tmp_path) -> None:
    dsn = disposable_database
    suffix = uuid.uuid4().hex[:6]
    maker_name, other_name, twin_name = (f"Fabricante Ejemplo {suffix}", f"Otro Fabricante {suffix}",
                                         f"Gemelo Ejemplo {suffix}")
    [(maker,)] = _owner(dsn, "insert into crm.organization (kind, name, confirmation) values "
                             "('manufacturer', %s, 'machine_proposed') returning id::text", (maker_name,))
    for name in (twin_name, twin_name):  # two organizations with one name: never guessed
        _owner(dsn, "insert into crm.organization (kind, name, confirmation) values "
                    "('manufacturer', %s, 'machine_proposed')", (name,))
    [(other,)] = _owner(dsn, "insert into crm.organization (kind, name, confirmation) values "
                             "('manufacturer', %s, 'machine_proposed') returning id::text", (other_name,))
    [(balance,)] = _owner(dsn, "insert into catalog.product (manufacturer_organization_id, model_number, name) "
                               "values (%s, '30000001', 'Balanza') returning id::text", (maker,))
    [(holder,)] = _owner(dsn, "insert into catalog.product (manufacturer_organization_id, model_number, name) "
                              "values (%s, 'XQ-200', 'Soporte') returning id::text", (maker,))
    [(foreign,)] = _owner(dsn, "insert into catalog.product (manufacturer_organization_id, model_number, name) "
                               "values (%s, '77', 'Otro') returning id::text", (other,))
    entries = [
        {"maker": maker_name.upper(), "model_number": "30000001", "alias": "XQ 100", "reason": "quote line"},
        {"maker": maker_name, "model_number": "30000001", "alias": "XQ200", "reason": "a model of the maker"},
        {"maker": maker_name, "model_number": "3000-0001", "alias": "XQ101", "reason": "not exact"},
        {"maker": twin_name, "model_number": "1", "alias": "TW1", "reason": "two makers of that name"},
        {"maker": other_name, "model_number": "77", "alias": "XQ100", "reason": "another maker, same print"},
    ]
    plan, sha = _plan(tmp_path, entries)

    assert ima.main(_argv("verify", dsn, plan, sha, tmp_path / "v0")) == _common.EXIT_MISMATCH
    assert _report(tmp_path / "v0", "verify-report.json")["counts"] == {
        "missing": 2, "present_different": 1, "unmatched": 2}

    assert ima.main(_argv("apply", dsn, plan, sha, tmp_path / "a1", operator)) == 0
    r1 = _report(tmp_path / "a1", "apply-report.json")
    assert r1["inserted"] == 2 and r1["present_different"] == 1
    assert r1["by_action"]["add_model_alias"].get("unmatched") == 2
    assert ima.main(_argv("apply", dsn, plan, sha, tmp_path / "a2", operator)) == 0
    r2 = _report(tmp_path / "a2", "apply-report.json")
    assert r2["inserted"] == 0 and r2["already_present"] == 2

    assert ima.main(_argv("verify", dsn, plan, sha, tmp_path / "v1")) == 0
    assert _report(tmp_path / "v1", "verify-report.json")["counts"] == {
        "present": 2, "present_different": 1, "unmatched": 2}
    rows = _owner(dsn, "select product_id::text, alias, alias_key, source, created_by_operator_id::text, "
                       "origin_source_record_id is not null from catalog.product_model_alias order by alias_key, alias")
    assert rows == [(balance, "XQ 100", "XQ100", "import", operator.operator_id, True),
                    (foreign, "XQ100", "XQ100", "import", operator.operator_id, True)]
    assert holder not in {r[0] for r in rows}
    events = _owner(dsn, "select aggregate_id::text, payload->>'alias_key', payload->>'reason' from crm.domain_event "
                         "where event_type = 'product.model_alias_added' order by aggregate_id")
    assert sorted(events) == sorted([(balance, "XQ100", "quote line"), (foreign, "XQ100", "another maker, same print")])

    assert ima.main(_argv("rollback", dsn, plan, sha, tmp_path / "r", None, "--confirm-delete-loaded-rows")) == 0
    assert _owner(dsn, "select count(*) from catalog.product_model_alias") == [(0,)]
    assert _owner(dsn, "select count(*) from crm.domain_event where event_type = 'product.model_alias_added'") == [(0,)]


@needs_db
def test_a_retired_alias_is_never_brought_back_and_blocks_rollback(disposable_database, operator, tmp_path) -> None:
    dsn = disposable_database
    maker_name = f"Fabricante Retiro {uuid.uuid4().hex[:6]}"
    [(maker,)] = _owner(dsn, "insert into crm.organization (kind, name, confirmation) values "
                             "('manufacturer', %s, 'machine_proposed') returning id::text", (maker_name,))
    _owner(dsn, "insert into catalog.product (manufacturer_organization_id, model_number, name) "
                "values (%s, '50000005', 'Balanza') returning id", (maker,))
    plan, sha = _plan(tmp_path, [{"maker": maker_name, "model_number": "50000005", "alias": "ZQ5"}])
    assert ima.main(_argv("apply", dsn, plan, sha, tmp_path / "a1", operator)) == 0
    _owner(dsn, "update catalog.product_model_alias set retired_at = now(), retired_by_operator_id = %s "
                "where alias_key = 'ZQ5'", (operator.operator_id,))
    assert ima.main(_argv("apply", dsn, plan, sha, tmp_path / "a2", operator)) == 0
    assert _report(tmp_path / "a2", "apply-report.json")["kept_later_value"] == 1
    assert ima.main(_argv("verify", dsn, plan, sha, tmp_path / "v")) == 0
    assert _report(tmp_path / "v", "verify-report.json")["counts"] == {"superseded_later": 1}
    assert ima.main(_argv("rollback", dsn, plan, sha, tmp_path / "r", None,
                          "--confirm-delete-loaded-rows")) == _common.EXIT_REFUSED
    assert _owner(dsn, "select count(*) from catalog.product_model_alias where alias_key = 'ZQ5'") == [(1,)]
