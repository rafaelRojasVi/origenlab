import type {
  GmailInteractionAuditDomainRow,
  GmailInteractionAuditSnapshot,
} from "../api/gmailInteractionAuditTypes";
import type { WarmCaseCategory, WarmCaseItem } from "../api/commercialTypes";
import { emailDomain, parseSortableTimestamp } from "./clientTableView";
import { formatDashboardDateTime } from "./dashboardDateFormat";
import { findGmailAuditForDomains } from "./gmailInteractionAuditMatch";
import { buildSupplierMirrorDepthSummary } from "./supplierMirrorDepth";
import { truncate } from "./safeText";

const QUOTE_CATEGORIES: ReadonlySet<WarmCaseCategory> = new Set([
  "supplier_quote_received",
  "supplier_reply",
]);

const FOLLOWUP_CATEGORIES: ReadonlySet<WarmCaseCategory> = new Set([
  "supplier_followup",
  "waiting_supplier",
]);

export type SupplierRoleBadge = "Cotización recibida" | "Seguimiento" | "Hilo activo";

export interface SupplierEntityGroup {
  id: string;
  label: string;
  count: number;
  summaryLabel: string;
  auditDomain: GmailInteractionAuditDomainRow | null;
  quoteCount: number;
  followupCount: number;
  latestSeenAt: string | null;
  latestActivityLabel: string | null;
  latestSubject: string;
  roleBadge: SupplierRoleBadge;
  items: WarmCaseItem[];
}

function latestItem(items: WarmCaseItem[]): WarmCaseItem | null {
  if (items.length === 0) {
    return null;
  }
  return [...items].sort(
    (a, b) => parseSortableTimestamp(b.last_seen_at) - parseSortableTimestamp(a.last_seen_at),
  )[0];
}

export function roleBadgeForCategory(category: WarmCaseCategory | undefined): SupplierRoleBadge {
  if (!category) {
    return "Hilo activo";
  }
  if (QUOTE_CATEGORIES.has(category)) {
    return "Cotización recibida";
  }
  if (FOLLOWUP_CATEGORIES.has(category)) {
    return "Seguimiento";
  }
  return "Hilo activo";
}

export function buildSupplierCaseSummary(
  items: WarmCaseItem[],
  audit?: GmailInteractionAuditDomainRow | null,
): string {
  return buildSupplierMirrorDepthSummary(items, audit);
}

function previewSubject(row: WarmCaseItem | null): string {
  if (!row) {
    return "—";
  }
  const raw = row.subject?.trim() || row.snippet?.trim() || "";
  return raw ? truncate(raw, 72) : "—";
}

function formatActivityDate(iso: string | null): string | null {
  if (!iso?.trim()) {
    return null;
  }
  const formatted = formatDashboardDateTime(iso);
  return formatted === "—" ? null : formatted;
}

function buildGroup(
  id: string,
  label: string,
  items: WarmCaseItem[],
  auditSnapshot: GmailInteractionAuditSnapshot | null | undefined,
  domains: readonly string[],
): SupplierEntityGroup {
  const latest = latestItem(items);
  const auditDomain = findGmailAuditForDomains(auditSnapshot, domains);
  return {
    id,
    label,
    count: items.length,
    summaryLabel: buildSupplierCaseSummary(items, auditDomain),
    auditDomain,
    quoteCount: items.filter((row) => QUOTE_CATEGORIES.has(row.category)).length,
    followupCount: items.filter((row) => FOLLOWUP_CATEGORIES.has(row.category)).length,
    latestSeenAt: latest?.last_seen_at ?? null,
    latestActivityLabel: formatActivityDate(latest?.last_seen_at ?? null),
    latestSubject: previewSubject(latest),
    roleBadge: roleBadgeForCategory(latest?.category),
    items,
  };
}

/**
 * Supplier grouping is derived purely from warm-case evidence (contact email
 * domain), never from a UI-owned supplier list. Canonical supplier identity
 * belongs to `commercial.organization`; this view only groups Gmail evidence.
 */
export function resolveSupplierGroupId(row: WarmCaseItem): string {
  const domain = emailDomain(row.contact_email);
  if (domain) {
    return `domain:${domain}`;
  }
  return "other";
}

/** Most frequent non-empty account name in the group, else the domain. */
function deriveGroupLabel(id: string, items: WarmCaseItem[]): string {
  const counts = new Map<string, number>();
  for (const row of items) {
    const name = (row.account_name || "").trim();
    if (!name) {
      continue;
    }
    counts.set(name, (counts.get(name) ?? 0) + 1);
  }
  let best: string | null = null;
  let bestCount = 0;
  for (const [name, count] of counts) {
    if (count > bestCount) {
      best = name;
      bestCount = count;
    }
  }
  if (best) {
    return best;
  }
  if (id.startsWith("domain:")) {
    return id.replace("domain:", "");
  }
  return "Otros proveedores";
}

export function groupSupplierWarmCases(
  items: WarmCaseItem[],
  auditSnapshot?: GmailInteractionAuditSnapshot | null,
): SupplierEntityGroup[] {
  const buckets = new Map<string, WarmCaseItem[]>();

  for (const row of items) {
    const id = resolveSupplierGroupId(row);
    const list = buckets.get(id) ?? [];
    list.push(row);
    buckets.set(id, list);
  }

  const groups: SupplierEntityGroup[] = [];
  for (const [id, groupItems] of buckets) {
    if (groupItems.length === 0) {
      continue;
    }
    const domain = id.startsWith("domain:") ? id.replace("domain:", "") : "";
    groups.push(
      buildGroup(
        id,
        deriveGroupLabel(id, groupItems),
        groupItems,
        auditSnapshot,
        domain ? [domain] : [],
      ),
    );
  }

  groups.sort((a, b) => {
    if (b.count !== a.count) {
      return b.count - a.count;
    }
    return (
      parseSortableTimestamp(b.latestSeenAt) - parseSortableTimestamp(a.latestSeenAt)
    );
  });
  return groups;
}
