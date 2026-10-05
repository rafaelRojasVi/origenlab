"""Catalog importer apply / verify / rollback against a disposable PostgreSQL. Every name and number is invented.

The catalog rows are written as the real `origenlab_api` login; the manifest (which that role may
not insert) and the rollback go through the maintenance login of the same disposable database.
"""
from __future__ import annotations

import json
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import openpyxl
import psycopg
import pytest

from origenlab_api.v2.catalog import importing
from origenlab_api.v2.catalog.commands import V2CatalogRepository
from origenlab_api.v2.catalog.routes import RecordSupplierCostBody, UpdateProductBody
from origenlab_api.v2.commands import request_digest
from origenlab_api.v2.identity import OperatorIdentity
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "catalog"
sys.path.insert(0, str(_SCRIPTS))
import _common  # noqa: E402
import import_price_lists as ipl  # noqa: E402


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


def _loser_plan(tmp_path: Path, name: str, lines: list[tuple[str, str, str]], maker: str) -> tuple[Path, str]:
    """A synthetic Löser-layout text list for one invented manufacturer, planned into tmp_path/name."""
    src = tmp_path / f"{name}.txt"
    src.write_text("".join(f"{n} {desc} {item} {price} €\n" for n, (desc, item, price) in enumerate(lines, 1)),
                   encoding="utf-8")
    out = tmp_path / name
    assert ipl.main(["plan", "--loser-text", str(src), "--as-of", "loser=2024-01-01",
                     "--org-name", f"loser={maker}", "--out", str(out)]) == 0
    data = (out / "plan.json").read_bytes()
    return out / "plan.json", importing.sha256_bytes(data)


def _argv(cmd: str, dsn: str, plan: Path, sha: str, out: Path, operator: OperatorIdentity | None = None,
          *extra: str) -> list[str]:
    argv = [cmd, "--target-dsn", runtime_dsn(dsn), "--plan", str(plan), "--plan-sha256", sha, "--out", str(out)]
    if cmd in ("apply", "rollback"):
        argv += ["--admin-dsn", dsn]
    if operator is not None:
        argv += ["--operator-email", operator.email_norm]
    return argv + list(extra)


def _report(out: Path, name: str) -> dict:
    return json.loads((out / name).read_text(encoding="utf-8"))


def _manifest_id(dsn: str, sha: str) -> str | None:
    rows = _owner(dsn, "select id::text from evidence.source_record where dedupe_key = %s", (f"catalog-import:{sha}",))
    return rows[0][0] if rows else None


def _catalog_command(dsn: str, operator: OperatorIdentity, command: str, body) -> dict:
    repo = V2CatalogRepository(psycopg.connect, runtime_dsn(dsn))
    return repo.execute(command_name=command, operator=operator, fields=body.model_dump(mode="json"),
                        idempotency_key=uuid.uuid4().hex, digest=request_digest(command, body))


def _maker(tag: str) -> str:
    return f"ACME {tag} {uuid.uuid4().hex[:6]}"


# ------------------------------------------------------------------ apply / verify

@needs_db
def test_apply_is_idempotent(disposable_database, operator, tmp_path) -> None:
    dsn = disposable_database
    plan, sha = _loser_plan(tmp_path, "a", [("Osmometer", "ACME-1", "1.000,00"), ("Printer", "ACME-2", "300,50")],
                            _maker("Alpha"))
    first, second = tmp_path / "apply1", tmp_path / "apply2"
    assert ipl.main(_argv("apply", dsn, plan, sha, first, operator)) == 0
    r1 = _report(first, "apply-report.json")
    assert r1["inserted"] == 1 + 2 + 2 and r1["already_present"] == 0
    assert ipl.main(_argv("apply", dsn, plan, sha, second, operator)) == 0
    r2 = _report(second, "apply-report.json")
    assert r2["inserted"] == 0 and r2["already_present"] == 5
    assert ipl.main(_argv("verify", dsn, plan, sha, tmp_path / "verify")) == 0
    assert _report(tmp_path / "verify", "verify-report.json")["counts"] == {"present": 5}

    manifest = _manifest_id(dsn, sha)
    [(kind, status, payload)] = _owner(dsn, "select kind, review_status, payload from evidence.source_record "
                                            "where id = %s", (manifest,))
    assert kind == "migration_manifest" and payload["plan_sha256"] == sha and payload["importer"] == "price_lists"
    products = _owner(dsn, "select p.content_origin, p.created_by_operator_id::text, sp.origin_source_record_id::text, "
                           "sp.price, sp.currency, sp.recorded_by_operator_id::text "
                           "from catalog.product p join catalog.supplier_product sp on sp.product_id = p.id "
                           "where sp.origin_source_record_id = %s order by sp.price", (manifest,))
    assert [(p[0], p[1], p[2]) for p in products] == [("import", operator.operator_id, manifest)] * 2
    assert [(p[3], p[4], p[5]) for p in products] == [(Decimal("300.500000"), "EUR", operator.operator_id),
                                                      (Decimal("1000.000000"), "EUR", operator.operator_id)]
    events = _owner(dsn, "select event_type, count(*) from crm.domain_event "
                         "where payload->>'origin_source_record_id' = %s or aggregate_id = %s::uuid "
                         "group by event_type order by 1", (manifest, manifest))
    assert events == [("organization.created", 1), ("product.cost_recorded", 2), ("product.created", 2),
                      ("source_record.migration_manifest_recorded", 1)]
    actors = _owner(dsn, "select distinct actor_kind, actor_operator_id::text from crm.domain_event "
                         "where payload->>'origin_source_record_id' = %s", (manifest,))
    assert actors == [("operator", operator.operator_id)]


@needs_db
def test_verify_before_apply_is_a_mismatch(disposable_database, operator, tmp_path) -> None:
    dsn = disposable_database
    plan, sha = _loser_plan(tmp_path, "v", [("Osmometer", "ACME-3", "10,00")], _maker("Verify"))
    assert ipl.main(_argv("verify", dsn, plan, sha, tmp_path / "verify")) == _common.EXIT_MISMATCH
    assert _report(tmp_path / "verify", "verify-report.json")["counts"] == {"missing": 3}


@needs_db
def test_apply_skips_an_existing_organization_by_normalised_name(disposable_database, operator, tmp_path) -> None:
    dsn = disposable_database
    maker = _maker("Norm")
    existing = _owner(dsn, "insert into crm.organization (kind, name, confirmation) values ('supplier', %s, "
                           "'machine_proposed') returning id::text", (maker.upper() + "  ",))[0][0]
    plan, sha = _loser_plan(tmp_path, "n", [("Osmometer", "ACME-4", "10,00")], maker)
    assert ipl.main(_argv("apply", dsn, plan, sha, tmp_path / "apply", operator)) == 0
    report = _report(tmp_path / "apply", "apply-report.json")
    assert report["by_action"]["create_org"] == {"inserted": 0, "already_present": 1}
    [(owner_org,)] = _owner(dsn, "select manufacturer_organization_id::text from catalog.product "
                                 "where model_key = 'ACME4' and manufacturer_organization_id = %s", (existing,))
    assert owner_org == existing


@needs_db
def test_apply_uses_a_provided_organization_id(disposable_database, operator, tmp_path) -> None:
    dsn = disposable_database
    other = _owner(dsn, "insert into crm.organization (kind, name, confirmation) values ('supplier', %s, "
                        "'machine_proposed') returning id::text", (_maker("Elsewhere"),))[0][0]
    maker = _maker("Mapped")
    src = tmp_path / "m.txt"
    src.write_text("1 Osmometer ACME-10 10,00 €\n", encoding="utf-8")
    out = tmp_path / "m"
    assert ipl.main(["plan", "--loser-text", str(src), "--as-of", "loser=2024-01-01", "--org-name",
                     f"loser={maker}", "--org-id", f"{maker}={other}", "--out", str(out)]) == 0
    plan = out / "plan.json"
    sha = importing.sha256_bytes(plan.read_bytes())
    assert ipl.main(_argv("apply", dsn, plan, sha, tmp_path / "a", operator)) == 0
    assert _owner(dsn, "select manufacturer_organization_id::text from catalog.product where model_key = 'ACME10'") \
        == [(other,)]


@needs_db
def test_apply_refuses_an_ambiguous_name_before_writing(disposable_database, operator, tmp_path) -> None:
    dsn = disposable_database
    maker = _maker("Twice")
    for spelling in (maker, maker.lower()):
        _owner(dsn, "insert into crm.organization (kind, name, confirmation) values ('supplier', %s, "
                    "'machine_proposed')", (spelling,))
    plan, sha = _loser_plan(tmp_path, "amb", [("Osmometer", "ACME-11", "10,00")], maker)
    assert ipl.main(_argv("apply", dsn, plan, sha, tmp_path / "a", operator)) == _common.EXIT_REFUSED
    assert _manifest_id(dsn, sha) is None


@needs_db
def test_apply_refuses_an_unknown_operator(disposable_database, tmp_path) -> None:
    dsn = disposable_database
    plan, sha = _loser_plan(tmp_path, "u", [("Osmometer", "ACME-5", "10,00")], _maker("Unknown"))
    argv = _argv("apply", dsn, plan, sha, tmp_path / "apply") + ["--operator-email", "nobody@example.test"]
    assert ipl.main(argv) == _common.EXIT_REFUSED
    assert _manifest_id(dsn, sha) is None


@needs_db
def test_cleanroom_flag_refuses_a_disposable_database(disposable_database, operator, tmp_path) -> None:
    dsn = disposable_database
    plan, sha = _loser_plan(tmp_path, "c", [("Osmometer", "ACME-6", "10,00")], _maker("Clean"))
    argv = _argv("apply", dsn, plan, sha, tmp_path / "apply", operator, "--allow-cleanroom-production")
    assert ipl.main(argv) == _common.EXIT_REFUSED
    assert _manifest_id(dsn, sha) is None


# ------------------------------------------------------------------ rollback

@needs_db
def test_rollback_of_a_leaves_b_untouched(disposable_database, operator, tmp_path) -> None:
    dsn = disposable_database
    plan_a, sha_a = _loser_plan(tmp_path, "ra", [("Osmometer", "ACME-1", "100,00")], _maker("RollA"))
    plan_b, sha_b = _loser_plan(tmp_path, "rb", [("Osmometer", "ACME-1", "200,00")], _maker("RollB"))
    assert ipl.main(_argv("apply", dsn, plan_a, sha_a, tmp_path / "aa", operator)) == 0
    assert ipl.main(_argv("apply", dsn, plan_b, sha_b, tmp_path / "ab", operator)) == 0
    manifest_a, manifest_b = _manifest_id(dsn, sha_a), _manifest_id(dsn, sha_b)
    b_before = _owner(dsn, "select count(*) from catalog.supplier_product where origin_source_record_id = %s",
                      (manifest_b,))

    assert ipl.main(_argv("rollback", dsn, plan_a, sha_a, tmp_path / "rolla", None,
                          "--confirm-delete-loaded-rows")) == 0
    report = _report(tmp_path / "rolla", "rollback-report.json")
    assert report["deleted"] == {"catalog.product": 1, "catalog.supplier_product": 1, "crm.domain_event": 4,
                                 "crm.organization": 1, "evidence.source_record": 1}
    assert _manifest_id(dsn, sha_a) is None
    assert _owner(dsn, "select count(*) from crm.domain_event where payload->>'origin_source_record_id' = %s "
                       "or aggregate_id = %s::uuid", (manifest_a, manifest_a)) == [(0,)]
    assert _owner(dsn, "select count(*) from catalog.supplier_product where origin_source_record_id = %s",
                  (manifest_b,)) == b_before
    assert ipl.main(_argv("verify", dsn, plan_b, sha_b, tmp_path / "vb")) == 0
    assert ipl.main(_argv("verify", dsn, plan_a, sha_a, tmp_path / "va")) == _common.EXIT_MISMATCH
    # Rolled back, the same plan applies again cleanly.
    assert ipl.main(_argv("apply", dsn, plan_a, sha_a, tmp_path / "aa2", operator)) == 0
    assert _report(tmp_path / "aa2", "apply-report.json")["inserted"] == 3


@needs_db
def test_rollback_removes_terms_it_set_on_a_preexisting_supplier(disposable_database, operator, tmp_path) -> None:
    dsn = disposable_database
    maker = _maker("Terms")
    existing = _owner(dsn, "insert into crm.organization (kind, name, confirmation) values ('supplier', %s, "
                           "'machine_proposed') returning id::text", (maker,))[0][0]
    _owner(dsn, "insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, "
                "payload, actor_kind) values ('organization', %s, 1, 'organization.created', 1, '{}', 'migrator')",
           (existing,))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["ID del producto", "SKU", "UPC", "Nombre", "Capacidad", "Legibilidad", "Tamaño del plato",
               "Precio de lista", "Precio MAP", "Precio del distribuidor"])
    ws.append(["1", "ACME 12e", "0", "Balanzas ficticias", "1g", "1g", "1mm", "$100.00", "$90.00", "$60.00"])
    wb.save(tmp_path / "adam.xlsx")
    out = tmp_path / "t"
    assert ipl.main(["plan", "--adam", str(tmp_path / "adam.xlsx"), "--as-of", "adam=2026-08-20", "--org-name",
                     f"adam={maker}", "--out", str(out)]) == 0
    plan = out / "plan.json"
    sha = importing.sha256_bytes(plan.read_bytes())
    assert ipl.main(_argv("apply", dsn, plan, sha, tmp_path / "a", operator)) == 0
    assert _owner(dsn, "select map_enforced from catalog.supplier_terms where supplier_organization_id = %s",
                  (existing,)) == [(True,)]
    assert ipl.main(_argv("rollback", dsn, plan, sha, tmp_path / "r", None, "--confirm-delete-loaded-rows")) == 0
    deleted = _report(tmp_path / "r", "rollback-report.json")["deleted"]
    assert deleted["catalog.supplier_terms"] == 1 and "crm.organization" not in deleted
    assert _owner(dsn, "select count(*) from crm.organization where id = %s", (existing,)) == [(1,)]
    assert _owner(dsn, "select count(*) from catalog.supplier_terms where supplier_organization_id = %s",
                  (existing,)) == [(0,)]


@needs_db
def test_rollback_refused_when_a_later_manifest_uses_its_organization(disposable_database, operator,
                                                                      tmp_path) -> None:
    dsn = disposable_database
    maker = _maker("Shared")
    plan_a, sha_a = _loser_plan(tmp_path, "sa", [("Osmometer", "ACME-1", "100,00")], maker)
    plan_b, sha_b = _loser_plan(tmp_path, "sb", [("Printer", "ACME-2", "50,00")], maker)
    assert ipl.main(_argv("apply", dsn, plan_a, sha_a, tmp_path / "aa", operator)) == 0
    assert ipl.main(_argv("apply", dsn, plan_b, sha_b, tmp_path / "ab", operator)) == 0
    assert ipl.main(_argv("rollback", dsn, plan_a, sha_a, tmp_path / "ra", None,
                          "--confirm-delete-loaded-rows")) == _common.EXIT_REFUSED
    blockers = _report(tmp_path / "ra", "rollback-report.json")["blockers"]
    assert any(b["table"] == "crm.organization" and "catalog.product" in b["reason"] for b in blockers)
    assert _manifest_id(dsn, sha_a) is not None
    assert ipl.main(_argv("verify", dsn, plan_a, sha_a, tmp_path / "va")) == 0


@needs_db
def test_rollback_refused_when_a_loaded_product_changed(disposable_database, operator, tmp_path) -> None:
    dsn = disposable_database
    plan, sha = _loser_plan(tmp_path, "ch", [("Osmometer", "ACME-7", "100,00")], _maker("Changed"))
    assert ipl.main(_argv("apply", dsn, plan, sha, tmp_path / "a", operator)) == 0
    [(product_id,)] = _owner(dsn, "select p.id::text from catalog.product p join catalog.supplier_product sp "
                                  "on sp.product_id = p.id where sp.origin_source_record_id = %s",
                             (_manifest_id(dsn, sha),))
    _catalog_command(dsn, operator, "update-product",
                     UpdateProductBody(product_id=product_id, expected_version=1, name_es="Osmómetro ficticio"))
    assert ipl.main(_argv("rollback", dsn, plan, sha, tmp_path / "r", None,
                          "--confirm-delete-loaded-rows")) == _common.EXIT_REFUSED
    blockers = _report(tmp_path / "r", "rollback-report.json")["blockers"]
    assert {(b["table"], b["id"]) for b in blockers} >= {("catalog.product", product_id)}
    assert _owner(dsn, "select count(*) from catalog.product where id = %s", (product_id,)) == [(1,)]


@needs_db
def test_rollback_refused_when_a_later_observation_references_a_product(disposable_database, operator,
                                                                        tmp_path) -> None:
    dsn = disposable_database
    maker = _maker("Referenced")
    plan, sha = _loser_plan(tmp_path, "ref", [("Osmometer", "ACME-8", "100,00")], maker)
    assert ipl.main(_argv("apply", dsn, plan, sha, tmp_path / "a", operator)) == 0
    [(product_id, supplier)] = _owner(dsn, "select product_id::text, supplier_organization_id::text "
                                           "from catalog.supplier_product where origin_source_record_id = %s",
                                      (_manifest_id(dsn, sha),))
    _catalog_command(dsn, operator, "record-supplier-cost", RecordSupplierCostBody(
        product_id=product_id, supplier_organization_id=supplier, as_of="2025-01-01T00:00:00Z", price="120",
        currency="EUR", price_kind="supplier_offer"))
    assert ipl.main(_argv("rollback", dsn, plan, sha, tmp_path / "r", None,
                          "--confirm-delete-loaded-rows")) == _common.EXIT_REFUSED
    assert _manifest_id(dsn, sha) is not None
