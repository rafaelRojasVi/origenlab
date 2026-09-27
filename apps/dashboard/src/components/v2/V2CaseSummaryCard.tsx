/**
 * A commercial case as a card an operator can read in four seconds.
 *
 * Six lines, no schema: who is asking, what they want, from whom, whether there is a quote,
 * and when something last happened. Everything the model records *about how it knows* —
 * part rows opening and closing, withdrawn interests, evidence relations, the stage machine
 * and the six blocked commands — lives on the case's own screen, inside its technical
 * drawer, not here.
 *
 * `summary` is null while the case's card is still loading or failed to load. The list row
 * alone already carries the title, the stage and who is asking, so the card degrades to
 * those rather than disappearing.
 */

import type { CaseSummary } from "../../lib/commercialCase";
import type { V2CommercialCase, V2Quote } from "../../api/v2Types";
import { caseStageLabel } from "../../lib/commercialCase";
import { formatDate, sourceKindLabel } from "../../lib/crm360";
import { caseQuoteLine, groupParticipants } from "../../lib/crmConnections";
import { V2Chip } from "./V2Chip";

function Line({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-sm">
      <dt className="w-24 shrink-0 text-xs uppercase tracking-wide text-[var(--color-muted)]">
        {label}
      </dt>
      <dd className="min-w-0 flex-1 text-slate-800">{children}</dd>
    </div>
  );
}

function Unknown({ children }: { children: React.ReactNode }) {
  return <span className="text-[var(--color-muted)]">{children}</span>;
}

export function V2CaseSummaryCard({
  row,
  summary,
  quotes,
  onOpen,
  onOpenOrganization,
  role,
}: {
  row: V2CommercialCase;
  summary: CaseSummary | null;
  quotes: readonly V2Quote[];
  onOpen: () => void;
  onOpenOrganization?: (organizationId: string) => void;
  /**
   * What the institution this card was reached from is *on this case*.
   *
   * Only a screen that came from one institution can fill this in, and only that screen
   * should: on the global case list there is no "su papel" to speak of. It is the first
   * line because it is the reason this case is in front of the operator at all.
   */
  role?: React.ReactNode;
}) {
  const requestingName =
    summary?.requesting?.name ?? row.requesting_organization_name ?? null;
  const requestingId =
    summary?.requesting?.organization_id ?? row.requesting_organization_id ?? null;

  /*
    Parts come from the list row when the route carried them (`/v2/cases`), else from the
    case's card. Neither known means neither is shown — the card never asserts an absence
    it did not measure.
  */
  const groups = row.participants ? groupParticipants(row.participants) : null;
  const namesFor = (role: string): string[] | null =>
    groups
      ? (groups.find((group) => group.role === role)?.organizations.map((org) => org.name) ?? [])
      : summary
        ? role === "supplier"
          ? summary.suppliers
          : summary.manufacturers
        : null;
  const suppliers = namesFor("supplier");
  const manufacturers = namesFor("manufacturer");
  const otherGroups = groups
    ? groups.filter(
        (group) =>
          group.role !== "requesting_institution" &&
          group.role !== "supplier" &&
          group.role !== "manufacturer",
      )
    : null;
  const interests =
    summary && summary.interests.length > 0 ? summary.interests : (row.interest_labels ?? summary?.interests ?? null);
  const durableQuoteLine = caseQuoteLine(row.quote_count, row.latest_quote_status);

  return (
    <article
      className="space-y-3 rounded-xl border border-slate-200 bg-[var(--color-card)] p-4"
      data-testid="case-summary-card"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="min-w-0 text-sm font-semibold text-slate-900">{row.title}</h3>
        <span className="flex shrink-0 items-center gap-2">
          <V2Chip tone={row.closed_at ? "neutral" : "ok"}>{caseStageLabel(row.stage)}</V2Chip>
          <button
            type="button"
            onClick={onOpen}
            className="rounded-md border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700"
          >
            Abrir caso
          </button>
        </span>
      </div>

      <dl className="space-y-1">
        {role ? <Line label="Su papel">{role}</Line> : null}

        <Line label="Pide">
          {requestingName ? (
            requestingId && onOpenOrganization ? (
              <button
                type="button"
                onClick={() => onOpenOrganization(requestingId)}
                className="text-left font-medium text-slate-900 underline decoration-dotted"
              >
                {requestingName}
              </button>
            ) : (
              <span className="font-medium text-slate-900">{requestingName}</span>
            )
          ) : (
            <V2Chip tone="warn">Nadie ha dicho quién pide</V2Chip>
          )}
        </Line>

        {interests !== null || suppliers !== null ? (
          <>
            <Line label="Busca">
              {interests && interests.length > 0 ? (
                interests.join(" · ")
              ) : (
                <Unknown>Todavía no se registra qué busca</Unknown>
              )}
            </Line>

            <Line label="Proveedor">
              {suppliers && suppliers.length > 0 ? (
                suppliers.join(" · ")
              ) : (
                <Unknown>Sin proveedor registrado todavía</Unknown>
              )}
            </Line>

            <Line label="Fabricante">
              {manufacturers && manufacturers.length > 0 ? (
                manufacturers.join(" · ")
              ) : (
                <Unknown>Sin fabricante registrado todavía</Unknown>
              )}
            </Line>

            {otherGroups && otherGroups.length > 0 ? (
              <Line label="Otros papeles">
                <span className="flex flex-wrap gap-1" data-testid="case-summary-other-roles">
                  {otherGroups.map((group) => (
                    <V2Chip key={group.role} tone={group.role === "mentioned" ? "neutral" : "ok"}>
                      {group.label}: {group.organizations.map((org) => org.name).join(", ")}
                    </V2Chip>
                  ))}
                </span>
              </Line>
            ) : null}
          </>
        ) : (
          /*
            Without the case's card there is no way to know what it seeks or who supplies
            it, and the list row does not carry either. Printing "sin proveedor" here would
            be the card asserting an absence it never measured — so it reports what the row
            *does* say and sends the operator to the case for the rest.
          */
          <Line label="Contenido">
            <Unknown>
              {row.interest_count > 0
                ? `${row.interest_count} interés(es)`
                : "sin intereses"}{" "}
              · {row.organization_count} institución(es) · {row.evidence_count} documento(s)
            </Unknown>
          </Line>
        )}

        <Line label="Origen">
          {row.origin_source_kind ? (
            <>
              {sourceKindLabel(row.origin_source_kind)}
              <span className="text-[var(--color-muted)]">
                {" "}
                · {row.evidence_count === 1 ? "1 documento vinculado" : `${row.evidence_count} documentos vinculados`}
              </span>
            </>
          ) : (
            <Unknown>Origen no registrado todavía</Unknown>
          )}
        </Line>

        <Line label="Cotización">
          {quotes.length === 0 && durableQuoteLine ? (
            row.quote_count ? durableQuoteLine : <Unknown>{durableQuoteLine}</Unknown>
          ) : quotes.length > 0 ? (
            quotes
              .map(
                (quote) =>
                  `${quote.quote_number ?? "sin número"} rev. ${quote.revision_no} · ${quote.status}`,
              )
              .join(" · ")
          ) : (
            <Unknown>Sin cotización registrada todavía</Unknown>
          )}
        </Line>

        <Line label="Últ. act.">
          {row.last_activity_at ? (
            <>
              {formatDate(row.last_activity_at)}
              <span className="text-[var(--color-muted)]"> · actividad registrada</span>
            </>
          ) : summary?.lastActivityAt ? (
            <>
              {formatDate(summary.lastActivityAt)}
              {summary.lastActivityLabel ? (
                <span className="text-[var(--color-muted)]"> · {summary.lastActivityLabel}</span>
              ) : null}
            </>
          ) : (
            <Unknown>{formatDate(row.updated_at)}</Unknown>
          )}
        </Line>
      </dl>
    </article>
  );
}
