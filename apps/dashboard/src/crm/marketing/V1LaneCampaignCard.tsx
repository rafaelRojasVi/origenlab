import { useState } from "react";
import type { V1LaneCampaign } from "../crmTypes";
import { fmtInt } from "../ui";
import { WEEKDAYS, todayInSantiago, v1LaneBannerState, v1LaneStatus } from "./calendar";
import { EmailFrame } from "./EmailFrame";

const MONTH = new Intl.DateTimeFormat("es-CL", { timeZone: "UTC", month: "short" });

/** "lun 5 oct" for a YYYY-MM-DD day, read as that calendar day. */
function dayLabel(day: string): string {
  const d = new Date(`${day}T12:00:00Z`);
  return `${WEEKDAYS[(d.getUTCDay() + 6) % 7]} ${d.getUTCDate()} ${MONTH.format(d).replace(".", "")}`;
}

const STATE_LABEL = {
  upcoming: "Programada · canal V1",
  active: "En curso · canal V1",
  done: "Envío terminado · resultados por importar",
} as const;

/**
 * A campaign prepared and sent through the V1 lane (the timer on the owner's PC), shown as what it
 * is: the plan, day by day, and the email. Every figure is a planned count from the declaration,
 * never a sent one: V2 cannot see what V1 sent until the results are imported.
 */
export function V1LaneCampaignCard({ campaign }: { campaign: V1LaneCampaign }) {
  const today = todayInSantiago();
  const state = v1LaneBannerState(campaign, today);
  const [full, setFull] = useState(false);
  const plan = campaign.clients_per_day ?? null;
  return (
    <section
      data-testid="v1-lane-card"
      className="overflow-hidden rounded-xl border border-good/40 bg-canvas-raised shadow-[0_1px_2px_rgb(28_25_23/0.04)]"
    >
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line bg-good-bg/50 px-4 py-3">
        <h2 className="text-[15px] font-semibold text-ink">{campaign.name}</h2>
        <span className="rounded-full border border-good/40 bg-canvas-raised px-2 py-0.5 text-[11px] font-medium text-good">
          {STATE_LABEL[state]}
        </span>
        <span className="ml-auto text-[11px] text-ink-muted">
          {dayLabel(campaign.send_days[0])} a {dayLabel(campaign.send_days[campaign.send_days.length - 1])} ·{" "}
          {campaign.send_time} · promo hasta el {dayLabel(campaign.promo_until)}
        </span>
      </header>
      <div className="grid grid-cols-1 gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_auto]">
        <div className="min-w-0 space-y-3">
          <ol className="divide-y divide-line rounded-lg border border-line">
            {campaign.send_days.map((day, i) => {
              const status = v1LaneStatus(day, today);
              const isToday = day === today;
              return (
                <li
                  key={day}
                  data-testid={`v1-lane-day-${day}`}
                  className={`grid grid-cols-[9.5rem_minmax(0,1fr)_auto] items-center gap-3 px-3 py-2 text-[13px] ${
                    isToday ? "bg-good-bg/60" : ""
                  }`}
                >
                  <span className="whitespace-nowrap font-medium text-ink">
                    {dayLabel(day)} <span className="text-[11px] font-normal text-ink-faint">· oleada {i + 1}</span>
                  </span>
                  <span className="tabular-nums text-ink">{plan ? `${fmtInt(plan[i])} clientes` : "—"}</span>
                  <span className={`text-right text-[11px] ${isToday ? "font-semibold text-good" : "text-ink-muted"}`}>{status}</span>
                </li>
              );
            })}
            {plan ? (
              <li data-testid="v1-lane-total" className="flex items-center justify-between bg-canvas-sunken/60 px-3 py-2 text-[13px]">
                <span className="font-medium text-ink">Total planificado</span>
                <span className="font-semibold tabular-nums text-ink">{fmtInt(plan.reduce((n, x) => n + x, 0))} clientes</span>
              </li>
            ) : null}
          </ol>
          {campaign.audience_rule ? (
            <p className="text-xs text-ink-muted">
              <span className="font-medium text-ink">Audiencia: </span>
              {campaign.audience_rule}
            </p>
          ) : null}
          <p className="text-[11px] text-ink-faint">
            Se envía por el canal V1 (el temporizador del PC que hace los envíos). Las cifras son lo planificado, no lo enviado: quién
            recibió cada oleada y los resultados llegan al CRM con la importación al terminar.
          </p>
        </div>
        <div data-testid="v1-lane-preview" className="min-w-0">
          {campaign.html ? (
            <div className="space-y-2">
              <div className={`overflow-auto rounded-md border border-line bg-canvas-sunken ${full ? "max-h-[640px]" : ""}`}>
                {full ? (
                  <EmailFrame html={campaign.html} width={600} height={1400} title={`Correo de ${campaign.name}`} />
                ) : (
                  <EmailFrame html={campaign.html} width={600} height={900} scale={0.45} title={`Correo de ${campaign.name}`} />
                )}
              </div>
              <button
                type="button"
                onClick={() => setFull((v) => !v)}
                className="text-xs font-medium text-brand-700 hover:underline"
              >
                {full ? "Ver miniatura" : "Ver correo completo"}
              </button>
            </div>
          ) : (
            <p
              data-testid="v1-lane-no-preview"
              className="flex h-[150px] w-[270px] items-center justify-center rounded-md border border-dashed border-line-strong bg-canvas-sunken px-3 text-center text-xs text-ink-muted"
            >
              Vista previa del correo no cargada en este entorno
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
