from origenlab_api.v2.catalog.redaction import redact_costs

SAMPLE = {"id": "p1", "name_es": "Sonicador", "current_cost": {"price": "100.00", "currency": "EUR"},
          "cost_history": [{"price": "90.00"}], "supplier_terms": [{"default_discount_pct": "0.30", "route": "domestic"}]}


def test_viewer_never_sees_cost_fields():
    out = redact_costs(SAMPLE, "viewer")
    assert "cost_history" not in out
    assert "price" not in out["current_cost"]
    assert "default_discount_pct" not in out["supplier_terms"][0] and out["supplier_terms"][0]["route"] == "domestic"
    assert SAMPLE["cost_history"]  # input untouched


def test_sales_and_admin_see_everything():
    assert redact_costs(SAMPLE, "sales") == SAMPLE and redact_costs(SAMPLE, "admin") == SAMPLE
