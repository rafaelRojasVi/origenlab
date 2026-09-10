"""What each observation has to look like, and what it is worth if it does not.

Every check names the obligation it discharges, so a report can be read next to `MIGRATION.md`
§5.2 rather than interpreted. Four kinds, and the difference between them is the point:

`proof`        the catalogue answers the question completely. A violation is a finding.
`corroborate`  the catalogue supports an answer it cannot settle. Never a pass on its own.
`attest`       only an operator can answer it. The audit records the attestation and its evidence,
               and refuses to call an unattested item proven.
`record`       reported, never failed on -- the provider's own catalogue, which this design does
               not confer and may not revoke (ARCHITECTURE.md §6.5).

Comparison against the baseline is by value, not by count. A table missing hosted and a table that
exists only hosted are two findings, not a total that cancels out.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .verdict import ATTESTED, CORROBORATED, ERROR, FAIL, NOT_RUN, PASS, RECORDED

BASELINE_PATH = Path(__file__).resolve().parent.parent / "baselines" / "slice0.json"

PROOF = "proof"
CORROBORATE = "corroborate"
ATTEST = "attest"
RECORD = "record"


@dataclass(frozen=True)
class Context:
    """What the evaluators need to know about the run itself."""

    mode: str
    simulated: bool
    attestation: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Result:
    """One check's outcome, ready for the report."""

    check_id: str
    title: str
    obligation: str
    kind: str
    required: bool
    status: str
    findings: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    summary: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Spec:
    """One check definition."""

    check_id: str
    title: str
    obligation: str
    kind: str
    required_in: tuple[str, ...]
    evaluate: Callable[[dict, dict, Context], tuple[str, list[str], list[str], dict]]

    def required_for(self, mode: str) -> bool:
        return mode in self.required_in


# ------------------------------------------------------------------------------------------------
# Comparison helpers
# ------------------------------------------------------------------------------------------------


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def diff_collection(observed, expected, label: str, limit: int = 12) -> list[str]:
    """Compare two lists as multisets of canonical values, naming both directions."""
    findings: list[str] = []
    if observed is None:
        return [f"{label}: absent from the observation"]
    if not isinstance(observed, list) or not isinstance(expected, list):
        if observed != expected:
            findings.append(f"{label}: observed {_canonical(observed)}, baseline {_canonical(expected)}")
        return findings

    observed_keys = sorted(_canonical(item) for item in observed)
    expected_keys = sorted(_canonical(item) for item in expected)
    if observed_keys == expected_keys:
        return findings

    missing = [key for key in expected_keys if key not in observed_keys]
    extra = [key for key in observed_keys if key not in expected_keys]
    if missing:
        findings.append(
            f"{label}: {len(missing)} entr(y|ies) in the baseline are absent here: "
            + "; ".join(missing[:limit])
            + (" ..." if len(missing) > limit else "")
        )
    if extra:
        findings.append(
            f"{label}: {len(extra)} entr(y|ies) are present here and not in the baseline: "
            + "; ".join(extra[:limit])
            + (" ..." if len(extra) > limit else "")
        )
    return findings


def must_be_empty(data: dict, key: str, label: str, limit: int = 12) -> list[str]:
    """A list that has to be empty. Its contents are the finding."""
    value = data.get(key)
    if value is None:
        return [f"{label}: absent from the observation"]
    if not value:
        return []
    entries = [_canonical(item) for item in value]
    return [
        f"{label}: {len(entries)} entr(y|ies) present, expected none: "
        + "; ".join(entries[:limit])
        + (" ..." if len(entries) > limit else "")
    ]


def must_equal(data: dict, key: str, expected, label: str) -> list[str]:
    value = data.get(key)
    if value != expected:
        return [f"{label}: observed {_canonical(value)}, expected {_canonical(expected)}"]
    return []


def _settle(findings: list[str], notes: list[str], summary: dict, ok_status: str = PASS):
    return (FAIL if findings else ok_status, findings, notes, summary)


# ------------------------------------------------------------------------------------------------
# Evaluators
# ------------------------------------------------------------------------------------------------


def eval_s01(data: dict, baseline: dict, ctx: Context):
    findings: list[str] = []
    notes: list[str] = []

    findings += must_equal(data, "transaction_read_only", "on", "the transaction is read-only")
    findings += must_equal(
        data, "default_transaction_read_only", "on", "default_transaction_read_only on the connection"
    )
    if data.get("current_user") != "origenlab_owner":
        findings.append(
            f"the session assumed '{data.get('current_user')}', not origenlab_owner, "
            "so the catalogue reads are not the ones this audit reviewed"
        )
    if str(data.get("statement_timeout", "0")) in {"0", "0ms"}:
        findings.append("statement_timeout is unset; a hosted audit must not be able to hang on a lock")

    version = str(data.get("server_version_num", "0"))
    if not version.isdigit() or int(version) < 170000:
        findings.append(f"server is PostgreSQL {data.get('server_version')}, expected 17 or later")

    expected_session_user = baseline.get("audit_identity")
    if ctx.mode == "hosted":
        if data.get("session_user") != expected_session_user:
            findings.append(
                f"the hosted login is '{data.get('session_user')}', not the configured audit "
                f"identity '{expected_session_user}'"
            )
    else:
        notes.append(
            f"local mode connects as '{data.get('session_user')}' -- the CLI's development login. "
            f"The hosted audit identity is '{expected_session_user}'."
        )

    summary = {
        "session_user": data.get("session_user"),
        "current_user": data.get("current_user"),
        "server_version": data.get("server_version"),
        "transaction_read_only": data.get("transaction_read_only"),
        "default_transaction_read_only": data.get("default_transaction_read_only"),
        "statement_timeout": data.get("statement_timeout"),
        "is_superuser": data.get("is_superuser"),
    }
    return _settle(findings, notes, summary)


def eval_a01(data: dict, baseline: dict, ctx: Context):
    expected = baseline["a01"]
    findings: list[str] = []
    findings += diff_collection(data.get("roles"), expected["roles"], "OrigenLab role attributes")
    findings += diff_collection(
        data.get("owner_members"), expected["owner_members"], "members of origenlab_owner"
    )
    findings += diff_collection(
        data.get("runtime_role_memberships"),
        expected["runtime_role_memberships"],
        "memberships involving a runtime role",
    )
    if data.get("service_role_in_origenlab_role"):
        findings.append("service_role is a member of an OrigenLab role")

    bypass = [role["rolname"] for role in data.get("roles", []) if role.get("rolbypassrls")]
    if bypass:
        findings.append(f"check 1 violated: {', '.join(sorted(bypass))} carry BYPASSRLS")

    summary = {
        "origenlab_roles": [role.get("rolname") for role in data.get("roles", [])],
        "bypassrls_among_them": bypass,
        "owner_members": data.get("owner_members"),
    }
    return _settle(findings, [], summary)


def eval_a02(data: dict, baseline: dict, ctx: Context):
    expected = baseline["a02"]
    findings = must_be_empty(
        data,
        "forbidden_members",
        "an OrigenLab or Data-API-facing role is inside pg_read_all_data or pg_write_all_data",
    )

    notes = [
        "check 2: service_role's platform BYPASSRLS is recorded, never failed on and never altered "
        "(ARCHITECTURE.md §6.4).",
        "The hosted role catalogue is its own catalogue. The platform identities below are recorded "
        "for re-derivation before slice 1, not asserted equal to the local set "
        "(ARCHITECTURE.md §6.5).",
    ]
    for label, key in (
        ("pg_read_all_data / pg_write_all_data membership", "observed_members"),
        ("BYPASSRLS roles", "bypassrls_roles"),
        ("superuser roles", "superuser_roles"),
    ):
        drift = diff_collection(data.get(key), expected[key], label)
        notes.extend(f"recorded, not failed: {line}" for line in drift)

    summary = {
        "bypassrls_roles": data.get("bypassrls_roles"),
        "superuser_roles": data.get("superuser_roles"),
        "predefined_role_members": data.get("observed_members"),
    }
    return _settle(findings, notes, summary)


def eval_a03(data: dict, baseline: dict, ctx: Context):
    expected = baseline["a03"]
    findings: list[str] = []
    findings += must_be_empty(data, "forbidden_schema_grants", "a forbidden schema grant")
    findings += must_be_empty(
        data, "effective_schema_privilege", "a Data-API-facing role effectively reaches a schema"
    )
    findings += diff_collection(data.get("schemas"), expected["schemas"], "application schemas")
    findings += diff_collection(data.get("schema_acl"), expected["schema_acl"], "schema ACL")
    summary = {"schema_count": len(data.get("schemas") or []), "acl_entries": len(data.get("schema_acl") or [])}
    return _settle(findings, [], summary)


def eval_a04(data: dict, baseline: dict, ctx: Context):
    expected = baseline["a04"]
    findings: list[str] = []
    findings += must_be_empty(data, "forbidden_table_grants", "a forbidden table or sequence grant")
    findings += must_be_empty(data, "forbidden_column_grants", "a forbidden column grant")
    findings += must_be_empty(
        data, "effective_table_privilege", "a Data-API-facing role effectively reaches a relation"
    )
    findings += must_equal(data, "relation_count", expected["relation_count"], "relations in scope")
    summary = {"relation_count": data.get("relation_count")}
    return _settle(findings, [], summary)


def eval_a05(data: dict, baseline: dict, ctx: Context):
    expected = baseline["a05"]
    findings: list[str] = []
    findings += must_be_empty(data, "forbidden_execute_grants", "a forbidden EXECUTE grant")
    findings += must_be_empty(
        data, "effective_execute", "a Data-API-facing role effectively holds EXECUTE"
    )
    findings += diff_collection(
        data.get("security_definer_functions"),
        expected["security_definer_functions"],
        "SECURITY DEFINER functions (the closed list of ARCHITECTURE.md §6.2)",
    )
    findings += must_equal(data, "function_count", expected["function_count"], "functions in scope")
    summary = {
        "function_count": data.get("function_count"),
        "security_definer_count": len(data.get("security_definer_functions") or []),
    }
    return _settle(findings, [], summary)


def eval_a06(data: dict, baseline: dict, ctx: Context):
    expected = baseline["a06"]
    findings: list[str] = []
    findings += must_be_empty(data, "forbidden_default_acl", "a forbidden default privilege")
    findings += diff_collection(
        data.get("default_acl"), expected["default_acl"], "default privileges in scope"
    )
    notes = [
        "The absence of a default-privilege row is meaningful, not neutral: PostgreSQL's built-in "
        "default grants PUBLIC EXECUTE on functions. The baseline is therefore compared as an exact "
        "set, so a hosted project missing the owner's function default fails here."
    ]
    summary = {"default_acl_entries": len(data.get("default_acl") or [])}
    return _settle(findings, notes, summary)


def eval_a07(data: dict, baseline: dict, ctx: Context):
    expected = baseline["a07"]
    findings = diff_collection(
        data.get("database_privilege"), expected["database_privilege"], "database-level privileges"
    )
    owner_create = [
        row
        for row in data.get("database_privilege") or []
        if row.get("role") == "origenlab_owner" and row.get("privilege") == "CREATE" and row.get("held")
    ]
    if owner_create:
        findings.append(
            "origenlab_owner holds CREATE on the database between migrations "
            "(ARCHITECTURE.md §6.4 point 6)"
        )
    summary = {"owner_holds_database_create": bool(owner_create)}
    return _settle(findings, [], summary)


def eval_a08(data: dict, baseline: dict, ctx: Context):
    expected = baseline["a08"]
    findings: list[str] = []
    findings += diff_collection(data.get("schemas"), expected["schemas"], "schemas")
    findings += diff_collection(data.get("tables"), expected["tables"], "tables")
    findings += must_equal(data, "table_count", expected["table_count"], "table count")
    findings += must_be_empty(data, "tables_without_rls", "a table with RLS disabled")
    findings += must_be_empty(data, "tables_not_owned_by_owner", "a table not owned by origenlab_owner")
    summary = {"table_count": data.get("table_count"), "schema_count": len(data.get("schemas") or [])}
    return _settle(findings, [], summary)


def eval_a09(data: dict, baseline: dict, ctx: Context):
    expected = baseline["a09"]
    findings: list[str] = []
    findings += must_equal(data, "policy_count", expected["policy_count"], "RLS policy count")
    findings += diff_collection(data.get("policies"), expected["policies"], "RLS policies")
    summary = {"policy_count": data.get("policy_count")}
    return _settle(findings, [], summary)


def eval_a10(data: dict, baseline: dict, ctx: Context):
    expected = baseline["a10"]
    findings: list[str] = []
    findings += must_be_empty(data, "uncovered", "a foreign key with no covering index")
    findings += must_equal(data, "foreign_key_count", expected["foreign_key_count"], "foreign keys")
    if data.get("covered_count") != data.get("foreign_key_count"):
        findings.append(
            f"{data.get('covered_count')} of {data.get('foreign_key_count')} foreign keys are index-covered"
        )
    summary = {
        "foreign_key_count": data.get("foreign_key_count"),
        "covered_count": data.get("covered_count"),
        "covered_unconditionally": data.get("covered_unconditionally"),
    }
    return _settle(findings, [], summary)


def eval_a11(data: dict, baseline: dict, ctx: Context):
    expected = baseline["a11"]
    findings: list[str] = []
    findings += must_equal(data, "row_count", expected["row_count"], "send_control rows")
    findings += diff_collection(data.get("rows"), expected["rows"], "send_control")
    if data.get("any_flag_true"):
        findings.append("a send flag is true; slice 0 requires both false")
    summary = {"rows": data.get("rows")}
    return _settle(findings, [], summary)


def eval_a12(data: dict, baseline: dict, ctx: Context):
    expected = baseline["a12"]
    findings: list[str] = []
    findings += must_equal(data, "counted_tables", expected["counted_tables"], "tables counted")

    allowed = {(row["schema"], row["table"]): row["rows"] for row in expected["allowed_non_empty"]}
    for row in data.get("non_empty") or []:
        key = (row.get("schema"), row.get("table"))
        if key not in allowed:
            findings.append(f"{key[0]}.{key[1]} holds {row.get('rows')} row(s); slice 0 holds no business data")
        elif row.get("rows") != allowed[key]:
            findings.append(
                f"{key[0]}.{key[1]} holds {row.get('rows')} row(s), expected {allowed[key]}"
            )
    for (schema, table), count in allowed.items():
        present = any(
            row.get("schema") == schema and row.get("table") == table for row in data.get("non_empty") or []
        )
        if not present:
            findings.append(f"{schema}.{table} is empty; it must hold exactly {count} control row(s)")

    notes = [
        "outbound.send_control is a control row, not business data. It is counted here and "
        "excluded by name; a11 asserts its shape."
    ]
    summary = {"counted_tables": data.get("counted_tables"), "non_empty": data.get("non_empty")}
    return _settle(findings, notes, summary)


def eval_a13(data: dict, baseline: dict, ctx: Context):
    application_schemas = set(baseline["application_schemas"])
    findings = must_be_empty(
        data,
        "application_schema_reachable_by",
        "a Data-API-facing role holds USAGE on an application schema",
    )
    exposing = [
        entry
        for entry in data.get("pgrst_role_settings") or []
        if any(schema in str(entry.get("entry", "")) for schema in application_schemas)
    ]
    if exposing:
        findings.append(
            f"a PostgREST role setting names an application schema: {_canonical(exposing)}"
        )

    notes = [
        "This check corroborates and never proves. PostgREST's exposed-schema list lives in the "
        "project's API settings, which a database session cannot read. The Data API status is "
        "settled only by this corroboration together with the operator attestation of the "
        "Dashboard toggle -- see d01.",
    ]
    summary = {
        "pgrst_role_settings": data.get("pgrst_role_settings"),
        "authenticator_rolconfig": data.get("authenticator_rolconfig"),
    }
    status = FAIL if findings else CORROBORATED
    return (status, findings, notes, summary)


def eval_a14(data: dict, baseline: dict, ctx: Context):
    names = [ext.get("name") for ext in data.get("extensions") or []]
    notes = [
        "Recorded, never failed on. pg_cron, pgmq and the independent bucket-backup job are hosted "
        "slice 0 gate items (MIGRATION.md §5); they are absent locally by design and the hosted "
        "project's set is its own.",
        f"pg_cron installed: {'pg_cron' in names}. pgmq installed: {'pgmq' in names}.",
    ]
    return (RECORDED, [], notes, {"extensions": data.get("extensions")})


# ------------------------------------------------------------------------------------------------
# Derived and attested checks -- no SQL file behind them
# ------------------------------------------------------------------------------------------------

ATTESTATION_ITEMS: tuple[tuple[str, str, str], ...] = (
    (
        "t01",
        "data_api_disabled",
        "MIGRATION.md §5 slice 0 gate -- Data API off, application schemas not exposed",
    ),
    ("t02", "storage_buckets_all_private", "ARCHITECTURE.md §7 -- all buckets private, no public bucket"),
    (
        "t03",
        "no_privileged_key_in_deployments",
        "MIGRATION.md §5.2 check 10 -- no secret or legacy service-role key in any runtime configuration",
    ),
    ("t04", "backups_configured", "MIGRATION.md §5 slice 0 gate -- backups and the bucket-backup job"),
    ("t05", "database_restore_drill_passed", "MIGRATION.md §5 slice 0 gate -- database restore drill"),
    ("t06", "bucket_restore_drill_passed", "MIGRATION.md §5 slice 0 gate -- bucket restore drill"),
    ("t07", "security_advisors_clean", "MIGRATION.md §5.2 check 11 -- Supabase security advisors"),
)


def make_attest_evaluator(item: str):
    def evaluate(data: dict, baseline: dict, ctx: Context):
        entry = (ctx.attestation.get("items") or {}).get(item)
        if not isinstance(entry, dict):
            return (
                NOT_RUN,
                [],
                [f"no operator attestation for '{item}' was supplied; this item is unproven"],
                {},
            )
        if entry.get("value") is not True:
            return (
                FAIL,
                [f"the operator attested '{item}' as {entry.get('value')!r}"],
                [],
                {"evidence": entry.get("evidence", "")},
            )
        evidence = str(entry.get("evidence", "")).strip()
        if not evidence:
            return (
                NOT_RUN,
                [],
                [f"'{item}' was attested true with no evidence recorded; it is not accepted"],
                {},
            )
        return (
            ATTESTED,
            [],
            [
                "Operator-attested, not proven by this audit. The audit cannot inspect a Dashboard "
                "toggle, a bucket, a backup schedule, or the secret stores of Render, Cloudflare "
                "or GitHub."
            ],
            {"evidence": evidence, "attested_by": ctx.attestation.get("attested_by", "")},
        )

    return evaluate


def eval_d01(data: dict, baseline: dict, ctx: Context):
    """The Data API status: corroboration and attestation together, or nothing."""
    corroborated = data.get("corroborated") is True
    attested = data.get("attested") is True
    if corroborated and attested:
        return (
            ATTESTED,
            [],
            [
                "Settled by both halves: a13 shows no Data-API-facing role reaches an application "
                "schema and no PostgREST role setting names one, and the operator attested the "
                "Dashboard toggle. Neither half alone would settle it."
            ],
            {"sql_corroboration": corroborated, "operator_attestation": attested},
        )
    missing = []
    if not corroborated:
        missing.append("the SQL corroboration (a13) did not hold")
    if not attested:
        missing.append("the operator attestation of the Dashboard toggle (t01) is absent")
    return (
        NOT_RUN,
        [],
        ["The Data API status is unproven: " + "; and ".join(missing) + "."],
        {"sql_corroboration": corroborated, "operator_attestation": attested},
    )


def eval_c01(data: dict, baseline: dict, ctx: Context):
    """Project API-key types, recorded from the CLI. Never a key value, never a `--reveal`."""
    if not data:
        return (
            NOT_RUN,
            [],
            [
                "Project metadata was not collected. Pass --cli-metadata to record the API-key "
                "types present on the project."
            ],
            {},
        )
    names = sorted(str(name) for name in data.get("key_names") or [])
    notes = [
        "Key types only. The audit never requests, displays or persists a key value, never passes "
        "--reveal, and never stores the raw command output.",
        "The presence of a legacy service_role key is recorded here and does not fail the audit. "
        "The gate that matters is that no privileged key is configured or exposed in the "
        "applications, Render, Cloudflare, GitHub or a public client -- secret stores this audit "
        "cannot inspect. That is t03, an operator-attested deployment check.",
    ]
    return (RECORDED, [], notes, {"key_names": names})


# ------------------------------------------------------------------------------------------------
# The registry
# ------------------------------------------------------------------------------------------------

BOTH = ("local", "hosted")
HOSTED_ONLY = ("hosted",)
NEITHER: tuple[str, ...] = ()

SPECS: tuple[Spec, ...] = (
    Spec("s01", "The audit's own session is read-only", "OPERATIONS.md §4.2", PROOF, BOTH, eval_s01),
    Spec("a01", "Every OrigenLab-created role is NOBYPASSRLS", "MIGRATION.md §5.2 check 1", PROOF, BOTH, eval_a01),
    Spec("a02", "The platform and predefined-role boundary", "MIGRATION.md §5.2 check 2; ARCHITECTURE.md §6.5", PROOF, BOTH, eval_a02),
    Spec("a03", "No schema USAGE or CREATE for PUBLIC, anon, authenticated or service_role", "MIGRATION.md §5.2 check 3", PROOF, BOTH, eval_a03),
    Spec("a04", "No table, sequence or column privilege for those roles", "MIGRATION.md §5.2 check 3", PROOF, BOTH, eval_a04),
    Spec("a05", "No EXECUTE for those roles, and the SECURITY DEFINER inventory", "MIGRATION.md §5.2 checks 4 and 9", PROOF, BOTH, eval_a05),
    Spec("a06", "The owner's default privileges preserve checks 3 and 4", "MIGRATION.md §5.2 check 5", PROOF, BOTH, eval_a06),
    Spec("a07", "origenlab_owner holds no CREATE on the database", "ARCHITECTURE.md §6.4 point 6", PROOF, BOTH, eval_a07),
    Spec("a08", "Seven schemas, thirty-three tables, owned by the owner, RLS enabled", "ARCHITECTURE.md §6.1; DOMAIN.md §7", PROOF, BOTH, eval_a08),
    Spec("a09", "The RLS policy inventory is unchanged", "ARCHITECTURE.md §6.1", PROOF, BOTH, eval_a09),
    Spec("a10", "Every foreign key is index-covered", "MIGRATION.md §5.2 (impl)", PROOF, BOTH, eval_a10),
    Spec("a11", "Both send flags are false", "MIGRATION.md §5 slice 0 gate", PROOF, BOTH, eval_a11),
    Spec("a12", "The foundation holds no business data", "MIGRATION.md §5 slice 0 gate", PROOF, BOTH, eval_a12),
    Spec("a13", "Data API exposure -- SQL corroboration", "MIGRATION.md §5 slice 0 gate", CORROBORATE, NEITHER, eval_a13),
    Spec("a14", "Installed extensions", "MIGRATION.md §5 slice 0 gate", RECORD, NEITHER, eval_a14),
    Spec("c01", "Project API-key types", "MIGRATION.md §5.2 check 10 (partial)", RECORD, NEITHER, eval_c01),
) + tuple(
    Spec(check_id, item.replace("_", " ").capitalize(), obligation, ATTEST, HOSTED_ONLY, make_attest_evaluator(item))
    for check_id, item, obligation in ATTESTATION_ITEMS
) + (
    # d01 is folded from a13 and t01, so it is registered after both. Evaluation follows this
    # order, and `evaluate` reads the two results it depends on rather than re-deriving them.
    Spec("d01", "The Data API is off", "MIGRATION.md §5 slice 0 gate", ATTEST, HOSTED_ONLY, eval_d01),
)

SPEC_BY_ID = {spec.check_id: spec for spec in SPECS}

# Check ids answered by a SQL file rather than by the engine.
SQL_CHECK_IDS = tuple(spec.check_id for spec in SPECS if spec.check_id[0] in {"s", "a"})


def load_baseline(path: Path | None = None) -> dict:
    return json.loads((path or BASELINE_PATH).read_text(encoding="utf-8"))


def evaluate(observations: dict, baseline: dict, ctx: Context) -> list[Result]:
    """Evaluate every registered check. A check with no observation is NOT_RUN, never a pass."""
    results: list[Result] = []
    corroborated_a13 = False

    for spec in SPECS:
        required = spec.required_for(ctx.mode)

        if spec.check_id == "d01":
            data = {
                "corroborated": corroborated_a13,
                "attested": any(
                    r.check_id == "t01" and r.status == ATTESTED for r in results
                ),
            }
        elif spec.kind == ATTEST:
            data = {}
        elif spec.check_id == "c01":
            data = observations.get("c01") or {}
        else:
            if spec.check_id not in observations:
                results.append(
                    Result(
                        spec.check_id,
                        spec.title,
                        spec.obligation,
                        spec.kind,
                        required,
                        NOT_RUN,
                        notes=("the check produced no observation; it is not counted as passing",),
                    )
                )
                continue
            data = observations[spec.check_id]

        try:
            status, findings, notes, summary = spec.evaluate(data, baseline, ctx)
        except Exception as exc:  # noqa: BLE001 - an evaluator fault must not be read as a pass
            results.append(
                Result(
                    spec.check_id,
                    spec.title,
                    spec.obligation,
                    spec.kind,
                    required,
                    ERROR,
                    findings=(f"the observation could not be evaluated: {exc.__class__.__name__}",),
                )
            )
            continue

        if spec.check_id == "a13" and status == CORROBORATED:
            corroborated_a13 = True

        results.append(
            Result(
                spec.check_id,
                spec.title,
                spec.obligation,
                spec.kind,
                required,
                status,
                tuple(findings),
                tuple(notes),
                summary,
            )
        )

    return results
