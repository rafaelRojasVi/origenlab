#!/usr/bin/env python3
"""HTTP smoke for Dashboard v1 routes only (read-only GET).

Uses TestClient against apps/api. Supports sqlite (default) and postgres mirror backends
via ORIGENLAB_API_BACKEND / ORIGENLAB_POSTGRES_URL (or ORIGENLAB_TEST_POSTGRES_URL).

Does not call legacy /dashboard or /classification routes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "src") not in sys.path:
    sys.path.insert(0, str(_REPO / "src"))

DASHBOARD_V1_ROUTES: tuple[tuple[str, str, dict[str, str | int | bool]], ...] = (
    ("GET /health", "/health", {}),
    ("GET /operator/status", "/operator/status", {"max_staleness_days": 14}),
    (
        "GET /cases/warm",
        "/cases/warm",
        {"days": 14, "limit": 5, "positive_signal_only": "false"},
    ),
    ("GET /opportunities/equipment", "/opportunities/equipment", {"limit": 5}),
)

FORBIDDEN_LEGACY_PREFIXES = ("/dashboard", "/classification", "/commercial/")
FORBIDDEN_CONTACT_KEYS = frozenset(
    {
        "body",
        "body_preview",
        "email_body",
        "source_path",
        "sqlite_path",
        "source_file",
        "gmail_url",
    }
)


def _is_valid_email(value: object) -> bool:
    s = str(value or "").strip()
    return "@" in s and " " not in s


def _pick_contact_email(warm: dict, equipment: dict) -> tuple[str, str] | None:
    for row in warm.get("items") or []:
        email = str(row.get("contact_email") or "").strip()
        if _is_valid_email(email):
            return email, "warm_cases"
    for row in equipment.get("items") or []:
        email = str(row.get("contact_email") or "").strip()
        if _is_valid_email(email):
            return email, "equipment"
    return None


def _validate_contact_payload(data: dict) -> list[str]:
    errors: list[str] = []
    blob = json.dumps(data)
    for key in FORBIDDEN_CONTACT_KEYS:
        if f'"{key}"' in blob:
            errors.append(f"contact response must not expose {key}")
    meta = data.get("meta") or {}
    if meta.get("read_only") is not True:
        errors.append("contact meta.read_only must be true")
    if not data.get("contact"):
        errors.append("contact.contact missing")
    return errors


def _apply_backend_env(expect_backend: str | None) -> None:
    if expect_backend == "postgres":
        os.environ["ORIGENLAB_API_BACKEND"] = "postgres"
        pg = (
            os.environ.get("ORIGENLAB_POSTGRES_URL")
            or os.environ.get("ORIGENLAB_TEST_POSTGRES_URL")
            or ""
        ).strip()
        if not pg:
            print(
                "ERROR: postgres backend requires ORIGENLAB_POSTGRES_URL or "
                "ORIGENLAB_TEST_POSTGRES_URL",
                file=sys.stderr,
            )
            raise SystemExit(2)
        os.environ["ORIGENLAB_POSTGRES_URL"] = pg
    elif expect_backend == "sqlite":
        # An explicit smoke expectation must override a developer .env.
        # Unsetting API_BACKEND here would allow .env to select Postgres again.
        os.environ["ORIGENLAB_API_BACKEND"] = "sqlite"
        os.environ.pop("ORIGENLAB_POSTGRES_URL", None)
        os.environ.pop("ORIGENLAB_POSTGRES_WRITE_URL", None)
        os.environ["ORIGENLAB_COMMERCIAL_OPERATIONS_WRITES_ENABLED"] = "false"


def _validate_payload(label: str, data: dict, expect_backend: str | None) -> list[str]:
    errors: list[str] = []
    if label == "GET /health":
        backend = data.get("backend")
        if expect_backend and backend != expect_backend:
            errors.append(f"health.backend={backend!r} expected {expect_backend!r}")
        if (
            expect_backend == "postgres"
            and data.get("mode") != "operator-postgres-mirror-readonly"
        ):
            errors.append(
                f"health.mode={data.get('mode')!r} expected operator-postgres-mirror-readonly"
            )
        if expect_backend == "sqlite" and backend != "sqlite":
            errors.append(f"health.backend={backend!r} expected sqlite")
    elif label == "GET /cases/warm":
        meta = data.get("meta") or {}
        if (
            expect_backend == "postgres"
            and meta.get("data_source") != "postgres_mirror"
        ):
            errors.append(
                f"warm meta.data_source={meta.get('data_source')!r} expected postgres_mirror"
            )
        if expect_backend == "sqlite" and meta.get("data_source") not in (
            "sqlite",
            None,
        ):
            if meta.get("data_source") == "postgres_mirror":
                errors.append(
                    "warm meta.data_source must not be postgres_mirror in sqlite mode"
                )
    elif label == "GET /opportunities/equipment":
        meta = data.get("meta") or {}
        if (
            expect_backend == "postgres"
            and meta.get("data_source") != "postgres_mirror"
        ):
            errors.append(
                f"equipment meta.data_source={meta.get('data_source')!r} expected postgres_mirror"
            )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expect-backend",
        choices=("sqlite", "postgres"),
        default=None,
        help="Assert health.backend and mirror meta labels",
    )
    args = parser.parse_args(argv)

    for prefix in FORBIDDEN_LEGACY_PREFIXES:
        for _label, path, _params in DASHBOARD_V1_ROUTES:
            if path.startswith(prefix):
                print(
                    f"ERROR: smoke route list must not include legacy path {path}",
                    file=sys.stderr,
                )
                return 2

    _apply_backend_env(args.expect_backend)

    from fastapi.testclient import TestClient

    from origenlab_api.main import create_app
    from origenlab_api.settings import get_settings

    get_settings.cache_clear()
    client = TestClient(create_app())

    report: dict[str, object] = {
        "routes": {},
        "validation_errors": [],
        "contact_smoke": {},
    }
    warm_data: dict | None = None
    equipment_data: dict | None = None
    for label, path, params in DASHBOARD_V1_ROUTES:
        r = client.get(path, params=params)
        entry: dict[str, object] = {
            "status_code": r.status_code,
            "ok": r.status_code == 200,
        }
        if r.status_code == 200:
            data = r.json()
            entry["summary"] = {
                "backend": data.get("backend") if path == "/health" else None,
                "verdict": data.get("verdict") if path == "/operator/status" else None,
                "meta": (data.get("meta") or {}) if path != "/health" else None,
                "item_count": len(data.get("items") or []) if "items" in data else None,
            }
            report["validation_errors"].extend(
                _validate_payload(label, data, args.expect_backend)
            )
            if path == "/cases/warm":
                warm_data = data
            if path == "/opportunities/equipment":
                equipment_data = data
        else:
            entry["body"] = r.text[:300]
        report["routes"][label] = entry

    contact_smoke: dict[str, object] = {"skipped": True, "reason": "no_email_in_rows"}
    if warm_data is not None and equipment_data is not None:
        picked = _pick_contact_email(warm_data, equipment_data)
        if picked:
            email, source = picked
            from urllib.parse import quote

            r = client.get(f"/contacts/{quote(email, safe='')}")
            contact_entry: dict[str, object] = {
                "status_code": r.status_code,
                "ok": r.status_code == 200,
                "email": email,
                "source": source,
            }
            if r.status_code == 200:
                contact_data = r.json()
                contact_entry["summary"] = {
                    "data_source": (contact_data.get("meta") or {}).get("data_source"),
                    "reduced_mode": (contact_data.get("meta") or {}).get(
                        "reduced_mode"
                    ),
                    "normalized_email": (contact_data.get("contact") or {}).get(
                        "normalized_email"
                    ),
                }
                report["validation_errors"].extend(
                    _validate_contact_payload(contact_data)
                )
            else:
                contact_entry["body"] = r.text[:300]
            report["routes"]["GET /contacts/{email}"] = contact_entry
            contact_smoke = {
                "skipped": False,
                "email": email,
                "source": source,
                "status_code": r.status_code,
            }
            if r.status_code != 200:
                print(
                    f"FAIL: GET /contacts/{{email}} → {r.status_code}", file=sys.stderr
                )
                return 1
        else:
            print(
                "WARN: no contact_email in warm/equipment rows — skipping GET /contacts/{email}",
                file=sys.stderr,
            )
    report["contact_smoke"] = contact_smoke

    print(json.dumps(report, indent=2, default=str))
    failed = [k for k, v in report["routes"].items() if not v.get("ok")]  # type: ignore[union-attr]
    if failed:
        print(f"FAIL: routes not 200: {failed}", file=sys.stderr)
        return 1
    if report["validation_errors"]:
        print(f"FAIL: validation: {report['validation_errors']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
