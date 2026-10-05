/**
 * «Sugerencias de la web» on the institution card: each researched field with its value, the
 * suggestion's confidence and its sources; «Aplicar» per field, «Aplicar todo y confirmar» for a
 * high-confidence suggestion. Nothing is applied without a click (`webSuggestions.ts`).
 */
import { useState } from "react";
import { Badge, Section } from "../ui";
import { refusalOf, type OrganizationAuthoringResponse, type OrgWebSuggestion } from "./crmAuthoringApi";
import {
  CONFIDENCE_LABEL,
  FIELD_LABEL,
  applyAllAndConfirm,
  applyAllPlan,
  applyField,
  refusalText,
  suggestionNote,
  suggestionRows,
  type SuggestionRow,
} from "./webSuggestions";

const CONFIDENCE_TONE = { high: "good", medium: "warn", low: "bad", not_found: "neutral" } as const;

function host(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export function WebSuggestionsSection({
  data,
  suggestion,
  mayAuthor,
  onRefresh,
}: {
  data: OrganizationAuthoringResponse;
  suggestion: OrgWebSuggestion;
  mayAuthor: boolean;
  onRefresh: () => void;
}) {
  const org = data.organization;
  const rows = suggestionRows(data, suggestion);
  const plan = applyAllPlan(rows, suggestion);
  const note = suggestionNote(suggestion);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: "good" | "bad"; text: string } | null>(null);

  async function applyOne(row: SuggestionRow) {
    setBusy(row.field);
    setMessage(null);
    try {
      await applyField(row, org.id, org.version, note);
      onRefresh();
    } catch (err) {
      const r = refusalOf(err);
      setMessage({ tone: "bad", text: `${row.label}: ${refusalText(r?.code ?? "error", r?.message ?? String(err))}` });
    } finally {
      setBusy(null);
    }
  }

  async function applyAll() {
    setBusy("all");
    setMessage(null);
    const outcome = await applyAllAndConfirm({
      organizationId: org.id,
      version: org.version,
      alreadyConfirmed: org.confirmation === "confirmed",
      plan,
      note,
    });
    setBusy(null);
    if (outcome.ok) {
      setMessage({ tone: "good", text: outcome.confirmed ? "Sugerencias aplicadas e institución confirmada." : "Sugerencias aplicadas." });
    } else {
      const where = outcome.failed === "confirm" ? "la confirmación" : FIELD_LABEL[outcome.failed];
      const done = outcome.applied.length ? ` Ya aplicado: ${outcome.applied.map((f) => FIELD_LABEL[f]).join(", ")}.` : "";
      setMessage({ tone: "bad", text: `Se detuvo en ${where}: ${refusalText(outcome.code, outcome.message)}${done}` });
    }
    onRefresh();
  }

  return (
    <Section
      title="Sugerencias de la web"
      aside={<Badge tone={CONFIDENCE_TONE[suggestion.confidence]} glyph={false}>{CONFIDENCE_LABEL[suggestion.confidence]}</Badge>}
    >
      {rows.length === 0 ? (
        <p className="text-xs text-ink-faint">La investigación no encontró datos para esta institución.</p>
      ) : (
        <ul className="divide-y divide-line rounded-md border border-line" aria-label="Sugerencias de la web">
          {rows.map((row) => (
            <li key={row.field} className="flex flex-wrap items-center gap-2 px-3 py-2 text-xs">
              <span className="w-28 shrink-0 text-ink-faint">{row.label}</span>
              <span className={`min-w-0 flex-1 break-words text-ink ${row.field === "rut" || row.field === "domain" ? "font-mono" : "font-medium"}`}>
                {row.value}
              </span>
              {row.verifyInSii && !row.applied ? (
                <span className="text-[11px] font-medium text-warn">verificar en SII antes de aplicar</span>
              ) : null}
              {row.applied ? (
                <Badge tone="good">ya aplicado</Badge>
              ) : mayAuthor ? (
                <button
                  type="button"
                  onClick={() => void applyOne(row)}
                  disabled={busy !== null}
                  aria-label={`Aplicar ${row.label}`}
                  className="h-6 rounded-md border border-line bg-canvas-raised px-2.5 text-[11px] font-medium text-ink hover:bg-canvas-sunken disabled:opacity-50"
                >
                  {busy === row.field ? "…" : "Aplicar"}
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {suggestion.sources.length > 0 ? (
        <ul className="mt-2 space-y-0.5 text-[11px] text-ink-muted" aria-label="Fuentes">
          {suggestion.sources.map((src) => (
            <li key={src.url}>
              <a href={src.url} target="_blank" rel="noreferrer noopener" className="font-medium text-brand-700 hover:underline">
                {host(src.url)}
              </a>
              {src.shows ? ` — ${src.shows}` : ""}
            </li>
          ))}
        </ul>
      ) : null}
      {suggestion.notes ? <p className="mt-1 text-[11px] text-ink-faint">{suggestion.notes}</p> : null}
      {mayAuthor && plan.length > 0 ? (
        <button
          type="button"
          onClick={() => void applyAll()}
          disabled={busy !== null}
          className="mt-2 h-7 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black disabled:opacity-50"
        >
          {busy === "all" ? "…" : org.confirmation === "confirmed" ? "Aplicar todo" : "Aplicar todo y confirmar"}
        </button>
      ) : null}
      {message ? (
        <p role="status" className={`mt-2 text-[11px] ${message.tone === "good" ? "text-good" : "text-bad"}`}>
          {message.text}
        </p>
      ) : null}
    </Section>
  );
}
