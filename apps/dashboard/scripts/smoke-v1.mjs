#!/usr/bin/env node
/**
 * Smoke: Dashboard v1/v2 apps/api routes (read-only GET only).
 *
 * Base URL resolution (first match):
 *   SMOKE_BASE_URL → VITE_ORIGENLAB_API_BASE_URL → http://127.0.0.1:8001
 *
 * Optional: EXPECT_BACKEND=postgres|sqlite to assert health/meta labels.
 *
 * Dashboard-2: after warm cases load, GET /contacts/{email} using the first
 * valid contact_email found. Skips with a warning if none (not a failure).
 *
 * Does not call legacy /dashboard, /classification, or mutating methods.
 */
const base = (
  process.env.SMOKE_BASE_URL ||
  process.env.VITE_ORIGENLAB_API_BASE_URL ||
  "http://127.0.0.1:8001"
).replace(/\/$/, "");

const EXPECT_BACKEND = (process.env.EXPECT_BACKEND || "").trim().toLowerCase();
const SKIP_CONTACTS = process.env.SMOKE_SKIP_CONTACTS === "1";

const ROUTES = [
  { label: "GET /health", path: "/health" },
  { label: "GET /operator/status", path: "/operator/status?max_staleness_days=14" },
  { label: "GET /cases/warm", path: "/cases/warm?limit=5&positive_signal_only=false" },
  { label: "GET /operator/procurement/status", path: "/operator/procurement/status" },
];

const FORBIDDEN_LEGACY = ["/dashboard/", "/classification/", "/commercial/"];
const FORBIDDEN_CONTACT_KEYS = [
  "body",
  "body_preview",
  "email_body",
  "source_path",
  "sqlite_path",
  "source_file",
  "gmail_url",
];

function assertNoLegacyPaths() {
  for (const { path } of ROUTES) {
    for (const bad of FORBIDDEN_LEGACY) {
      if (path.includes(bad)) {
        throw new Error(`smoke misconfigured: legacy path ${path}`);
      }
    }
  }
  if (/:8000\b/.test(base) && !process.env.SMOKE_ALLOW_LEGACY_PORT) {
    console.warn(
      "[smoke-v1] Warning: base URL uses :8000 (legacy email-pipeline). Prefer :8001 apps/api or Vite proxy :5173.",
    );
  }
}

function isValidEmail(value) {
  const s = String(value || "").trim();
  return s.includes("@") && !/\s/.test(s);
}

/** Pick first contact email from warm cases (mirrors src/lib/smokeContactPick.ts). */
function pickContactEmailFromLists(warm) {
  for (const row of warm?.items || []) {
    if (isValidEmail(row.contact_email)) {
      return { email: row.contact_email.trim(), source: "warm_cases" };
    }
  }
  return null;
}

function assertContactPayloadSafe(data) {
  const errors = [];
  const blob = JSON.stringify(data);
  for (const key of FORBIDDEN_CONTACT_KEYS) {
    if (blob.includes(`"${key}"`)) {
      errors.push(`contact response must not expose ${key}`);
    }
  }
  if (!data?.meta?.read_only) {
    errors.push("contact meta.read_only must be true");
  }
  if (!data?.contact) {
    errors.push("contact.contact missing");
  }
  return errors;
}

async function get(path) {
  const res = await fetch(`${base}${path}`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${path} → HTTP ${res.status}: ${text.slice(0, 200)}`);
  }
  return res.json();
}

function validate(label, data) {
  const errors = [];
  if (label === "GET /health") {
    if (EXPECT_BACKEND === "postgres") {
      if (data.backend !== "postgres") {
        errors.push(`health.backend=${data.backend} expected postgres`);
      }
      if (data.mode !== "operator-postgres-mirror-readonly") {
        errors.push(`health.mode=${data.mode} expected operator-postgres-mirror-readonly`);
      }
    }
    if (EXPECT_BACKEND === "sqlite" && data.backend !== "sqlite") {
      errors.push(`health.backend=${data.backend} expected sqlite`);
    }
  }
  if (label === "GET /cases/warm" && EXPECT_BACKEND === "postgres") {
    if (data.meta?.data_source !== "postgres_mirror") {
      errors.push(`warm meta.data_source=${data.meta?.data_source} expected postgres_mirror`);
    }
  }
  return errors;
}

async function smokeContactDetail(warm) {
  if (SKIP_CONTACTS) {
    console.warn("[smoke-v1] SMOKE_SKIP_CONTACTS=1 — skipping GET /contacts/{email}");
    return { skipped: true, reason: "SMOKE_SKIP_CONTACTS" };
  }

  const picked = pickContactEmailFromLists(warm);
  if (!picked) {
    console.warn(
      "[smoke-v1] WARN: no contact_email in warm rows — skipping GET /contacts/{email} (not a failure)",
    );
    return { skipped: true, reason: "no_email_in_rows" };
  }

  const encoded = encodeURIComponent(picked.email);
  const path = `/contacts/${encoded}`;
  const data = await get(path);
  const errors = assertContactPayloadSafe(data);
  if (errors.length) {
    throw new Error(`GET /contacts/{email} validation: ${errors.join("; ")}`);
  }

  return {
    skipped: false,
    email: picked.email,
    source: picked.source,
    data_source: data.meta?.data_source,
    reduced_mode: data.meta?.reduced_mode,
    normalized_email: data.contact?.normalized_email,
    do_not_repeat: data.outreach?.do_not_repeat,
  };
}

async function main() {
  assertNoLegacyPaths();
  const results = {};
  const validationErrors = [];

  for (const { label, path } of ROUTES) {
    const data = await get(path);
    results[label] = data;
    validationErrors.push(...validate(label, data));
  }

  const health = results["GET /health"];
  const status = results["GET /operator/status"];
  const warm = results["GET /cases/warm"];
  const procurement = results["GET /operator/procurement/status"];

  const contactSmoke = await smokeContactDetail(warm);

  console.log("smoke v1 ok:", {
    base,
    backend: health.backend,
    mode: health.mode,
    verdict: status.verdict,
    warm_count: warm.meta?.count,
    warm_data_source: warm.meta?.data_source,
    procurement_summary_ok: procurement.summary_ok,
    expect_backend: EXPECT_BACKEND || "(any)",
    contact_smoke: contactSmoke,
  });

  if (validationErrors.length) {
    console.error("smoke v1 validation failed:", validationErrors.join("; "));
    process.exit(1);
  }
}

const isMain =
  typeof process !== "undefined" &&
  process.argv[1] &&
  (process.argv[1].endsWith("smoke-v1.mjs") || process.argv[1].includes("smoke-v1"));

if (isMain) {
  main().catch((err) => {
    console.error("smoke v1 failed:", err.message);
    process.exit(1);
  });
}
