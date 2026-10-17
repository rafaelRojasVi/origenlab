"""Products from sent quotes: makers, the manufacturer role, products. Every name and domain is invented."""
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
import import_quote_products as iqp  # noqa: E402

REASON = "desde cotización"


def _product(maker: str, model: str, kind: str = "equipment") -> dict:
    return {"maker": maker, "model_number": model, "name_es": f"Equipo {model}", "product_kind": kind,
            "reason": REASON}


# ------------------------------------------------------------------ plan (no database)

def test_a_maker_becomes_an_organization_its_role_and_its_domain() -> None:
    items = iqp.manifest_items({"makers": [{"name": "Fabricante Uno", "domain": "Fabricante-uno.example"}],
                                "manufacturer_role": [{"name": "Marca Vieja"}],
                                "products": [_product("Fabricante Uno", "FX-1"), _product("Otra Marca", "OM 2")]})
    by_key = {i["key"]: i for i in items}
    assert by_key["org:fabricante uno"]["fields"] == {"name": "Fabricante Uno", "kind": "manufacturer"}
    assert by_key["role:fabricante uno|manufacturer"]["fields"]["organization"] == "org:fabricante uno"
    assert by_key["domain:fabricante uno|fabricante-uno.example"]["fields"]["domain"] == "fabricante-uno.example"
    # Named but not listed as a maker: reused, never created.
    assert by_key["org:marca vieja"]["fields"] == {"name": "Marca Vieja", "must_exist": True}
    assert by_key["org:otra marca"]["fields"] == {"name": "Otra Marca", "must_exist": True}
    assert "role:otra marca|manufacturer" not in by_key
    assert by_key["product:fabricante uno|FX1"]["fields"] == {
        "manufacturer": "org:fabricante uno", "model_number": "FX-1", "name_es": "Equipo FX-1",
        "product_kind": "equipment", "note": REASON}
    assert {i["action"] for i in items} == {"create_org", "add_org_role", "add_org_domain", "create_product"}


def test_a_role_is_planned_once_per_maker() -> None:
    items = iqp.manifest_items({"makers": [{"name": "Fabricante Uno", "domain": None}],
                                "manufacturer_role": [{"name": "fabricante uno"}, {"name": "Marca Vieja"},
                                                      {"name": "MARCA VIEJA"}], "products": []})
    assert sorted(i["key"] for i in items if i["action"] == "add_org_role") == [
        "role:fabricante uno|manufacturer", "role:marca vieja|manufacturer"]


@pytest.mark.parametrize("manifest", [
    {"products": [{**_product("M", "X1"), "unit_price": "100"}]},
    {"products": [{**_product("M", "X1"), "product_kind": "gadget"}]},
    {"products": [{**_product("M", "X1"), "reason": " "}]},
    {"products": [_product("M", "X-1"), _product("M", "X 1")]},
    {"makers": [{"name": "M", "domain": "not a domain"}]},
    {"makers": [{"name": "M", "domain": None}, {"name": "m", "domain": None}]},
    {"makers": [], "products": []},
    {"orgs": []},
], ids=["a-cost", "kind", "no-reason", "same-model-key", "domain-shape", "maker-twice", "nothing", "unknown-section"])
def test_the_plan_refuses_a_bad_manifest(manifest) -> None:
    with pytest.raises(ValueError):
        iqp.manifest_items(manifest)


@pytest.mark.parametrize("manifest", [
    {"makers": [{"name": "Lab Delivery Equipos", "domain": None}]},
    {"products": [{**_product("M", "X1"), "name_es": "Equipo de Labdelivery"}]},
], ids=["maker", "product"])
def test_labdelivery_is_refused(manifest) -> None:
    with pytest.raises(LabdeliveryRefused):
        iqp.manifest_items(manifest)


def test_plan_writes_no_path_and_a_refused_manifest_path_exits_11(tmp_path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"products": [_product("Marca", "SYN-1")]}), encoding="utf-8")
    assert iqp.main(["plan", "--manifest", str(manifest), "--out", str(tmp_path / "o")]) == 0
    text = (tmp_path / "o" / "plan.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in text and json.loads(text)["counts"]["create_product"] == 1

    bad = tmp_path / "labdelivery.json"
    bad.write_text(json.dumps({"products": [_product("Marca", "SYN-1")]}), encoding="utf-8")
    assert iqp.main(["plan", "--manifest", str(bad), "--out", str(tmp_path / "o2")]) == _common.EXIT_REFUSED
    assert not (tmp_path / "o2" / "plan.json").exists()


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


def _plan(tmp_path: Path, manifest: dict) -> tuple[Path, str]:
    src = tmp_path / "manifest.json"
    src.write_text(json.dumps(manifest), encoding="utf-8")
    assert iqp.main(["plan", "--manifest", str(src), "--out", str(tmp_path / "p")]) == 0
    plan = tmp_path / "p" / "plan.json"
    return plan, importing.sha256_bytes(plan.read_bytes())


def _org(dsn: str, name: str, kind: str = "supplier") -> str:
    return _owner(dsn, "insert into crm.organization (kind, name, confirmation) values (%s, %s, 'machine_proposed') "
                       "returning id::text", (kind, name))[0][0]


def _orgs_named(dsn: str, name: str) -> list[tuple]:
    return _owner(dsn, "select id::text, kind from crm.organization where lower(name) = lower(%s)", (name,))


def _roles(dsn: str, org_id: str) -> list[tuple]:
    return _owner(dsn, "select role, valid_to from crm.organization_relationship where organization_id = %s",
                  (org_id,))


@needs_db
def test_create_reuse_already_present_and_the_role_once(disposable_database, operator, tmp_path) -> None:
    dsn, tag = disposable_database, uuid.uuid4().hex[:6]
    new_maker, reused, old_maker = f"Fabricante Nuevo {tag}", f"Marca Existente {tag}", f"Marca Antigua {tag}"
    reused_id, old_id = _org(dsn, reused), _org(dsn, old_maker)
    with_role = _org(dsn, f"Marca Con Rol {tag}")
    _owner(dsn, "insert into crm.organization_relationship (organization_id, role, valid_from) "
                "values (%s, 'manufacturer', '2026-01-01')", (with_role,))
    [(present,)] = _owner(dsn, "insert into catalog.product (manufacturer_organization_id, model_number, name) "
                               "values (%s, 'AB-100', 'ya existe') returning id::text", (old_id,))
    plan, sha = _plan(tmp_path, {
        "makers": [{"name": new_maker, "domain": f"fabricante-{tag}.example"},
                   {"name": reused.upper(), "domain": None}],
        "manufacturer_role": [{"name": old_maker}, {"name": f"Marca Con Rol {tag}"}],
        "products": [_product(new_maker, "NX-1"), _product(reused, "RX 2", "accessory"),
                     _product(old_maker, "AB 100"), _product(f"Marca Con Rol {tag}", "CR-3", "consumable")]})

    assert iqp.main(_argv("apply", dsn, plan, sha, tmp_path / "a1", operator)) == 0
    r1 = _report(tmp_path / "a1", "apply-report.json")["by_action"]
    assert r1["create_org"] == {"inserted": 1, "already_present": 3}
    assert r1["add_org_role"] == {"inserted": 3, "already_present": 1}
    assert r1["add_org_domain"] == {"inserted": 1}
    assert r1["create_product"] == {"inserted": 3, "already_present": 1}

    # The new maker: one organization, the role, the domain.
    [(new_id, kind)] = _orgs_named(dsn, new_maker)
    assert kind == "manufacturer"
    assert _roles(dsn, new_id) == [("manufacturer", None)]
    assert _owner(dsn, "select domain_norm, scope from crm.organization_domain where organization_id = %s",
                  (new_id,)) == [(f"fabricante-{tag}.example", "shared")]
    # The existing maker named in another case: reused, not duplicated, given the role.
    assert _orgs_named(dsn, reused) == [(reused_id, "supplier")]
    assert _roles(dsn, reused_id) == [("manufacturer", None)] and _roles(dsn, old_id) == [("manufacturer", None)]
    assert _roles(dsn, with_role) == [("manufacturer", None)]  # in force already: not opened again
    # The product under an existing model key is untouched.
    assert _owner(dsn, "select count(*), max(name) from catalog.product where manufacturer_organization_id = %s",
                  (old_id,)) == [(1, "ya existe")]
    rows = _owner(dsn, "select model_number, name_es, product_kind, content_origin, created_by_operator_id::text "
                       "from catalog.product where manufacturer_organization_id = %s", (new_id,))
    assert rows == [("NX-1", "Equipo NX-1", "equipment", "import", operator.operator_id)]
    notes = _owner(dsn, "select e.payload->>'note' from crm.domain_event e join catalog.product p on p.id = e.aggregate_id "
                        "where e.event_type = 'product.created' and p.manufacturer_organization_id = any(%s::uuid[])",
                   ([new_id, reused_id, with_role],))
    assert notes == [(REASON,)] * 3
    # No cost is ever written.
    assert _owner(dsn, "select count(*) from catalog.supplier_product sp join catalog.product p on p.id = sp.product_id "
                       "where p.manufacturer_organization_id = any(%s::uuid[])",
                  ([new_id, reused_id, old_id, with_role],)) == [(0,)]

    # A rerun writes nothing: the role is added once.
    assert iqp.main(_argv("apply", dsn, plan, sha, tmp_path / "a2", operator)) == 0
    r2 = _report(tmp_path / "a2", "apply-report.json")
    assert r2["inserted"] == 0 and r2["by_action"]["add_org_role"] == {"already_present": 4}
    assert _roles(dsn, reused_id) == [("manufacturer", None)]
    assert iqp.main(_argv("verify", dsn, plan, sha, tmp_path / "v")) == 0
    assert _report(tmp_path / "v", "verify-report.json")["counts"] == {"present": 13}
    assert present  # the pre-existing product id, kept


@needs_db
def test_rollback_removes_exactly_what_it_created(disposable_database, operator, tmp_path) -> None:
    dsn, tag = disposable_database, uuid.uuid4().hex[:6]
    new_maker, old_maker = f"Fabricante Efímero {tag}", f"Marca Previa {tag}"
    old_id = _org(dsn, old_maker)
    plan, sha = _plan(tmp_path, {"makers": [{"name": new_maker, "domain": f"efimero-{tag}.example"}],
                                 "manufacturer_role": [{"name": old_maker}],
                                 "products": [_product(new_maker, "EF-1"), _product(old_maker, "PV-2")]})
    assert iqp.main(_argv("apply", dsn, plan, sha, tmp_path / "a", operator)) == 0
    [(new_id, _)] = _orgs_named(dsn, new_maker)

    assert iqp.main(_argv("rollback", dsn, plan, sha, tmp_path / "r", None, "--confirm-delete-loaded-rows")) == 0
    deleted = _report(tmp_path / "r", "rollback-report.json")["deleted"]
    assert deleted["crm.organization"] == 1 and deleted["crm.organization_relationship"] == 2
    assert deleted["crm.organization_domain"] == 1 and deleted["catalog.product"] == 2
    assert _orgs_named(dsn, new_maker) == [] and _orgs_named(dsn, old_maker) == [(old_id, "supplier")]
    assert _roles(dsn, old_id) == []
    assert _owner(dsn, "select count(*) from catalog.product where manufacturer_organization_id = any(%s::uuid[])",
                  ([new_id, old_id],)) == [(0,)]
    assert _owner(dsn, "select count(*) from crm.domain_event where aggregate_id = any(%s::uuid[]) "
                       "or payload->>'organization_id' = any(%s)", ([new_id, old_id], [new_id, old_id])) == [(0,)]


@needs_db
def test_a_reused_maker_that_does_not_exist_is_refused_before_anything_is_written(
        disposable_database, operator, tmp_path) -> None:
    dsn, tag = disposable_database, uuid.uuid4().hex[:6]
    plan, sha = _plan(tmp_path, {"makers": [{"name": f"Fabricante Real {tag}", "domain": None}],
                                 "products": [_product(f"Marca Inexistente {tag}", "NO-1")]})
    assert iqp.main(_argv("apply", dsn, plan, sha, tmp_path / "a", operator)) == _common.EXIT_REFUSED
    assert _orgs_named(dsn, f"Fabricante Real {tag}") == [] and _orgs_named(dsn, f"Marca Inexistente {tag}") == []
    assert _owner(dsn, "select count(*) from evidence.source_record where dedupe_key = %s",
                  (importing.manifest_dedupe_key(sha),)) == [(0,)]
