/**
 * «Web» — tarjeta de estadísticas del sitio público en Hoy.
 *
 * Muestra visitas, clics en WhatsApp y «Cotizar», envíos de formulario, descargas de PDF
 * e interacciones 3D para los últimos 7 y 30 días.
 *
 * Datos: GET /api/stats/web-stats manejado directamente por el Worker
 * (apps/dashboard-proxy/src/stats_read.ts). La consulta a la Analytics Engine SQL API
 * vive en el Worker, no en la API.
 *
 * Diseño: tokens monochrome + teal del dashboard; funciona en dark mode.
 */

import { useState } from "react";
import { useResource } from "../useResource";
import { Skeleton } from "../ui";
import { fetchWebStats, NOT_CONFIGURED_CODE, type WebStatsWindow, type WebStatsPeriod } from "./webStatsApi";
import { OperatorApiError } from "../../api/operatorClient";

// ─── helpers ─────────────────────────────────────────────────────────────────

function fmtN(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

/** Spanish label for each acquisition origen. */
const ORIGEN_LABEL: Record<string, string> = {
  google: "Google",
  "perfil-google": "Perfil Google",
  linkedin: "LinkedIn",
  directo: "Directo",
  whatsapp: "WhatsApp",
  otro: "Otro",
};

function origenLabel(slug: string): string {
  return ORIGEN_LABEL[slug] ?? slug;
}

// ─── sub-components ──────────────────────────────────────────────────────────

function StatRow({ label, value, accent = false }: { label: string; value: number; accent?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-[12px] text-ink-muted">{label}</span>
      <span className={`tabular-nums text-[14px] font-semibold leading-none ${accent ? "text-accent" : "text-ink"}`}>
        {fmtN(value)}
      </span>
    </div>
  );
}

function TopList({ items, label }: { items: [string, number][]; label: string }) {
  if (items.length === 0) return null;
  return (
    <div className="mt-1.5 space-y-1">
      <p className="text-[11px] font-medium uppercase tracking-wide text-ink-faint">{label}</p>
      {items.map(([key, cnt]) => (
        <div key={key} className="flex items-center justify-between gap-2">
          <span
            className="max-w-[160px] truncate text-[11px] text-ink-muted"
            title={key}
          >
            {key}
          </span>
          <span className="shrink-0 tabular-nums text-[12px] font-medium text-ink">{fmtN(cnt)}</span>
        </div>
      ))}
    </div>
  );
}

function VisitasOrigens({ by_origen }: { by_origen: Record<string, number> }) {
  const entries = Object.entries(by_origen).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return null;
  const total = entries.reduce((s, [, n]) => s + n, 0);
  return (
    <div className="mt-1.5 space-y-1">
      {entries.map(([origen, cnt]) => (
        <div key={origen} className="flex items-center gap-2">
          {/* progress bar */}
          <div
            aria-hidden="true"
            className="h-1.5 flex-1 overflow-hidden rounded-full bg-canvas-sunken"
          >
            <div
              className="h-full rounded-full bg-brand-600"
              style={{ width: total > 0 ? `${Math.round((cnt / total) * 100)}%` : "0%" }}
            />
          </div>
          <span className="w-[80px] text-right text-[11px] text-ink-muted">{origenLabel(origen)}</span>
          <span className="w-[36px] text-right tabular-nums text-[12px] font-medium text-ink">{fmtN(cnt)}</span>
        </div>
      ))}
    </div>
  );
}

function PeriodView({ data }: { data: WebStatsPeriod }) {
  return (
    <div className="space-y-3 divide-y divide-line">
      {/* Visitas */}
      <div>
        <StatRow label="Visitas (adquisición)" value={data.visitas_total} accent />
        <VisitasOrigens by_origen={data.visitas_by_origen} />
        {data.visitas_interno > 0 && (
          <p className="mt-1 text-[11px] text-ink-faint">
            + {fmtN(data.visitas_interno)} navegación interna
          </p>
        )}
      </div>

      {/* Acciones */}
      <div className="pt-3 space-y-1.5">
        <StatRow label="Clics en WhatsApp" value={data.whatsapp_total} />
        <TopList items={data.whatsapp_top_models} label="Por modelo" />
        <div className="pt-1.5">
          <StatRow label="Clics en Cotizar" value={data.cotizacion_total} />
          <TopList items={data.cotizacion_top_models} label="Por modelo" />
        </div>
        <div className="pt-0.5 space-y-1">
          <StatRow label="Formularios enviados" value={data.form_submits} />
          <StatRow label="Descargas de PDF" value={data.pdf_downloads} />
          <StatRow label="Interacciones 3D" value={data.d3_interacts} />
        </div>
      </div>

      {/* Páginas */}
      {data.top_pages.length > 0 && (
        <div className="pt-3">
          <TopList items={data.top_pages} label="Páginas más visitadas" />
        </div>
      )}
    </div>
  );
}

// ─── main card ───────────────────────────────────────────────────────────────

/**
 * Tarjeta «Web» en la columna lateral de Hoy.
 *
 * Muestra las métricas del sitio público para 7 o 30 días.
 * Estados: cargando → sin datos (nunca hubo eventos) → sin configurar → error → datos.
 */
export function WebStatsCard() {
  const [window, setWindow] = useState<WebStatsWindow>("7d");
  const [state, reload] = useResource(fetchWebStats);

  if (state.kind === "loading") {
    return (
      <section aria-label="Estadísticas web" data-testid="web-stats-card">
        <SectionHeader window={window} onWindow={setWindow} disabled />
        <Skeleton rows={5} />
      </section>
    );
  }

  if (state.kind === "unavailable") {
    return (
      <section aria-label="Estadísticas web" data-testid="web-stats-card">
        <SectionHeader window={window} onWindow={setWindow} />
        <NotEnabled />
      </section>
    );
  }

  if (state.kind === "permission") {
    return null; // No mostrar la tarjeta a quienes no tengan acceso.
  }

  if (state.kind === "error") {
    // Distinguish «not_configured» from a real fetch error.
    const isNotConfigured = isNotConfiguredError(state.message);
    return (
      <section aria-label="Estadísticas web" data-testid="web-stats-card">
        <SectionHeader window={window} onWindow={setWindow} />
        {isNotConfigured ? <NotConfigured /> : <FetchError onRetry={reload} />}
      </section>
    );
  }

  const data = state.data[window];

  return (
    <section aria-label="Estadísticas web" data-testid="web-stats-card">
      <SectionHeader window={window} onWindow={setWindow} />
      {data.visitas_total === 0 &&
       data.visitas_interno === 0 &&
       data.cotizacion_total === 0 &&
       data.whatsapp_total === 0 ? (
        <EmptyPeriod />
      ) : (
        <div className="rounded-xl border border-line bg-canvas-raised px-4 py-3.5">
          <PeriodView data={data} />
          <p className="mt-3 text-[11px] text-ink-faint">
            Actualizado {new Intl.DateTimeFormat("es-CL", {
              hour: "2-digit",
              minute: "2-digit",
            }).format(new Date(state.data.fetched_at))}.
          </p>
        </div>
      )}
    </section>
  );
}

// ─── inner components ─────────────────────────────────────────────────────────

function SectionHeader({
  window,
  onWindow,
  disabled = false,
}: {
  window: WebStatsWindow;
  onWindow: (w: WebStatsWindow) => void;
  disabled?: boolean;
}) {
  return (
    <div className="mb-2 flex items-center justify-between gap-2">
      <h2 className="text-[13px] font-semibold text-ink">Web</h2>
      <div className="flex gap-1" role="group" aria-label="Período">
        {(["7d", "30d"] as WebStatsWindow[]).map((w) => (
          <button
            key={w}
            type="button"
            disabled={disabled}
            aria-pressed={window === w}
            onClick={() => onWindow(w)}
            className={`rounded px-2 py-0.5 text-[11px] font-medium transition-colors ${
              window === w
                ? "bg-brand-600 text-on-solid"
                : "text-ink-muted hover:bg-canvas-sunken hover:text-ink disabled:opacity-40"
            }`}
          >
            {w === "7d" ? "7 días" : "30 días"}
          </button>
        ))}
      </div>
    </div>
  );
}

function NotConfigured() {
  return (
    <div
      data-testid="web-stats-not-configured"
      className="rounded-xl border border-dashed border-line-strong bg-canvas-raised px-4 py-4 text-center"
    >
      <p className="text-[12px] text-ink-muted">
        Falta configurar la lectura de estadísticas.
      </p>
      <p className="mt-1 text-[11px] text-ink-faint">
        Pedir al administrador que configure los secretos{" "}
        <code className="rounded bg-canvas-sunken px-1 font-mono text-[10px]">CF_ACCOUNT_ID</code>{" "}
        y{" "}
        <code className="rounded bg-canvas-sunken px-1 font-mono text-[10px]">CF_STATS_API_TOKEN</code>{" "}
        en el Worker.
      </p>
    </div>
  );
}

function NotEnabled() {
  return (
    <div className="rounded-xl border border-dashed border-line bg-canvas-raised px-4 py-4 text-center">
      <p className="text-[12px] text-ink-muted">Estadísticas web no disponibles en este entorno.</p>
    </div>
  );
}

function FetchError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="rounded-xl border border-line bg-canvas-raised px-4 py-3.5">
      <p className="text-[12px] text-ink-muted">No se pudieron cargar las estadísticas web.</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-2 h-7 rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink transition-colors hover:border-line-strong active:translate-y-px"
      >
        Reintentar
      </button>
    </div>
  );
}

function EmptyPeriod() {
  return (
    <div className="rounded-xl border border-dashed border-line bg-canvas-raised px-4 py-4 text-center">
      <p className="text-[12px] text-ink-muted">Sin eventos en este período.</p>
    </div>
  );
}

/** Checks if the error message comes from the Worker's «not_configured» 503 response. */
function isNotConfiguredError(message: string): boolean {
  try {
    const body = JSON.parse(message) as { error?: { code?: string } };
    return body?.error?.code === NOT_CONFIGURED_CODE;
  } catch {
    return false;
  }
}
