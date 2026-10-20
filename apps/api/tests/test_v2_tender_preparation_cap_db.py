"""The monthly cap on bid preparations, in its own database.

`platform.model_usage` is append-only, so seeding spend to the cap cannot be undone: the test lives
in its own module (own disposable database) and no other test depends on its order.
"""

from __future__ import annotations

import pytest
from v2_command_harness import build_disposable_database, needs_db

from test_v2_tender_preparation_db import (  # noqa: F401 - fixtures and helpers shared with the main module
    _owner,
    add_annex,
    ask,
    client,
    code,
    open_tender,
    operators,
    seed_spend,
    storage,
)

pytestmark = needs_db


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


def test_at_the_cap_the_request_is_refused_with_the_spend(db, operators, client) -> None:
    case = open_tender(db, operators)
    add_annex(db, case, operators)
    seed_spend(db, "60.00")
    r = ask(client, case, "read")
    assert r.status_code == 409 and code(r) == "model_budget_reached"
    detail = r.json()["detail"]
    assert detail["cap_usd"] == "60" and detail["spent_usd"] == "60.000000" and detail["reached"] is True
    with _owner(db) as conn:
        assert conn.execute("select count(*) from evidence.bid_preparation where opportunity_id = %s",
                            (case,)).fetchone()[0] == 0
