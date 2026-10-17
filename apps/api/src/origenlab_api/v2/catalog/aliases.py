"""Model aliases at read time: the keys a quote line or a text may name a product by (DOMAIN.md §7 #59).

A product is named by its own `model_key` and by each of its active `catalog.product_model_alias`
keys (the same normalisation, `keys.model_key`). A merged duplicate counts as its twin
(`merged_into_product_id`). A key that names more than one product — two makers' products, or the
model of one and an alias of another — links to none of them through an alias: ambiguous means no
link, as before. A product's own `model_key` keeps reading as it always did.

SQL fragments only. Each takes the SQL of the key (a named parameter such as `%(key)s`, or a
column) and returns a subquery; the callers keep their own parameters. The partial index
`product_model_alias_key_active_idx` (`alias_key where retired_at is null`) serves every alias lookup.
"""
from __future__ import annotations


def roots_named_by(key: str) -> str:
    """`pid`: the root products the key names, by `model_key` or by an active alias. Its table aliases
    (`np`, `na`) are its own, so `key` may be a column of the caller's query."""
    return (
        f"select coalesce(np.merged_into_product_id, np.id) as pid from catalog.product np where np.model_key = {key} "
        f"union select coalesce(np.merged_into_product_id, np.id) from catalog.product_model_alias na "
        f"join catalog.product np on np.id = na.product_id where na.alias_key = {key} and na.retired_at is null"
    )


def unique_root_named_by(key: str) -> str:
    """A scalar subquery: the one root product the key names, or null when it names none or several."""
    return f"(select case when count(*) = 1 then (array_agg(r.pid))[1] end from ({roots_named_by(key)}) r)"


def unambiguous_alias_keys_of(root: str) -> str:
    """`k`: the active alias keys of a root product (and of the duplicates merged into it) that name
    no other product."""
    return (
        f"select ua.alias_key as k from catalog.product_model_alias ua join catalog.product uf on uf.id = ua.product_id "
        f"where ua.retired_at is null and coalesce(uf.merged_into_product_id, uf.id) = {root} "
        f"and not exists (select 1 from ({roots_named_by('ua.alias_key')}) o where o.pid <> {root})"
    )


def line_keys_of_key(key: str) -> str:
    """`k`: the model keys a line may carry to be about what `key` names — the key itself and, when it
    names exactly one product, that product's `model_key` and its unambiguous aliases."""
    root = unique_root_named_by(key)
    return (
        f"select {key}::text as k "
        f"union select lp.model_key from catalog.product lp where lp.id = {root} "
        f"union select x.k from ({unambiguous_alias_keys_of(root)}) x"
    )


def line_keys_of_product(product_id: str, key: str) -> str:
    """`k`: the model keys a line may carry to be about one product — its own `key` (`model_key`) and
    the unambiguous aliases of its root."""
    root = f"(select coalesce(rp.merged_into_product_id, rp.id) from catalog.product rp where rp.id = {product_id})"
    return f"select {key}::text as k union select x.k from ({unambiguous_alias_keys_of(root)}) x"


def has_active_alias(product: str, key: str) -> str:
    """A boolean expression: the product (`p.id`-like SQL) has an active alias with this key."""
    return (f"exists (select 1 from catalog.product_model_alias ha where ha.product_id = {product} "
            f"and ha.alias_key = {key} and ha.retired_at is null)")


__all__ = ["has_active_alias", "line_keys_of_key", "line_keys_of_product", "roots_named_by",
           "unambiguous_alias_keys_of", "unique_root_named_by"]
