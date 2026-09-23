import type { ReactNode } from "react";

export type V2ChipTone = "neutral" | "warn" | "ok" | "danger";

const PALETTE: Record<V2ChipTone, string> = {
  neutral: "bg-slate-100 text-slate-700",
  warn: "bg-amber-100 text-amber-800",
  ok: "bg-emerald-100 text-emerald-800",
  danger: "bg-rose-100 text-rose-800",
};

export function V2Chip({ tone = "neutral", children }: { tone?: V2ChipTone; children: ReactNode }) {
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${PALETTE[tone]}`}>
      {children}
    </span>
  );
}
