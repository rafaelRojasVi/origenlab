/**
 * Audience freeze — criteria, review, final confirmation, and the frozen snapshot.
 *
 * The API decides everything here (`/v2/workspace/marketing/campaigns/{id}/freeze-preview`);
 * this screen shows it, collects the operator's identity decisions and optional exclusions,
 * and sends exactly one command from the final confirmation screen. **There is no Send button**
 * and nothing on this screen reaches Gmail: a freeze is a record, not a send.
 *
 * Relevance and permission are shown apart: each destination's evidence per canonical line
 * («Sin información» when there is none) never decides whether it may be written to, and every
 * exclusion reason is listed.
 */

import { useMemo, useState } from "react";
import { Badge, EmptyState, Panel, ResourceGate, Segmented, Skeleton, StatLine, fmtDate, fmtInt } from "../ui";
import { useResource } from "../useResource";
import { fetchFreezePreview, fetchFrozenRecipients, freezeCampaignAudience, refusalOf } from "./marketingApi";
import type {
  CampaignContent,
  EquipmentTaxonomy,
  FreezeCriteria,
  FreezePreview,
  FreezeResult,
  FreezeRow,
  FrozenSnapshot,
  InterestBasis,
  ReviewDecision,
  SendBlocker,
} from "./marketingTypes";

const EMPTY_CRITERIA: FreezeCriteria = {
  family_id: "", brand_id: "", model_id: "", organization_id: "", bases: [], recorded: "", q: "", scope: "both",
};

const BASIS_OPTIONS: { value: InterestBasis; label: string }[] = [
  { value: "purchased", label: "Compró" },
  { value: "requested_quotation", label: "Pidió cotización" },
  { value: "requested_information", label: "Pidió información" },
  { value: "inferred_relevance", label: "Relevancia inferida" },
];

const REVIEW_LABEL: Record<string, string> = {
  no_contact_point: "Sin punto de contacto en el CRM",
  multiple_institutions: "Vinculado a más de una institución",
  institution_mismatch: "El contacto figura en otra institución",
};

const REASON_LABEL: Record<string, string> = {
  invalid_address: "Destino no válido",
  block: "Dirección bloqueada",
  block_domain: "Dominio bloqueado",
  prior_contact: "Contacto previo registrado",
  cooldown: "En período de espera",
  policy_supplier: "Proveedor o fabricante",
  manual_hold: "Retenido por un operador o sin revisar",
  already_in_audience: "Otra dirección de la misma persona",
};

const UNSUBSCRIBE_BLOCKER: SendBlocker = {
  code: "unsubscribe_processing_unsupported",
  label: "BAJA / desuscripción no soportada",
  detail:
    "No existe un procesador de respuestas entrantes ni un registro durable de supresiones probado. Ningún correo que ofrezca «responda BAJA» puede enviarse hasta que ambos existan.",
};

const btn = "h-7 rounded-md px-3 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-40";
const btnPrimary = `${btn} bg-ink text-white hover:bg-black`;
const btnSecondary = `${btn} border border-line bg-canvas-raised text-ink hover:bg-canvas-sunken`;
const inputCls = "h-7 w-full rounded-md border border-line bg-canvas-raised px-2 text-xs text-ink";

const short = (sha: string | null | undefined) => (sha ? sha.slice(0, 12) : "—");

/** Always shown on this screen: the reason nothing frozen here can be sent. */
export function BajaBlocker({ blockers }: { blockers?: SendBlocker[] }) {
  const all = blockers && blockers.length ? blockers : [UNSUBSCRIBE_BLOCKER];
  const [first, ...rest] = all;
  return (
    <div role="alert" data-testid="baja-blocker" className="rounded-md border border-bad/40 bg-bad-bg px-3 py-2 text-xs text-bad">
      <p>
        <b>Bloqueado: {first.label}.</b> {first.detail}
      </p>
      {rest.length ? (
        <ul className="mt-1 list-disc pl-4">
          {rest.map((b) => (
            <li key={b.code}>
              <b>{b.label}.</b> {b.detail}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function AudienceFreeze({
  campaign,
  taxonomy,
  freezeEnabled,
  onFrozen,
  onNewVersion,
}: {
  campaign: CampaignContent;
  taxonomy: EquipmentTaxonomy;
  freezeEnabled: boolean;
  onFrozen: (result: FreezeResult) => void;
  onNewVersion: (content: CampaignContent) => void;
}) {
  if (campaign.status !== "draft") {
    return <FrozenSnapshotView campaign={campaign} onNewVersion={onNewVersion} />;
  }
  return <FreezeFlow campaign={campaign} taxonomy={taxonomy} freezeEnabled={freezeEnabled} onFrozen={onFrozen} onNewVersion={onNewVersion} />;
}

type Step = "criteria" | "review" | "confirm" | "done";

function FreezeFlow({
  campaign,
  taxonomy,
  freezeEnabled,
  onFrozen,
  onNewVersion,
}: {
  campaign: CampaignContent;
  taxonomy: EquipmentTaxonomy;
  freezeEnabled: boolean;
  onFrozen: (result: FreezeResult) => void;
  onNewVersion: (content: CampaignContent) => void;
}) {
  const [criteria, setCriteria] = useState<FreezeCriteria>(EMPTY_CRITERIA);
  const [step, setStep] = useState<Step>("criteria");
  const [preview, setPreview] = useState<FreezePreview | null>(null);
  const [decisions, setDecisions] = useState<Record<string, ReviewDecision>>({});
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<FreezeResult | null>(null);

  const set = <K extends keyof FreezeCriteria>(k: K, v: FreezeCriteria[K]) => setCriteria((c) => ({ ...c, [k]: v }));
  const models = taxonomy.models.filter((m) => !criteria.brand_id || m.brand_id === criteria.brand_id);

  const load = async () => {
    setBusy(true);
    setError(null);
    try {
      const p = await fetchFreezePreview(campaign.campaign_id, criteria);
      setPreview(p);
      setDecisions({});
      setExcluded(new Set());
      setAcknowledged(false);
      setStep("review");
    } catch (err) {
      setError(refusalOf(err)?.message ?? (err instanceof Error ? err.message : String(err)));
    } finally {
      setBusy(false);
    }
  };

  const reviewRows = useMemo(() => (preview ? preview.rows.filter((r) => r.review_codes.length) : []), [preview]);
  const undecided = reviewRows.filter((r) => !decisions[r.key] || decisions[r.key].note.trim().length < 3);
  const serverProblems = (preview?.problems ?? []).filter((p) => p.code !== "review_pending");
  // Review rows are includable only once a person said so.
  const willInclude = preview
    ? preview.rows.filter(
        (r) =>
          r.inclusion === "included" &&
          !excluded.has(r.key) &&
          (!r.review_codes.length || decisions[r.key]?.decision === "include"),
      ).length
    : 0;
  const canConfirm = preview !== null && undecided.length === 0 && serverProblems.length === 0 && willInclude > 0;

  const freeze = async () => {
    if (!preview) return;
    setBusy(true);
    setError(null);
    try {
      const out = await freezeCampaignAudience({
        campaign_id: campaign.campaign_id,
        expected_version: campaign.version,
        expected_preview_sha256: preview.preview_sha256,
        criteria,
        review_decisions: reviewRows.map((r) => ({ ...decisions[r.key], key: r.key, note: decisions[r.key].note.trim() })),
        excluded_keys: [...excluded],
      });
      setResult(out);
      setStep("done");
      onFrozen(out);
    } catch (err) {
      const refusal = refusalOf(err);
      setError(
        refusal?.code === "path_not_allowed" || refusal?.code === "http_404" || refusal?.code === "http_405"
          ? "El congelamiento no está habilitado en este entorno."
          : refusal?.message ?? (err instanceof Error ? err.message : String(err)),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3" data-testid="audience-freeze">
      <BajaBlocker blockers={preview?.send_blockers} />
      <StepHeader step={step} />

      {step === "criteria" ? (
        <Panel title="Criterios de la audiencia" note="por interés evidenciado en equipos; nada estimado" bodyClassName="space-y-3 p-3">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <label className="space-y-1 text-[11px] text-ink-muted">
              <span>Línea (marca)</span>
              <select aria-label="Línea" className={inputCls} value={criteria.brand_id}
                onChange={(e) => setCriteria((c) => ({ ...c, brand_id: e.target.value, model_id: "" }))}>
                <option value="">Todas las líneas</option>
                {taxonomy.brands.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name} — {taxonomy.families.find((f) => f.id === b.family_id)?.name ?? b.family_id}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1 text-[11px] text-ink-muted">
              <span>Modelo</span>
              <select aria-label="Modelo" className={inputCls} value={criteria.model_id} onChange={(e) => set("model_id", e.target.value)}>
                <option value="">Cualquier modelo</option>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1 text-[11px] text-ink-muted">
              <span>Registro</span>
              <select aria-label="Registro" className={inputCls} value={criteria.recorded}
                onChange={(e) => set("recorded", e.target.value as FreezeCriteria["recorded"])}>
                <option value="">Registrado en el CRM y evidencia</option>
                <option value="crm">Sólo registrado en el CRM</option>
                <option value="evidence">Sólo evidencia no registrada</option>
              </select>
            </label>
          </div>
          <fieldset className="flex flex-wrap gap-3 text-xs text-ink">
            <legend className="mb-1 text-[11px] text-ink-muted">Base de la evidencia (vacío: todas)</legend>
            {BASIS_OPTIONS.map((b) => (
              <label key={b.value} className="inline-flex items-center gap-1">
                <input type="checkbox" checked={criteria.bases.includes(b.value)}
                  onChange={(e) => set("bases", e.target.checked ? [...criteria.bases, b.value] : criteria.bases.filter((x) => x !== b.value))} />
                {b.label}
              </label>
            ))}
          </fieldset>
          <Segmented
            label="Alcance"
            value={criteria.scope}
            onChange={(v) => set("scope", v)}
            options={[
              { value: "both", label: "Personas e instituciones" },
              { value: "persons", label: "Sólo personas con interés propio" },
              { value: "institutions", label: "Sólo destinos de instituciones" },
            ]}
          />
          <div className="flex items-center gap-2">
            <button type="button" className={btnPrimary} disabled={busy} onClick={() => void load()} data-testid="freeze-review">
              {busy ? "Calculando…" : "Revisar audiencia"}
            </button>
            <span className="text-[11px] text-ink-faint">
              Campaña «{campaign.name}», versión {campaign.version}. La revisión no escribe nada.
            </span>
          </div>
        </Panel>
      ) : null}

      {step === "review" && preview ? (
        <ReviewStep
          preview={preview}
          reviewRows={reviewRows}
          decisions={decisions}
          setDecision={(key, d) => setDecisions((all) => ({ ...all, [key]: d }))}
          excluded={excluded}
          toggleExcluded={(key) =>
            setExcluded((s) => {
              const next = new Set(s);
              if (next.has(key)) next.delete(key);
              else next.add(key);
              return next;
            })
          }
          undecided={undecided.length}
          serverProblems={serverProblems}
          canConfirm={canConfirm}
          onBack={() => setStep("criteria")}
          onConfirm={() => setStep("confirm")}
        />
      ) : null}

      {step === "confirm" && preview ? (
        <Panel title="Confirmación final" note="último paso: congela, no envía" bodyClassName="space-y-3 p-3">
          <div data-testid="freeze-confirmation" className="space-y-2 text-xs text-ink">
            <dl className="grid grid-cols-1 gap-x-4 gap-y-1 md:grid-cols-2">
              <Fact k="Campaña" v={`${campaign.name} (versión ${campaign.version})`} />
              <Fact k="Asunto" v={preview.content.subject ?? "—"} />
              <Fact k="Preencabezado" v={preview.content.preheader ?? "—"} />
              <Fact k="Huella del contenido" v={short(preview.content.content_sha256)} />
              <Fact k="Política de elegibilidad" v={preview.policy_version} />
              <Fact k="Huella de la revisión" v={short(preview.preview_sha256)} />
              <Fact k="Destinos incluidos" v={fmtInt(willInclude)} />
              <Fact k="Destinos excluidos (con motivo)" v={fmtInt(preview.counts.rows - willInclude)} />
              <Fact k="Mal formados (no se guardan)" v={fmtInt(preview.counts.malformed_not_stored)} />
              <Fact k="Decisiones de identidad" v={fmtInt(reviewRows.length)} />
            </dl>
            <p className="text-ink-muted">
              Al congelar se guarda una instantánea inmutable en <code>outbound.campaign_recipient</code>: cada destino con su decisión,
              todos sus motivos, su evidencia y fecha, la versión del contenido y la política. No se puede modificar; un cambio en el
              borrador o en la audiencia exige una versión nueva de la campaña y un congelamiento nuevo.
            </p>
            {preview.content.promises_baja ? (
              <p className="rounded-md border border-bad/40 bg-bad-bg px-2.5 py-1.5 text-bad" data-testid="baja-in-content">
                El contenido ofrece «BAJA». Esa promesa no puede cumplirse hoy: no hay procesamiento de BAJA.
              </p>
            ) : null}
            <label className="flex items-start gap-2">
              <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} data-testid="freeze-ack" />
              <span>
                Entiendo que congelar <b>no envía nada</b>, que la audiencia congelada <b>no se puede modificar</b> y que el envío sigue
                bloqueado mientras no exista procesamiento de BAJA.
              </span>
            </label>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className={btnSecondary} onClick={() => setStep("review")} disabled={busy}>
              ← Volver a la revisión
            </button>
            <button
              type="button"
              className={btnPrimary}
              disabled={!acknowledged || busy || !freezeEnabled}
              onClick={() => void freeze()}
              data-testid="freeze-confirm"
            >
              {busy ? "Congelando…" : "Congelar audiencia"}
            </button>
            {!freezeEnabled ? (
              <span className="text-[10px] text-ink-faint" data-testid="freeze-disabled">
                El congelamiento no está habilitado en este entorno.
              </span>
            ) : null}
          </div>
        </Panel>
      ) : null}

      {step === "done" && result ? (
        <div className="space-y-3">
          <p role="status" data-testid="freeze-done" className="rounded-md border border-line bg-canvas-sunken px-3 py-2 text-xs text-ink">
            <b>Audiencia congelada.</b> {fmtInt(result.counts.included)} incluidos y {fmtInt(result.counts.excluded)} excluidos en{" "}
            <code>{result.storage.tables.join(", ")}</code> de la base <code>{result.storage.database}</code>. Huella{" "}
            <code>{short(result.audience_sha256)}</code>. Nada fue enviado.
          </p>
          <FrozenSnapshotView campaign={{ ...campaign, status: "audience_frozen" }} onNewVersion={onNewVersion} />
        </div>
      ) : null}

      {error ? (
        <p role="alert" className="rounded-md border border-bad/30 bg-bad-bg px-2.5 py-1.5 text-xs text-bad" data-testid="freeze-error">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function StepHeader({ step }: { step: Step }) {
  const steps: [Step, string][] = [
    ["criteria", "1. Criterios"],
    ["review", "2. Revisión"],
    ["confirm", "3. Confirmación"],
    ["done", "4. Congelada"],
  ];
  return (
    <ol className="flex flex-wrap gap-2 text-[11px]">
      {steps.map(([s, label]) => (
        <li key={s} className={s === step ? "font-semibold text-ink" : "text-ink-faint"} aria-current={s === step ? "step" : undefined}>
          {label}
        </li>
      ))}
    </ol>
  );
}

function Fact({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex gap-2">
      <dt className="w-48 shrink-0 text-ink-muted">{k}</dt>
      <dd className="min-w-0 break-words font-medium">{v}</dd>
    </div>
  );
}

function Lines({ row }: { row: FreezeRow }) {
  return (
    <div className="flex flex-wrap gap-1">
      {row.lines.map((ln) => (
        <Badge key={ln.brand_id} tone={ln.status === "evidenced" ? "brand" : "neutral"} title={ln.line}>
          {ln.brand}: {ln.status === "evidenced" ? `${ln.label}${ln.latest_observed_at ? ` · ${fmtDate(ln.latest_observed_at)}` : ""}` : "Sin información"}
        </Badge>
      ))}
    </div>
  );
}

function ReviewStep({
  preview,
  reviewRows,
  decisions,
  setDecision,
  excluded,
  toggleExcluded,
  undecided,
  serverProblems,
  canConfirm,
  onBack,
  onConfirm,
}: {
  preview: FreezePreview;
  reviewRows: FreezeRow[];
  decisions: Record<string, ReviewDecision>;
  setDecision: (key: string, d: ReviewDecision) => void;
  excluded: Set<string>;
  toggleExcluded: (key: string) => void;
  undecided: number;
  serverProblems: { code: string; message: string }[];
  canConfirm: boolean;
  onBack: () => void;
  onConfirm: () => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const included = preview.rows.filter((r) => r.inclusion === "included");
  const excludedRows = preview.rows.filter((r) => r.inclusion === "excluded" && !r.review_codes.length);
  const shown = showAll ? included : included.slice(0, 50);
  const c = preview.counts;
  return (
    <div className="space-y-3" data-testid="freeze-preview">
      <StatLine
        items={[
          { label: "Destinos", value: fmtInt(c.rows) },
          { label: "Incluibles", value: fmtInt(c.included) },
          { label: "Excluidos con motivo", value: fmtInt(c.excluded) },
          { label: "Por revisar", value: fmtInt(c.review_required), tone: c.review_required ? "warn" : undefined },
          { label: "Mal formados (no se guardan)", value: fmtInt(c.malformed_not_stored) },
        ]}
      />
      <Panel title="Relevancia por línea" note="entre los incluibles; sin evidencia es «Sin información», nunca «bajo»" bodyClassName="divide-y divide-line">
        {c.included_by_line.map((ln) => (
          <div key={ln.brand_id} className="flex items-center gap-3 px-3 py-1.5 text-[13px]" data-testid="line-coverage">
            <span className="min-w-0 flex-1 text-ink">
              {ln.brand} — {ln.line}
            </span>
            <span className="tabular-nums text-ink">{fmtInt(ln.evidenced)} con evidencia</span>
            <span className="tabular-nums text-ink-muted">{fmtInt(ln.sin_informacion)} Sin información</span>
          </div>
        ))}
      </Panel>
      {c.excluded_by_reason.length ? (
        <Panel title="Excluidos, por motivo" note="un destino puede tener varios motivos; todos se guardan" bodyClassName="divide-y divide-line">
          {c.excluded_by_reason.map((r) => (
            <div key={r.code} className="flex items-center gap-3 px-3 py-1.5 text-[13px]">
              <span className="min-w-0 flex-1 text-ink">{r.label}</span>
              <span className="font-semibold tabular-nums text-ink">{fmtInt(r.count)}</span>
            </div>
          ))}
        </Panel>
      ) : null}

      {reviewRows.length ? (
        <Panel title="Identidades ambiguas" note="una persona decide cada una, con una nota" bodyClassName="divide-y divide-line">
          {reviewRows.map((r) => {
            const d = decisions[r.key];
            return (
              <div key={r.key} className="space-y-1.5 px-3 py-2" data-testid="review-row">
                <div className="flex flex-wrap items-center gap-2 text-[13px]">
                  <span className="font-medium text-ink">{r.display_name ?? r.address}</span>
                  {r.display_name ? <span className="text-ink-muted">{r.address}</span> : null}
                  {r.organizations.map((o) => (
                    <Badge key={o.organization_id} tone="neutral">
                      {o.name ?? "Institución sin nombre"}
                    </Badge>
                  ))}
                </div>
                <p className="text-[11px] text-warn">{r.review_codes.map((code) => REVIEW_LABEL[code] ?? code).join(" · ")}</p>
                <Lines row={r} />
                <div className="flex flex-wrap items-center gap-3 text-xs">
                  {(["include", "exclude"] as const).map((v) => (
                    <label key={v} className="inline-flex items-center gap-1">
                      <input type="radio" name={`d-${r.key}`} checked={d?.decision === v}
                        onChange={() => setDecision(r.key, { key: r.key, decision: v, note: d?.note ?? "" })} />
                      {v === "include" ? "Incluir" : "Excluir"}
                    </label>
                  ))}
                  <input
                    aria-label={`Nota de revisión para ${r.address}`}
                    className={`${inputCls} max-w-sm`}
                    placeholder="Por qué (obligatorio)"
                    value={d?.note ?? ""}
                    disabled={!d}
                    onChange={(e) => setDecision(r.key, { key: r.key, decision: d!.decision, note: e.target.value })}
                  />
                </div>
              </div>
            );
          })}
        </Panel>
      ) : null}

      <Panel title="Incluibles" note="relevancia a la vista; desmarque para retener un destino (queda excluido, con motivo)" bodyClassName="divide-y divide-line">
        {included.length === 0 ? (
          <EmptyState title="Nadie incluible">Con estos criterios ningún destino puede recibir la campaña.</EmptyState>
        ) : (
          shown.map((r) => (
            <label key={r.key} className="flex items-start gap-2 px-3 py-1.5 text-[13px]" data-testid="included-row">
              <input type="checkbox" checked={!excluded.has(r.key)} onChange={() => toggleExcluded(r.key)} aria-label={`Incluir ${r.address}`} />
              <span className="min-w-0 flex-1 space-y-1">
                <span className="block text-ink">
                  {r.display_name ? `${r.display_name} · ` : ""}
                  {r.address}
                  {r.note_labels.length ? <span className="ml-2 text-[11px] text-ink-faint">{r.note_labels.map((n) => n.label).join(" · ")}</span> : null}
                </span>
                <Lines row={r} />
              </span>
            </label>
          ))
        )}
        {included.length > shown.length ? (
          <button type="button" className="px-3 py-1.5 text-xs font-medium text-brand-700 hover:underline" onClick={() => setShowAll(true)}>
            Mostrar los {fmtInt(included.length)}
          </button>
        ) : null}
      </Panel>

      {excludedRows.length ? (
        <details className="rounded-lg border border-line bg-canvas-raised">
          <summary className="cursor-pointer px-3 py-2 text-[13px] font-semibold text-ink">Excluidos ({fmtInt(excludedRows.length)})</summary>
          <div className="divide-y divide-line">
            {excludedRows.map((r) => (
              <div key={r.key} className="flex flex-wrap gap-2 px-3 py-1.5 text-[13px]" data-testid="excluded-row">
                <span className="text-ink">{r.address}</span>
                <span className="text-ink-muted">{r.reasons.map((x) => x.label).join(" · ")}</span>
              </div>
            ))}
          </div>
        </details>
      ) : null}

      {serverProblems.length ? (
        <ul role="alert" className="list-disc rounded-md border border-bad/30 bg-bad-bg px-6 py-1.5 text-xs text-bad" data-testid="freeze-problems">
          {serverProblems.map((p) => (
            <li key={p.code}>{p.message}</li>
          ))}
        </ul>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={btnSecondary} onClick={onBack}>
          ← Cambiar criterios
        </button>
        <button type="button" className={btnPrimary} disabled={!canConfirm} onClick={onConfirm} data-testid="freeze-continue">
          Continuar a la confirmación
        </button>
        {undecided ? <span className="text-[10px] text-ink-faint">Faltan {undecided} decisión(es) de identidad con nota.</span> : null}
      </div>
    </div>
  );
}

function FrozenSnapshotView({ campaign, onNewVersion }: { campaign: CampaignContent; onNewVersion: (c: CampaignContent) => void }) {
  const [state, reload] = useResource(() => fetchFrozenRecipients(campaign.campaign_id), [campaign.campaign_id, campaign.status]);
  return (
    <div className="space-y-3" data-testid="frozen-snapshot">
      <BajaBlocker blockers={state.kind === "ready" ? state.data.send_blockers : undefined} />
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={4} />}>
        {(snap: FrozenSnapshot) => (
          <Panel
            title="Audiencia congelada"
            note={`${snap.storage.table} · base ${snap.storage.database} · inmutable`}
            aside={
              <button type="button" className={btnSecondary} onClick={() => onNewVersion(campaign)} data-testid="new-version">
                Nueva versión (borrador nuevo)
              </button>
            }
            bodyClassName="space-y-2 p-3"
          >
            {snap.recipients.length === 0 ? (
              <EmptyState title="Sin instantánea">Esta campaña no tiene una audiencia congelada en V2.</EmptyState>
            ) : (
              <>
                <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-xs md:grid-cols-2">
                  <Fact k="Congelada" v={fmtDate(snap.audience_frozen_at)} />
                  <Fact k="Política" v={snap.audience_policy_version ?? "—"} />
                  <Fact k="Huella del contenido" v={short(snap.content_sha256)} />
                  <Fact k="Huella de la audiencia" v={short(snap.audience_sha256)} />
                  <Fact k="Incluidos" v={fmtInt(snap.recipients.filter((r) => r.inclusion === "included").length)} />
                  <Fact k="Excluidos" v={fmtInt(snap.recipients.filter((r) => r.inclusion === "excluded").length)} />
                </dl>
                <div className="divide-y divide-line rounded-md border border-line">
                  {snap.recipients.map((r) => (
                    <div key={r.recipient_id} className="flex flex-wrap items-center gap-2 px-3 py-1.5 text-[13px]" data-testid="frozen-row">
                      <Badge tone={r.inclusion === "included" ? "brand" : "neutral"}>{r.inclusion === "included" ? "Incluido" : "Excluido"}</Badge>
                      <span className="text-ink">{r.address}</span>
                      {r.organization_name ? <span className="text-ink-muted">{r.organization_name}</span> : null}
                      <span className="text-[11px] text-ink-muted">
                        {r.frozen_reasons.map((x) => REASON_LABEL[x] ?? x).join(" · ")}
                        {r.relevance === "sin_informacion" ? " Sin información" : ` ${r.interest_evidence.length} evidencia(s)`}
                        {r.evidence_observed_at ? ` · ${fmtDate(r.evidence_observed_at)}` : ""}
                        {r.identity_review ? ` · revisado: ${r.identity_review.decision === "include" ? "incluir" : "excluir"}` : ""}
                      </span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </Panel>
        )}
      </ResourceGate>
    </div>
  );
}
