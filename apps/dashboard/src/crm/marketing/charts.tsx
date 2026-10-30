import { useEffect, useRef, useState } from "react";
import { fmtInt } from "../ui";

/**
 * Small, dependency-free charts for Marketing: a donut, a 100 % stacked bar and weekly stacked
 * columns. Colours are the `--chart-*` tokens (validated per theme in index.css); text stays in
 * ink tokens, never the series colour. Every chart carries its numbers in a legend or an
 * accessible label, so identity is never colour alone. Motion respects prefers-reduced-motion.
 */

export interface Slice {
  key: string;
  label: string;
  value: number;
  color: string;
}

/** No animation when the reader asked for less motion, or when the environment cannot say. */
function reducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return true;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** A number that counts up once when it first appears. */
export function CountUp({ value, className = "" }: { value: number; className?: string }) {
  const [shown, setShown] = useState(() => (reducedMotion() ? value : 0));
  const from = useRef(shown);
  useEffect(() => {
    if (reducedMotion()) {
      setShown(value);
      return;
    }
    const start = performance.now();
    const begin = from.current;
    let frame = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / 700);
      const eased = 1 - (1 - t) ** 3;
      setShown(Math.round(begin + (value - begin) * eased));
      if (t < 1) frame = requestAnimationFrame(tick);
      else from.current = value;
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value]);
  return <span className={`tabular-nums ${className}`}>{fmtInt(shown)}</span>;
}

/** Part-to-whole for a handful of slices; the hovered slice's value shows in the middle. */
export function Donut({
  slices,
  size = 168,
  thickness = 22,
  centerLabel,
  label,
}: {
  slices: Slice[];
  size?: number;
  thickness?: number;
  centerLabel: string;
  label: string;
}) {
  const [hover, setHover] = useState<string | null>(null);
  const [drawn, setDrawn] = useState(reducedMotion());
  useEffect(() => {
    const id = requestAnimationFrame(() => setDrawn(true));
    return () => cancelAnimationFrame(id);
  }, []);
  const total = slices.reduce((n, s) => n + s.value, 0);
  const r = (size - thickness) / 2;
  const c = 2 * Math.PI * r;
  const gap = slices.filter((s) => s.value > 0).length > 1 ? 2 : 0; // 2px surface gap between fills
  let offset = 0;
  const active = slices.find((s) => s.key === hover) ?? null;
  return (
    <div className="flex flex-wrap items-center gap-5">
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        role="img"
        aria-label={`${label}: ${slices.map((s) => `${s.label} ${s.value}`).join(", ")}`}
        className="shrink-0"
      >
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--color-canvas-sunken)" strokeWidth={thickness} />
        {slices.map((s) => {
          const len = total ? (s.value / total) * c : 0;
          const visible = Math.max(len - gap, 0);
          const el = (
            <circle
              key={s.key}
              className="chart-arc cursor-pointer"
              cx={size / 2}
              cy={size / 2}
              r={r}
              fill="none"
              stroke={s.color}
              strokeWidth={hover === s.key ? thickness + 4 : thickness}
              strokeDasharray={`${drawn ? visible : 0} ${c}`}
              strokeDashoffset={-offset}
              transform={`rotate(-90 ${size / 2} ${size / 2})`}
              opacity={hover && hover !== s.key ? 0.35 : 1}
              onMouseEnter={() => setHover(s.key)}
              onMouseLeave={() => setHover(null)}
            >
              <title>{`${s.label}: ${fmtInt(s.value)}`}</title>
            </circle>
          );
          offset += len;
          return el;
        })}
        <text x="50%" y="47%" textAnchor="middle" className="fill-ink text-[22px] font-bold tabular-nums">
          {fmtInt(active ? active.value : total)}
        </text>
        <text x="50%" y="60%" textAnchor="middle" className="fill-ink-faint text-[11px]">
          {active ? active.label : centerLabel}
        </text>
      </svg>
      <ul className="min-w-0 space-y-1.5 text-xs" aria-hidden="true">
        {slices.map((s) => (
          <li
            key={s.key}
            className={`flex items-center gap-2 rounded px-1 transition-opacity ${hover && hover !== s.key ? "opacity-40" : ""}`}
            onMouseEnter={() => setHover(s.key)}
            onMouseLeave={() => setHover(null)}
          >
            <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: s.color }} />
            <span className="text-ink-muted">{s.label}</span>
            <b className="ml-auto pl-3 tabular-nums text-ink">{fmtInt(s.value)}</b>
            <span className="w-10 text-right tabular-nums text-ink-faint">
              {total ? `${Math.round((s.value / total) * 100)} %` : ""}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** One 100 % bar: how a group splits. Segments grow in from the left. */
export function ShareBar({ slices, label, height = 10 }: { slices: Slice[]; label: string; height?: number }) {
  const total = slices.reduce((n, s) => n + s.value, 0);
  return (
    <div
      className="chart-bar-x flex gap-[2px] overflow-hidden rounded-full bg-canvas-sunken"
      style={{ height }}
      role="img"
      aria-label={`${label}: ${slices.map((s) => `${s.label} ${s.value}`).join(", ")}`}
    >
      {total
        ? slices
            .filter((s) => s.value > 0)
            .map((s) => (
              <span key={s.key} title={`${s.label}: ${fmtInt(s.value)}`} style={{ width: `${(s.value / total) * 100}%`, background: s.color }} />
            ))
        : null}
    </div>
  );
}

export interface WeekPoint {
  week: string;
  values: Record<string, number>;
}

const MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
const weekLabel = (iso: string) => `${Number(iso.slice(8, 10))} ${MONTHS[Number(iso.slice(5, 7)) - 1]}`;

/** Stacked columns per week (one axis), with a hover read-out above the plot. */
export function WeeklyBars({
  points,
  series,
  height = 150,
  label,
}: {
  points: WeekPoint[];
  series: { key: string; label: string; color: string }[];
  height?: number;
  label: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...points.map((p) => series.reduce((n, s) => n + (p.values[s.key] ?? 0), 0)));
  const w = 100 / Math.max(points.length, 1);
  const shown = hover === null ? null : points[hover];
  return (
    <figure className="space-y-2" aria-label={label}>
      <div className="flex min-h-5 flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-muted">
        {shown ? (
          <>
            <span className="font-semibold text-ink">Semana del {weekLabel(shown.week)}</span>
            {series.map((s) => (
              <span key={s.key}>
                <span className="mr-1.5 inline-block h-2 w-2 rounded-sm" style={{ background: s.color }} />
                {s.label} <b className="tabular-nums text-ink">{fmtInt(shown.values[s.key] ?? 0)}</b>
              </span>
            ))}
          </>
        ) : (
          series.map((s) => (
            <span key={s.key}>
              <span className="mr-1.5 inline-block h-2 w-2 rounded-sm" style={{ background: s.color }} />
              {s.label}
            </span>
          ))
        )}
      </div>
      <svg viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" className="h-[150px] w-full" role="img"
           aria-label={`${label}: ${points.map((p) => `${p.week} ${series.map((s) => `${s.label} ${p.values[s.key] ?? 0}`).join(" ")}`).join("; ")}`}>
        {[0.5, 1].map((f) => (
          <line key={f} x1="0" x2="100" y1={height - f * height} y2={height - f * height} stroke="var(--color-line)" strokeWidth="0.3" vectorEffect="non-scaling-stroke" />
        ))}
        {points.map((p, i) => {
          let y = height;
          return (
            <g key={p.week} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} className="cursor-default">
              <rect x={i * w} y="0" width={w} height={height} fill="transparent" />
              {series.map((s) => {
                const v = p.values[s.key] ?? 0;
                const h = (v / max) * (height - 4);
                y -= h;
                return v > 0 ? (
                  <rect
                    key={s.key}
                    className="chart-bar-y"
                    style={{ animationDelay: `${i * 30}ms` }}
                    x={i * w + w * 0.18}
                    y={y}
                    width={w * 0.64}
                    height={Math.max(h - 0.6, 0.6)}
                    rx="0.8"
                    fill={s.color}
                    opacity={hover !== null && hover !== i ? 0.4 : 1}
                  />
                ) : null;
              })}
            </g>
          );
        })}
      </svg>
      <div className="flex justify-between text-[10px] text-ink-faint">
        <span>{points[0] ? weekLabel(points[0].week) : ""}</span>
        <span>{points.at(-1) ? weekLabel(points.at(-1)!.week) : ""}</span>
      </div>
    </figure>
  );
}
