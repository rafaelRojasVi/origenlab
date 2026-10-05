#!/usr/bin/env python3
"""Propose Spanish catalog content and manufacturer images for products, with Claude.

    uv sync --extra enrich      # the `anthropic` SDK is not in the runtime image
    ANTHROPIC_API_KEY=… uv run python scripts/catalog/enrich_products.py \\
        --target-dsn postgresql://origenlab_api:…@127.0.0.1:…/origenlab_test_<hex> \\
        --operator-email … --select quoted|model_keys:A,B|all-missing [--limit N] \\
        [--source-dir DIR] [--manufacturer-domain [BRAND=]DOMAIN …] --out DIR \\
        [--apply [--allow-cleanroom-production]]

**Dry-run is the default**: products are read, Claude is asked, the answers are validated and
written to `--out`/enrich-report.json — nothing is written to the database, no image is
downloaded. `--apply` writes, with the target guards of the catalog importers (`_common.py`):
a literal loopback DSN only (hosted is refused), a disposable `origenlab_test_<8 hex>` database
or the clean room with `--allow-cleanroom-production`, and the restricted `origenlab_api` login,
proven on the server.

**What Claude sees and what is kept**: `origenlab_api.v2.catalog.enrichment` — the product's own
data and its datasheet/page text with commercial lines removed; nothing kept that the source does
not state. Sources are plain text files the operator extracted beforehand, by model key:
`DIR/<MODEL_KEY>.datasheet.txt` and `DIR/<MODEL_KEY>.page.txt` (either may be absent). The
model is one synchronous Messages request per product (`enrichment.DEFAULT_MODEL`, or `--model`); the key is read from
`ANTHROPIC_API_KEY` and never printed, logged or written.

**What apply writes, per product, in its own transaction** (one product's failure leaves the
others written): the product row is locked and must still have the version read before Claude
was asked. Content whose `content_origin` is `operator`, or that an operator confirmed, is never
touched (such products are not even selected). `import` content is filled only where empty;
`machine` content may be replaced. Changed fields set `content_origin = 'machine'`, bump `version`
and append `product.updated` with `{changed, content_origin: "machine"}`. Each image candidate is
downloaded first (outside the transaction: https on the manufacturer's domain only, public
addresses only, no redirects, a timeout, at most 8 MiB streamed, magic bytes checked), stored by
content hash through `CatalogStorage`, then recorded as a `product_image` with `status =
'proposed'`, `created_by_operator_id = NULL` and `product.image_added`. Events name the operator
of `--operator-email` as actor and carry no command receipt, as the importers' do.

Labdelivery (spec S5): a product, a source text or an answer naming it refuses that item; the
others go on.

Exit codes: 0 done · 2 bad arguments · 11 refused before any write · 12 at least one item failed
(an API error or a database error; that item's transaction rolled back).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from origenlab_api.v2.catalog import enrichment  # noqa: E402
from origenlab_api.v2.catalog.importing import norm_name  # noqa: E402
from origenlab_api.v2.catalog.keys import LabdeliveryRefused, is_labdelivery, model_key  # noqa: E402
from origenlab_api.v2.catalog.storage import (  # noqa: E402
    MAX_IMAGE_BYTES,
    CatalogStorage,
    ImageRefused,
    check_image,
    image_path,
)
from origenlab_api.v2.identity import OperatorIdentity  # noqa: E402

import _actions  # noqa: E402
import _common  # noqa: E402

EXIT_OK = _common.EXIT_OK
EXIT_REFUSED = _common.EXIT_REFUSED
EXIT_ITEM_FAILED = _common.EXIT_APPLY_FAILED
DEFAULT_LIMIT = 20
MAX_LIMIT = 500
IMAGE_TIMEOUT_S = 20.0
_DOMAIN = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_CONTENT_FIELDS = ("name_es", "description_es", "category_es", "product_kind", "specs", "weight_kg", "length_cm",
                   "width_cm", "height_cm", "origin_country")
_CASTS = {"specs": "::jsonb", "weight_kg": "::numeric", "length_cm": "::numeric", "width_cm": "::numeric",
          "height_cm": "::numeric"}


class ImageFetchRefused(Exception):
    """This image URL was not downloaded; the message is a short reason, never the bytes."""


# ------------------------------------------------------------------ arguments (pure)

def parse_select(value: str) -> tuple[str, tuple[str, ...]]:
    value = value.strip()
    if value in ("quoted", "all-missing"):
        return value, ()
    if value.startswith("model_keys:"):
        keys = tuple(k for k in (model_key(part.strip()) for part in value[len("model_keys:"):].split(",")) if k)
        if keys:
            return "model_keys", keys
    raise ValueError("--select is quoted, all-missing or model_keys:A,B")


@dataclass
class Domains:
    """Manufacturer domains images may come from: for every product, or for one brand (folded name)."""

    everywhere: tuple[str, ...] = ()
    by_brand: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def for_brand(self, brand: str | None) -> tuple[str, ...]:
        return self.everywhere + self.by_brand.get(norm_name(brand or ""), ())


def parse_domains(values: list[str] | None) -> Domains:
    everywhere: list[str] = []
    by_brand: dict[str, list[str]] = {}
    for value in values or []:
        brand, sep, domain = value.rpartition("=")
        domain = domain.strip().lower().rstrip(".")
        if not _DOMAIN.match(domain):
            raise ValueError("--manufacturer-domain is [BRAND=]domain.tld (no scheme, path, wildcard or IP)")
        if sep:
            if not brand.strip():
                raise ValueError("--manufacturer-domain BRAND=domain needs a brand")
            by_brand.setdefault(norm_name(brand), []).append(domain)
        else:
            everywhere.append(domain)
    return Domains(tuple(everywhere), {k: tuple(v) for k, v in by_brand.items()})


# ------------------------------------------------------------------ image download

Resolver = Callable[..., list[tuple[Any, ...]]]


def fetch_image(url: str, domains: tuple[str, ...], *, client: httpx.Client,
                resolve: Resolver = socket.getaddrinfo, timeout: float = IMAGE_TIMEOUT_S) -> tuple[bytes, str]:
    """The bytes and content type of one manufacturer image, or `ImageFetchRefused`.

    https on a manufacturer domain (or subdomain) only; every address the host resolves to must
    be public (no private, loopback, link-local, reserved or multicast address); no redirect is
    followed; a timeout; the body is streamed and cut at 8 MiB; the declared type and the magic
    bytes must agree (`check_image`).
    """
    problem = enrichment.image_url_problem(url, domains)
    if problem is not None:
        raise ImageFetchRefused(problem)
    host = urlsplit(url).hostname
    try:
        infos = resolve(host, 443, 0, socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        raise ImageFetchRefused("unresolvable") from None
    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise ImageFetchRefused("unresolvable")
    for address in addresses:
        ip = ip_address(str(address).split("%", 1)[0])
        if not ip.is_global or ip.is_multicast:
            raise ImageFetchRefused("non_public_address")
    data = bytearray()
    try:
        with client.stream("GET", url, follow_redirects=False, timeout=timeout,
                           headers={"Accept": "image/jpeg, image/png, image/webp"}) as response:
            if response.is_redirect or 300 <= response.status_code < 400:
                raise ImageFetchRefused("redirect")
            if response.status_code != 200:
                raise ImageFetchRefused(f"http_{response.status_code}")
            declared_length = response.headers.get("content-length", "")
            if declared_length.isdigit() and int(declared_length) > MAX_IMAGE_BYTES:
                raise ImageFetchRefused("too_large")
            for chunk in response.iter_bytes():
                data += chunk
                if len(data) > MAX_IMAGE_BYTES:
                    raise ImageFetchRefused("too_large")
            declared = response.headers.get("content-type")
    except httpx.HTTPError as exc:
        raise ImageFetchRefused(type(exc).__name__) from None
    try:
        return bytes(data), check_image(bytes(data), declared)
    except ImageRefused:
        raise ImageFetchRefused("not_an_image") from None


def _real_http_client() -> httpx.Client:
    # trust_env=False: no proxy or netrc from the environment decides where an image request goes.
    return httpx.Client(timeout=httpx.Timeout(IMAGE_TIMEOUT_S, connect=5.0), follow_redirects=False, trust_env=False)


def _storage_from_env() -> CatalogStorage | None:
    url, key = os.environ.get("ORIGENLAB_V2_STORAGE_URL"), os.environ.get("ORIGENLAB_V2_STORAGE_SECRET_KEY")
    if not (url and key):
        return None
    from origenlab_api.v2.catalog.storage import SupabaseStorage

    try:
        return SupabaseStorage(url, key, httpx.Client(timeout=httpx.Timeout(30.0, connect=5.0)))
    except ValueError as exc:
        raise _common.Refused(f"catalog storage: {exc}") from None


def _anthropic_client() -> Any:
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        raise _common.Refused("ANTHROPIC_API_KEY is not set")
    try:
        import anthropic
    except ImportError:
        raise _common.Refused("the anthropic SDK is not installed: uv sync --extra enrich") from None
    return anthropic.Anthropic()  # reads ANTHROPIC_API_KEY itself; the value never passes through here


# ------------------------------------------------------------------ database

_SELECT = """
select p.id::text as id, p.model_number, p.model_key, p.name, p.product_kind, p.category_es, p.name_es,
       p.description_es, p.specs, p.weight_kg, p.length_cm, p.width_cm, p.height_cm, p.origin_country,
       p.content_origin, p.content_confirmed_at, p.version, m.name as brand
  from catalog.product p
  join crm.organization m on m.id = p.manufacturer_organization_id
 where p.active and p.content_origin <> 'operator' and p.content_confirmed_at is null
   and {filter}
 order by p.model_key, p.id
 limit %s
"""
_FILTERS = {
    "quoted": ("p.model_key in (select l.model_key from evidence.document_line l where l.model_key is not null)", ()),
    "all-missing": ("(p.name_es is null or p.description_es is null or p.specs = '[]'::jsonb)", ()),
    "model_keys": ("p.model_key = any(%s)", None),
}


def _rows(cur: Any) -> list[dict[str, Any]]:
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def select_products(cur: Any, mode: str, keys: tuple[str, ...], limit: int) -> list[dict[str, Any]]:
    clause, params = _FILTERS[mode]
    cur.execute(_SELECT.format(filter=clause), (*((list(keys),) if params is None else params), limit))
    return _rows(cur)


def read_operator(cur: Any, email: str) -> OperatorIdentity:
    cur.execute("select id::text, email_norm, display_name, role, status from platform.operator where email_norm = %s",
                (email.strip().lower(),))
    row = cur.fetchone()
    if row is None:
        raise _common.Refused("--operator-email names no registered operator")
    identity = OperatorIdentity(operator_id=row[0], email_norm=row[1], display_name=row[2], role=row[3], status=row[4])
    if identity.status != "active" or identity.role not in _common.WRITING_ROLES:
        raise _common.Refused(f"the operator must be active with role {' or '.join(_common.WRITING_ROLES)}")
    return identity


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def _same(current: Any, new: Any) -> bool:
    if isinstance(new, Decimal):
        return current is not None and Decimal(str(current)) == new
    return current == new


def planned_changes(current: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
    """The fields apply may write: never operator content; import content only where empty."""
    if current["content_origin"] == "operator" or current.get("content_confirmed_at") is not None:
        return {}
    changes = {}
    for name in _CONTENT_FIELDS:
        new = proposal.get(name)
        if new is None:
            continue
        if (current["content_origin"] == "machine" or _empty(current.get(name))) and not _same(current.get(name), new):
            changes[name] = new
    return changes


def _sql_value(name: str, value: Any) -> Any:
    return json.dumps(value, ensure_ascii=False) if name == "specs" else value


def _write_item(cur: Any, operator: OperatorIdentity, product: dict[str, Any], proposal: dict[str, Any],
                images: list[dict[str, str]]) -> tuple[str, list[str], int]:
    """One product's writes (the caller's transaction): outcome, changed fields, images added."""
    cur.execute(f"select {', '.join(_CONTENT_FIELDS)}, content_origin, content_confirmed_at, version "
                "from catalog.product where id = %s::uuid for update", (product["id"],))
    rows = _rows(cur)
    if not rows:
        return "gone", [], 0
    current = rows[0]
    if current["version"] != product["version"]:
        return "changed_since_read", [], 0
    if current["content_origin"] == "operator" or current["content_confirmed_at"] is not None:
        return "skipped_operator_content", [], 0
    changes = planned_changes(current, proposal)
    if changes:
        assignments = [f"{name} = %s{_CASTS.get(name, '')}" for name in changes]
        cur.execute(f"update catalog.product set {', '.join(assignments)}, content_origin = 'machine', "
                    "version = version + 1, updated_at = now() where id = %s::uuid and version = %s",
                    [*(_sql_value(n, v) for n, v in changes.items()), product["id"], product["version"]])
        _actions._EVENTS._append_event(cur, aggregate_kind="product", aggregate_id=product["id"],
                                       event_type="product.updated",
                                       payload={"changed": list(changes), "content_origin": "machine"},
                                       operator=operator, receipt_id=None)
    added = 0
    for image in images:
        cur.execute("select 1 from catalog.product_image where product_id = %s::uuid and sha256 = %s",
                    (product["id"], image["sha256"]))
        if cur.fetchone() is not None:
            continue
        cur.execute(
            """
            insert into catalog.product_image
                (product_id, storage_bucket, storage_path, sha256, content_type, sort_order, source, source_url,
                 status, created_by_operator_id)
            values (%s::uuid, 'catalog', %s, %s, %s,
                    (select coalesce(max(sort_order) + 1, 0) from catalog.product_image where product_id = %s::uuid),
                    %s, %s, 'proposed', null)
            returning id::text
            """,
            (product["id"], image["storage_path"], image["sha256"], image["content_type"], product["id"],
             image["source"], image["url"]))
        image_id = cur.fetchone()[0]
        _actions._EVENTS._append_event(cur, aggregate_kind="product", aggregate_id=product["id"],
                                       event_type="product.image_added",
                                       payload={"image_id": image_id, "sha256": image["sha256"],
                                                "content_type": image["content_type"], "source": image["source"],
                                                "content_origin": "machine"},
                                       operator=operator, receipt_id=None)
        added += 1
    if changes:
        return "updated", list(changes), added
    return ("images_only" if added else "no_changes"), [], added


# ------------------------------------------------------------------ one product, before any write

def _read_source(directory: Path | None, key: str, kind: str) -> str | None:
    if directory is None:
        return None
    path = directory / f"{key}.{kind}.txt"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def propose(client: Any, product: dict[str, Any], source_dir: Path | None,
            domains: tuple[str, ...], model: str = enrichment.DEFAULT_MODEL) -> tuple[str, enrichment.EnrichmentResult | None, str | None]:
    """(outcome, result, detail) for one product: `proposed` with a result, or why not."""
    datasheet = _read_source(source_dir, product["model_key"], "datasheet")
    page = _read_source(source_dir, product["model_key"], "page")
    if any(is_labdelivery(v) for v in (*(product.get(k) for k in enrichment.PRODUCT_KEYS), datasheet, page)
           if isinstance(v, str)):
        return "refused_labdelivery", None, None
    if any(len(t or "") > enrichment.MAX_SOURCE_CHARS for t in (datasheet, page)):
        return "source_too_long", None, None
    request = enrichment.build_request(product, datasheet, page, model=model)
    try:
        message = client.beta.messages.create(**request)
    except Exception as exc:  # noqa: BLE001 - reported by class only; a message may echo request data
        return "api_error", None, type(exc).__name__
    try:
        raw = enrichment.parse_message(message)
        result = enrichment.validate_response(raw, source_text=enrichment.source_text(product, datasheet, page),
                                              manufacturer_domains=domains)
    except LabdeliveryRefused:
        return "refused_labdelivery", None, None
    except enrichment.EnrichmentRefused as exc:
        return "model_answer_unusable", None, str(exc)
    return "proposed", result, None


def _download(result: enrichment.EnrichmentResult, product: dict[str, Any], domains: tuple[str, ...],
              storage: CatalogStorage | None, http: httpx.Client, resolve: Resolver) -> tuple[list[dict], list[dict]]:
    """Fetch and store each candidate; (stored images, refusals by reason)."""
    stored, refused = [], []
    for url, source in result.images:
        if storage is None:
            refused.append({"source": source, "reason": "storage_not_configured"})
            continue
        try:
            data, content_type = fetch_image(url, domains, client=http, resolve=resolve)
        except ImageFetchRefused as exc:
            refused.append({"source": source, "reason": str(exc)})
            continue
        sha = hashlib.sha256(data).hexdigest()
        path = image_path(product["id"], sha, content_type)
        storage.put_if_absent(path, data, content_type)
        stored.append({"url": url, "source": source, "sha256": sha, "content_type": content_type,
                       "storage_path": path})
    return stored, refused


# ------------------------------------------------------------------ the CLI

def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target-dsn", required=True, help="the origenlab_api login of a loopback database")
    ap.add_argument("--operator-email", required=True, help="the operator recorded as actor of the events")
    ap.add_argument("--select", required=True, help="quoted | all-missing | model_keys:A,B")
    ap.add_argument("--model", default=enrichment.DEFAULT_MODEL,
                    help=f"the Claude model (default {enrichment.DEFAULT_MODEL}, the cost-sensitive choice)")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"at most this many products (≤ {MAX_LIMIT})")
    ap.add_argument("--source-dir", type=Path, help="DIR/<MODEL_KEY>.datasheet.txt and DIR/<MODEL_KEY>.page.txt")
    ap.add_argument("--manufacturer-domain", action="append", default=[],
                    help="[BRAND=]domain images may come from (repeatable); without one, no image is proposed")
    ap.add_argument("--out", type=Path, required=True, help="a directory outside the repository")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True, help="the default: write nothing")
    mode.add_argument("--apply", action="store_true", help="write proposals into a disposable database")
    ap.add_argument("--allow-cleanroom-production", action="store_true",
                    help=f"with --apply, write into {_common.CLEANROOM_DB} instead of a disposable database")
    return ap


def main(argv: list[str] | None = None, *, client: Any = None, storage: CatalogStorage | None = None,
         http: httpx.Client | None = None, resolve: Resolver | None = None) -> int:
    args = _parser().parse_args(argv)
    import psycopg

    try:
        enrichment.check_model_id(args.model)
        select_mode, keys = parse_select(args.select)
        domains = parse_domains(args.manufacturer_domain)
        if not 1 <= args.limit <= MAX_LIMIT:
            raise _common.Refused(f"--limit is between 1 and {MAX_LIMIT}")
        _common.check_local_dsn(args.target_dsn, "--target-dsn")
        if args.allow_cleanroom_production and not args.apply:
            raise _common.Refused("--allow-cleanroom-production is for --apply")
        db_mode = _common.writable_mode(args.target_dsn, args.allow_cleanroom_production) if args.apply else None
        out = _common.prepare_out(args.out)
        if client is None:
            client = _anthropic_client()
        if args.apply and storage is None:
            storage = _storage_from_env()
        http = http or _real_http_client()
        resolve = resolve or socket.getaddrinfo

        with _common._connect(args.target_dsn, options="-c default_transaction_read_only=on") as conn:
            cur = conn.cursor()
            _common._assert_reached(cur, args.target_dsn, db_mode)
            _common.assert_runtime_login(cur)
            operator = read_operator(cur, args.operator_email)
            products = select_products(cur, select_mode, keys, args.limit)
            conn.rollback()
    except ValueError as exc:
        return _common.refuse(str(exc))
    except _common.Refused as exc:
        return _common.refuse(str(exc))
    except psycopg.Error as exc:
        sqlstate = getattr(getattr(exc, "diag", None), "sqlstate", None)
        return _common.refuse(f"database error before any write: {type(exc).__name__} sqlstate={sqlstate}")

    started = datetime.now(UTC).isoformat()
    items: list[dict[str, Any]] = []
    failed = False
    conn = None
    try:
        if args.apply:
            conn = _common._connect(args.target_dsn)
            cur = conn.cursor()
            _common._assert_reached(cur, args.target_dsn, db_mode)
            _common.assert_runtime_login(cur)
            conn.commit()
        for product in products:
            domains_here = domains.for_brand(product["brand"])
            item: dict[str, Any] = {"model_key": product["model_key"], "product_id": product["id"]}
            outcome, result, detail = propose(client, product, args.source_dir, domains_here, args.model)
            if detail:
                item["detail"] = detail
            if result is not None:
                item["rejected"] = result.rejected
                item["proposal"] = _jsonable({**{k: v for k, v in result.product_fields().items() if v is not None},
                                              "images": [{"url": u, "source": s} for u, s in result.images]})
            if outcome == "api_error":
                failed = True
            if outcome == "proposed" and args.apply:
                assert conn is not None  # noqa: S101
                stored, image_refusals = _download(result, product, domains_here, storage, http, resolve)
                item["images_refused"] = image_refusals
                try:
                    with conn.transaction():
                        outcome, changed, added = _write_item(conn.cursor(), operator, product,
                                                              result.product_fields(), stored)
                    item["changed"], item["images_added"] = changed, added
                except psycopg.Error as exc:
                    failed = True
                    outcome = "failed"
                    item["detail"] = f"{type(exc).__name__} sqlstate={getattr(getattr(exc, 'diag', None), 'sqlstate', None)}"
            item["outcome"] = outcome
            items.append(item)
    except _common.Refused as exc:
        return _common.refuse(str(exc))
    finally:
        if conn is not None:
            conn.close()

    counts: dict[str, int] = {}
    for item in items:
        counts[item["outcome"]] = counts.get(item["outcome"], 0) + 1
    report = {"tool": "enrich_products", "mode": "apply" if args.apply else "dry_run", "model": args.model,
              "target_database": _common.dbname(args.target_dsn), "select": select_mode, "operator_id":
              operator.operator_id, "started_at": started, "finished_at": datetime.now(UTC).isoformat(),
              "counts": counts, "items": items}
    _common.write_report(out, "enrich-report.json", report)
    _common.say({"mode": report["mode"], "selected": len(products), "counts": counts})
    return EXIT_ITEM_FAILED if failed else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
