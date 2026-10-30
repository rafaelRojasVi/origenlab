/**
 * «Costos e infraestructura» — one button to refresh, no auto-refresh.
 * Shows: headline cards, Render services with bandwidth bars, Supabase table chart,
 * and a «¿Usamos lo que pagamos?» block.
 */
import { Badge, Button, Panel, ResourceGate, Skeleton } from "../ui";
import { CountUp, Donut, ShareBar } from "../marketing/charts";
import type { Slice } from "../marketing/charts";
import { fetchInfrastructure } from "../operations";
import type { InfraPayload, InfraRenderAvailable, InfraSupabase } from "../operations";
import { useResource } from "../useResource";

// ---------------------------------------------------------------------------
// byte / GB helpers
// ---------------------------------------------------------------------------

function toGB(bytes: number): number {
  return bytes / 1_073_741_824;
}

function fmtGB(bytes: number, decimals = 2): string {
  const gb = toGB(bytes);
  return gb < 0.01 ? "<0.01 GB" : `${gb.toFixed(decimals)} GB`;
}

function usePct(bytes: number, gbIncluded: number): number {
  const used = toGB(bytes);
  return Math.min(100, Math.round((used / gbIncluded) * 100));
}

// ---------------------------------------------------------------------------
// headline card tone
// ---------------------------------------------------------------------------

function pctTone(pct: number): "good" | "warn" | "bad" {
  if (pct >= 90) return "bad";
  if (pct >= 70) return "warn";
  return "good";
}

// ---------------------------------------------------------------------------
// Supabase donut
// ---------------------------------------------------------------------------

const CHART_COLORS: string[] = [
  "var(--chart-cat-1)",
  "var(--chart-cat-2)",
  "var(--chart-cat-3)",
  "var(--chart-cat-4)",
  "var(--chart-ok)",
  "var(--chart-optout)",
  "var(--chart-other)",
  "var(--chart-bounce)",
];

function SupabaseSection({ s }: { s: InfraSupabase }) {
  const dbBytes = s.database_bytes ?? 0;
  const dbPct = usePct(dbBytes, s.database_gb_included);
  const mailBytes = s.mail_bytes ?? 0;
  const mailCount = s.mail_count ?? 0;

  // Donut slices from the largest tables.
  const tables = s.largest_tables ?? [];
  const tableSlices: Slice[] = tables.map((t, i) => ({
    key: t.table,
    label: t.table.split(".")[1] ?? t.table,
    value: t.bytes,
    color: CHART_COLORS[i % CHART_COLORS.length],
  }));
  // Add an "other" slice if the tables don't account for all database bytes.
  const topTotal = tables.reduce((sum, t) => sum + t.bytes, 0);
  if (dbBytes > topTotal && topTotal > 0) {
    tableSlices.push({
      key: "_other",
      label: "resto",
      value: dbBytes - topTotal,
      color: "var(--chart-other)",
    });
  }

  return (
    <Panel title="Supabase" note={`Pro · US$${s.plan_monthly_usd}/mes`}>
      <div className="space-y-3 px-3 pb-3 pt-1">
        {/* DB size bar */}
        <div>
          <div className="mb-1 flex items-baseline justify-between text-[12px]">
            <span className="font-medium text-ink">Base de datos</span>
            <span className="tabular-nums text-ink-muted">
              {fmtGB(dbBytes)} / {s.database_gb_included} GB ({dbPct}%)
            </span>
          </div>
          <ShareBar
            label="Uso de base de datos Supabase"
            slices={[
              { key: "used", label: "Usado", value: dbPct, color: dbPct >= 90 ? "var(--chart-bounce)" : dbPct >= 70 ? "var(--chart-optout)" : "var(--chart-ok)" },
              { key: "free", label: "Libre", value: 100 - dbPct, color: "var(--canvas-sunken)" },
            ]}
          />
          {s.database_bytes_error ? (
            <p className="mt-0.5 text-[11px] text-ink-muted">No disponible: {s.database_bytes_error}</p>
          ) : null}
        </div>

        {/* Largest tables */}
        {tableSlices.length > 0 ? (
          <div className="flex flex-col items-center gap-2 sm:flex-row sm:items-start">
            <div className="shrink-0">
              <Donut
                slices={tableSlices}
                size={120}
                thickness={18}
                centerLabel="tablas"
                label="Tablas más grandes de la base de datos"
              />
            </div>
            <ul className="min-w-0 flex-1 space-y-0.5 text-[11px]">
              {tables.map((t, i) => (
                <li key={t.table} className="flex items-center gap-1.5">
                  <span
                    aria-hidden
                    className="size-2 shrink-0 rounded-full"
                    style={{ background: CHART_COLORS[i % CHART_COLORS.length] }}
                  />
                  <span className="min-w-0 truncate text-ink">{t.table}</span>
                  <span className="ml-auto tabular-nums text-ink-muted">{fmtGB(t.bytes)}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : s.largest_tables_error ? (
          <p className="text-[11px] text-ink-muted">Tablas no disponibles: {s.largest_tables_error}</p>
        ) : null}

        {/* Mail files */}
        <div className="text-[13px]">
          <span className="font-medium text-ink">Archivos de correo</span>
          {s.mail_bytes_error ? (
            <span className="ml-2 text-[11px] text-ink-muted">No disponible</span>
          ) : (
            <span className="ml-2 tabular-nums text-ink-muted">
              {fmtGB(mailBytes)} · <CountUp value={mailCount} /> correos
              {s.mail_bytes_this_month != null
                ? ` · ${fmtGB(s.mail_bytes_this_month)} este mes`
                : ""}
            </span>
          )}
        </div>
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Render section
// ---------------------------------------------------------------------------

function RenderSection({ r }: { r: InfraRenderAvailable }) {
  const includedBytes = r.bandwidth_gb_included * 1_073_741_824;
  const bwPct = Math.min(100, Math.round((r.bandwidth_bytes_this_month / includedBytes) * 100));

  return (
    <Panel title="Render" note={`Workspace ${r.workspace_plan} · US$${r.workspace_monthly_usd}/mes`}>
      <div className="space-y-3 px-3 pb-3 pt-1">
        {/* Workspace bandwidth */}
        <div>
          <div className="mb-1 flex items-baseline justify-between text-[12px]">
            <span className="font-medium text-ink">Ancho de banda saliente</span>
            <span className="tabular-nums text-ink-muted">
              {fmtGB(r.bandwidth_bytes_this_month)} / {r.bandwidth_gb_included} GB ({bwPct}%)
            </span>
          </div>
          <ShareBar
            label="Uso de ancho de banda Render este mes"
            slices={[
              {
                key: "used",
                label: "Usado",
                value: bwPct,
                color: bwPct >= 90 ? "var(--chart-bounce)" : bwPct >= 70 ? "var(--chart-optout)" : "var(--chart-ok)",
              },
              { key: "free", label: "Libre", value: 100 - bwPct, color: "var(--canvas-sunken)" },
            ]}
          />
          {bwPct >= 70 ? (
            <p className={`mt-1 text-[11px] ${bwPct >= 90 ? "text-bad" : "text-warn"}`}>
              {bwPct >= 90
                ? `⚠ Cerca del límite — si se supera, el exceso cuesta US$${r.bandwidth_overage_usd_per_100_gb}/100 GB.`
                : `Atención: se ha usado más del 70 % del ancho de banda incluido.`}
            </p>
          ) : null}
        </div>

        {/* Per-service rows */}
        {r.services.length > 0 ? (
          <ul className="divide-y divide-line text-[12px]">
            {r.services.map((svc) => {
              const svcPct = includedBytes > 0
                ? Math.min(100, Math.round((svc.bandwidth_bytes_this_month / includedBytes) * 100))
                : 0;
              return (
                <li key={svc.id} className="py-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="flex-1 truncate font-medium text-ink">{svc.name}</span>
                    <span className="text-[11px] text-ink-muted">{svc.type}</span>
                    {svc.suspended ? (
                      <Badge tone="neutral">Suspendido</Badge>
                    ) : svc.price_usd != null ? (
                      <Badge tone="info">US${svc.price_usd}/mes</Badge>
                    ) : (
                      <Badge tone="neutral">por uso</Badge>
                    )}
                  </div>
                  {!svc.suspended && svc.bandwidth_bytes_this_month > 0 ? (
                    <div className="mt-1 flex items-center gap-2">
                      <div className="flex-1">
                        <ShareBar
                          label={`Ancho de banda de ${svc.name}`}
                          height={6}
                          slices={[
                            {
                              key: "used",
                              label: "Usado",
                              value: svcPct,
                              color: svcPct >= 90 ? "var(--chart-bounce)" : svcPct >= 70 ? "var(--chart-optout)" : "var(--chart-cat-1)",
                            },
                            { key: "free", label: "Libre", value: Math.max(0, 100 - svcPct), color: "var(--canvas-sunken)" },
                          ]}
                        />
                      </div>
                      <span className="tabular-nums text-ink-muted">{fmtGB(svc.bandwidth_bytes_this_month)}</span>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="text-[12px] text-ink-muted">No se encontraron servicios.</p>
        )}
      </div>
    </Panel>
  );
}

function RenderUnavailable() {
  return (
    <Panel title="Render">
      <div className="px-3 pb-3 pt-1 text-[13px] text-ink-muted">
        <p>
          Para ver el uso de Render, agrega{" "}
          <code className="rounded bg-canvas-sunken px-1 font-mono text-[11px]">ORIGENLAB_RENDER_API_KEY</code>{" "}
          al servicio web <strong>origenlab</strong> en el panel de Render (Render → API keys).
        </p>
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Usage summary
// ---------------------------------------------------------------------------

function UsageSummary({ data }: { data: InfraPayload }) {
  const { supabase, render } = data;

  type Row = { label: string; used: string; total: string; pct: number };
  const rows: Row[] = [];

  if (supabase.database_bytes != null) {
    const pct = usePct(supabase.database_bytes, supabase.database_gb_included);
    rows.push({ label: "Base de datos Supabase", used: fmtGB(supabase.database_bytes), total: `${supabase.database_gb_included} GB`, pct });
  }
  if (render.available) {
    const bwIncluded = render.bandwidth_gb_included;
    const bwUsed = toGB(render.bandwidth_bytes_this_month);
    const pct = Math.min(100, Math.round((bwUsed / bwIncluded) * 100));
    rows.push({ label: "Ancho de banda Render", used: fmtGB(render.bandwidth_bytes_this_month), total: `${bwIncluded} GB`, pct });
  }
  if (supabase.mail_bytes != null) {
    const pct = usePct(supabase.mail_bytes, supabase.file_storage_gb_included);
    rows.push({ label: "Almacenamiento de correos", used: fmtGB(supabase.mail_bytes), total: `${supabase.file_storage_gb_included} GB`, pct });
  }

  if (!rows.length) return null;
  return (
    <Panel title="¿Usamos lo que pagamos?">
      <ul className="divide-y divide-line text-[13px]">
        {rows.map((row) => (
          <li key={row.label} className="flex flex-wrap items-center gap-3 px-3 py-2">
            <span className="flex-1 text-ink">{row.label}</span>
            <span className="tabular-nums text-ink-muted">
              {row.used} de {row.total}
            </span>
            <Badge tone={row.pct >= 90 ? "bad" : row.pct >= 70 ? "warn" : "good"}>
              {row.pct}%
            </Badge>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Headline cards
// ---------------------------------------------------------------------------

function HeadlineCards({ data }: { data: InfraPayload }) {
  const { supabase, render } = data;

  const dbBytes = supabase.database_bytes ?? 0;
  const dbPct = usePct(dbBytes, supabase.database_gb_included);
  const mailBytes = supabase.mail_bytes ?? 0;
  const mailCount = supabase.mail_count ?? 0;

  const bwPct = render.available
    ? Math.min(100, Math.round((toGB(render.bandwidth_bytes_this_month) / render.bandwidth_gb_included) * 100))
    : null;

  return (
    <ul className="grid grid-cols-2 gap-2 md:grid-cols-4">
      {/* Monthly cost */}
      <li className="rounded-lg border border-line bg-canvas-raised px-3 py-2.5">
        <p className="text-[11px] text-ink-muted">Costo mensual estimado</p>
        <p className="mt-0.5 text-[22px] font-semibold tabular-nums text-ink">
          US$<CountUp value={Math.round(data.monthly_cost_usd)} />
        </p>
      </li>
      {/* Render bandwidth */}
      <li className="rounded-lg border border-line bg-canvas-raised px-3 py-2.5">
        <p className="text-[11px] text-ink-muted">Ancho de banda Render</p>
        {render.available ? (
          <>
            <p className="mt-0.5 text-[18px] font-semibold tabular-nums text-ink">
              {fmtGB(render.bandwidth_bytes_this_month, 1)}{" "}
              <span className="text-[13px] font-normal text-ink-muted">/ {render.bandwidth_gb_included} GB</span>
            </p>
            {bwPct != null ? <Badge tone={pctTone(bwPct)}>{bwPct}% usado</Badge> : null}
          </>
        ) : (
          <p className="mt-0.5 text-[13px] text-ink-muted">Sin clave API</p>
        )}
      </li>
      {/* DB size */}
      <li className="rounded-lg border border-line bg-canvas-raised px-3 py-2.5">
        <p className="text-[11px] text-ink-muted">Base de datos Supabase</p>
        {supabase.database_bytes != null ? (
          <>
            <p className="mt-0.5 text-[18px] font-semibold tabular-nums text-ink">
              {fmtGB(dbBytes, 1)}{" "}
              <span className="text-[13px] font-normal text-ink-muted">/ {supabase.database_gb_included} GB</span>
            </p>
            <Badge tone={pctTone(dbPct)}>{dbPct}% usado</Badge>
          </>
        ) : (
          <p className="mt-0.5 text-[13px] text-ink-muted">No disponible</p>
        )}
      </li>
      {/* Mail files */}
      <li className="rounded-lg border border-line bg-canvas-raised px-3 py-2.5">
        <p className="text-[11px] text-ink-muted">Archivos de correo</p>
        {supabase.mail_bytes != null ? (
          <>
            <p className="mt-0.5 text-[18px] font-semibold tabular-nums text-ink">{fmtGB(mailBytes, 1)}</p>
            <p className="text-[11px] text-ink-muted">
              <CountUp value={mailCount} /> correos
            </p>
          </>
        ) : (
          <p className="mt-0.5 text-[13px] text-ink-muted">No disponible</p>
        )}
      </li>
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function InfraCostsPage() {
  const [state, reload] = useResource(fetchInfrastructure);
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-[13px] text-ink-muted">
          Datos de Supabase: en tiempo real. Render: actualizados al hacer clic en «Actualizar» (caché de 10 min).
          Precios de lista revisados el 2026-10-30; corrige en{" "}
          <code className="rounded bg-canvas-sunken px-1 font-mono text-[11px]">apps/api/src/origenlab_api/v2/infrastructure.py</code>{" "}
          si cambia el plan.
        </p>
        <Button onClick={reload}>Actualizar</Button>
      </div>
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton cards rows={4} />}>
        {(data) => (
          <div className="space-y-3">
            <HeadlineCards data={data} />
            <div className="grid gap-3 md:grid-cols-2">
              <SupabaseSection s={data.supabase} />
              {data.render.available ? (
                <RenderSection r={data.render} />
              ) : (
                <RenderUnavailable />
              )}
            </div>
            <UsageSummary data={data} />
          </div>
        )}
      </ResourceGate>
    </div>
  );
}
