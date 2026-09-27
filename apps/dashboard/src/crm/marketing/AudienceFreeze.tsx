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
 *
 * W12 (when the API has it on, `preview.recontact_review.enabled`): a destination kept out only
 * by a prior contact is listed with its last contact date, campaign or source and destination.
 * An operator approves recontact or keeps it excluded, with a note, per row or for a reviewed
 * selection — sent as one decision per recipient. Without a decision it stays excluded. The
 * API recomputes and revalidates every decision at freeze; W12 never lifts anything else.
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
  PriorContact,
  RecontactDecision,
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
  code: "unsubscribe_sync_not_automatic",
  label: "BAJA sin sincronización automática",
  detail:
    "Una BAJA se registra como supresión permanente sólo cuando un operador aplica un lote de respuestas ya descargadas. Las respuestas de Gmail no se sincronizan automáticamente todavía, así que ningún correo que ofrezca «responda BAJA» puede enviarse.",
};

const NOTE_LABEL: Record<string, string> = {
  unsubscribed: "Solicitó la BAJA",
  recontact_approved: "Recontacto aprobado (W12)",
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
  const [recontact, setRecontact] = useState<Record<string, RecontactDecision>>({});
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
      setRecontact({});
      setAcknowledged(false);
      setStep("review");
    } catch (err) {
      setError(refusalOf(err)?.message ?? (err instanceof Error ? err.message : String(err)));
    } finally {
      setBusy(false);
    }
  };

  const approved = (key: string) => recontact[key]?.decision === "approve";
  // An identity decision is needed only where it decides inclusion: a prior contact nobody
  // approved stays excluded whatever its identity.
  const reviewRows = useMemo(
    () => (preview ? preview.rows.filter((r) => r.review_codes.length && (!r.recontact_review_required || approved(r.key))) : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [preview, recontact],
  );
  const recontactRows = useMemo(() => (preview ? preview.rows.filter((r) => r.recontact_review_required) : []), [preview]);
  const undecided = reviewRows.filter((r) => !decisions[r.key] || decisions[r.key].note.trim().length < 3);
  const recontactIncomplete = Object.values(recontact).filter((d) => d.note.trim().length < 3).length;
  const serverProblems = (preview?.problems ?? []).filter((p) => p.code !== "review_pending");
  // Review rows are includable only once a person said so; a prior contact only once approved.
  const willInclude = preview
    ? preview.rows.filter(
        (r) =>
          (r.inclusion === "included" || (r.recontact_review_required && approved(r.key))) &&
          !excluded.has(r.key) &&
          (!r.review_codes.length || decisions[r.key]?.decision === "include"),
      ).length
    : 0;
  const recontactApproved = recontactRows.filter((r) => approved(r.key)).length;
  const canConfirm =
    preview !== null && undecided.length === 0 && recontactIncomplete === 0 && serverProblems.length === 0 && willInclude > 0;

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
        recontact_decisions: recontactRows
          .filter((r) => recontact[r.key])
          .map((r) => ({ ...recontact[r.key], key: r.key, note: recontact[r.key].note.trim() })),
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
          recontactRows={recontactRows}
          recontact={recontact}
          setRecontact={(updates) => setRecontact((all) => ({ ...all, ...updates }))}
          clearRecontact={(key) =>
            setRecontact((all) => {
              const next = { ...all };
              delete next[key];
              return next;
            })
          }
          recontactIncomplete={recontactIncomplete}
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
              {preview.recontact_review.enabled ? (
                <>
                  <Fact k="Revisión de recontacto (W12)" v={preview.recontact_review.policy_version ?? "—"} />
                  <Fact k="Recontacto aprobado" v={fmtInt(recontactApproved)} />
                  <Fact k="Contacto previo que sigue excluido" v={fmtInt(recontactRows.length - recontactApproved)} />
                </>
              ) : null}
            </dl>
            {recontactApproved ? (
              <p className="rounded-md border border-warn/40 bg-warn-bg px-2.5 py-1.5 text-warn" data-testid="recontact-summary">
                {fmtInt(recontactApproved)} destino(s) con contacto previo quedan incluidos por una decisión W12 con nota. La decisión se
                guarda con la instantánea y no se puede cambiar después.
              </p>
            ) : null}
            <p className="text-ink-muted">
              Al congelar se guarda una instantánea inmutable en <code>outbound.campaign_recipient</code>: cada destino con su decisión,
              todos sus motivos, su evidencia y fecha, la versión del contenido y la política. No se puede modificar; un cambio en el
              borrador o en la audiencia exige una versión nueva de la campaña y un congelamiento nuevo.
            </p>
            {preview.content.promises_baja ? (
              <p className="rounded-md border border-bad/40 bg-bad-bg px-2.5 py-1.5 text-bad" data-testid="baja-in-content">
                El contenido ofrece «BAJA». Hoy una BAJA sólo se registra cuando un operador aplica respuestas ya descargadas: Gmail no
                se sincroniza automáticamente, así que la promesa no puede cumplirse en un envío.
              </p>
            ) : null}
            <label className="flex items-start gap-2">
              <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} data-testid="freeze-ack" />
              <span>
                Entiendo que congelar <b>no envía nada</b>, que la audiencia congelada <b>no se puede modificar</b> y que el envío sigue
                bloqueado mientras las respuestas BAJA no se sincronicen automáticamente.
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
  recontactRows,
  recontact,
  setRecontact,
  clearRecontact,
  recontactIncomplete,
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
  recontactRows: FreezeRow[];
  recontact: Record<string, RecontactDecision>;
  setRecontact: (updates: Record<string, RecontactDecision>) => void;
  clearRecontact: (key: string) => void;
  recontactIncomplete: number;
  serverProblems: { code: string; message: string }[];
  canConfirm: boolean;
  onBack: () => void;
  onConfirm: () => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const included = preview.rows.filter((r) => r.inclusion === "included");
  const excludedRows = preview.rows.filter(
    (r) => r.inclusion === "excluded" && !r.recontact_review_required && !reviewRows.some((x) => x.key === r.key),
  );
  const priorOnlyWithoutW12 = preview.recontact_review.enabled
    ? 0
    : preview.rows.filter((r) => r.reasons.length === 1 && r.reasons[0].code === "prior_contact").length;
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

      {recontactRows.length ? (
        <RecontactPanel rows={recontactRows} recontact={recontact} setRecontact={setRecontact} clearRecontact={clearRecontact} />
      ) : null}
      {priorOnlyWithoutW12 ? (
        <p className="text-[11px] text-ink-muted" data-testid="recontact-disabled">
          {fmtInt(priorOnlyWithoutW12)} destino(s) quedan excluidos sólo por contacto previo. La revisión de recontacto (W12) no está
          habilitada en este entorno, así que siguen excluidos.
        </p>
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
        {recontactIncomplete ? (
          <span className="text-[10px] text-ink-faint">Faltan notas en {recontactIncomplete} decisión(es) de recontacto.</span>
        ) : null}
      </div>
    </div>
  );
}

const SOURCE_LABEL: Record<string, string> = {
  wave1a_union: "Historial V1 (ola 1A)",
  wave1a_rfc2047_addendum: "Historial V1 (ola 1A, anexo)",
  wave1b_prior_contact: "Historial V1 (ola 1B)",
  send_accepted: "Envío aceptado",
  operator_command: "Registrado por un operador",
};

function lastContact(pc: PriorContact | null): string {
  if (!pc) return "—";
  const when = pc.last_contact_at ? fmtDate(pc.last_contact_at) : "fecha desconocida";
  const where = pc.campaign_name ?? pc.sources.map((s) => SOURCE_LABEL[s.source] ?? s.source).join(" · ");
  return where ? `${when} · ${where}` : when;
}

/**
 * W12 — prior contacts an operator may approve for recontact in this campaign only. Each row
 * shows last contact, campaign or source and destination; each decision needs a note. A bulk
 * decision is applied only after the operator has looked at the exact list it covers, and is
 * still stored as one decision per recipient.
 */
function RecontactPanel({
  rows,
  recontact,
  setRecontact,
  clearRecontact,
}: {
  rows: FreezeRow[];
  recontact: Record<string, RecontactDecision>;
  setRecontact: (updates: Record<string, RecontactDecision>) => void;
  clearRecontact: (key: string) => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkDecision, setBulkDecision] = useState<RecontactDecision["decision"]>("approve");
  const [bulkNote, setBulkNote] = useState("");
  const [reviewing, setReviewing] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const chosen = rows.filter((r) => selected.has(r.key));
  const toggle = (key: string) =>
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const applyBulk = () => {
    const note = bulkNote.trim();
    setRecontact(Object.fromEntries(chosen.map((r) => [r.key, { key: r.key, decision: bulkDecision, note, mode: "bulk" as const }])));
    setSelected(new Set());
    setReviewing(false);
    setReviewed(false);
  };
  return (
    <Panel
      title="Contacto previo — revisión de recontacto (W12)"
      note="sin decisión quedan excluidos; W12 no levanta bloqueos, bajas, proveedores, rebotes, duplicados ni períodos de espera"
      bodyClassName="divide-y divide-line"
    >
      <div data-testid="recontact-panel" className="divide-y divide-line">
        {rows.map((r) => {
          const d = recontact[r.key];
          const pc = r.prior_contact;
          return (
            <div key={r.key} className="space-y-1.5 px-3 py-2" data-testid="recontact-row">
              <div className="flex flex-wrap items-center gap-2 text-[13px]">
                <input type="checkbox" checked={selected.has(r.key)} onChange={() => toggle(r.key)} aria-label={`Seleccionar ${r.address}`} />
                <span className="font-medium text-ink">{r.display_name ?? r.address}</span>
                <span className="text-ink-muted">Destino: {pc?.destination ?? r.address}</span>
                {r.organizations.map((o) => (
                  <Badge key={o.organization_id} tone="neutral">
                    {o.name ?? "Institución sin nombre"}
                  </Badge>
                ))}
                {d ? (
                  <Badge tone={d.decision === "approve" ? "warn" : "neutral"}>
                    {d.decision === "approve" ? "Recontacto aprobado" : "Se mantiene excluido"}
                    {d.mode === "bulk" ? " · selección" : ""}
                  </Badge>
                ) : (
                  <Badge tone="neutral">Sin decisión: excluido</Badge>
                )}
              </div>
              <p className="text-[11px] text-ink-muted" data-testid="recontact-last-contact">
                Último contacto: {lastContact(pc)}
              </p>
              <div className="flex flex-wrap items-center gap-3 text-xs">
                {(["approve", "keep_excluded"] as const).map((v) => (
                  <label key={v} className="inline-flex items-center gap-1">
                    <input type="radio" name={`w12-${r.key}`} checked={d?.decision === v}
                      onChange={() => setRecontact({ [r.key]: { key: r.key, decision: v, note: d?.note ?? "", mode: "individual" } })} />
                    {v === "approve" ? "Aprobar recontacto" : "Mantener excluido"}
                  </label>
                ))}
                <input
                  aria-label={`Nota de recontacto para ${r.address}`}
                  className={`${inputCls} max-w-sm`}
                  placeholder="Por qué (obligatorio)"
                  value={d?.note ?? ""}
                  disabled={!d}
                  onChange={(e) => setRecontact({ [r.key]: { ...d!, note: e.target.value, mode: "individual" } })}
                />
                {d ? (
                  <button type="button" className="text-[11px] text-ink-muted hover:underline" onClick={() => clearRecontact(r.key)}>
                    Quitar decisión
                  </button>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
      <div className="space-y-2 px-3 py-2" data-testid="recontact-bulk">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-ink-muted">{fmtInt(chosen.length)} seleccionado(s)</span>
          <select aria-label="Decisión para la selección" className={`${inputCls} w-48`} value={bulkDecision}
            onChange={(e) => setBulkDecision(e.target.value as RecontactDecision["decision"])}>
            <option value="approve">Aprobar recontacto</option>
            <option value="keep_excluded">Mantener excluido</option>
          </select>
          <input aria-label="Nota para la selección" className={`${inputCls} max-w-sm`} placeholder="Por qué (obligatorio, se guarda en cada uno)"
            value={bulkNote} onChange={(e) => setBulkNote(e.target.value)} />
          <button type="button" className={btnSecondary} disabled={!chosen.length || bulkNote.trim().length < 3}
            onClick={() => { setReviewing(true); setReviewed(false); }} data-testid="recontact-bulk-review">
            Revisar selección…
          </button>
        </div>
        {reviewing && chosen.length ? (
          <div className="space-y-2 rounded-md border border-warn/40 bg-warn-bg px-3 py-2 text-xs text-ink" data-testid="recontact-bulk-confirm">
            <p>
              <b>{bulkDecision === "approve" ? "Aprobar recontacto" : "Mantener excluidos"}</b> para estos {fmtInt(chosen.length)} destino(s),
              con la nota «{bulkNote.trim()}». Se guarda una decisión por destinatario.
            </p>
            <ul className="list-disc pl-5">
              {chosen.map((r) => (
                <li key={r.key}>
                  {r.address} — último contacto: {lastContact(r.prior_contact)}
                </li>
              ))}
            </ul>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} data-testid="recontact-bulk-ack" />
              Revisé cada destino de esta lista.
            </label>
            <div className="flex gap-2">
              <button type="button" className={btnSecondary} onClick={() => setReviewing(false)}>
                Cancelar
              </button>
              <button type="button" className={btnPrimary} disabled={!reviewed} onClick={applyBulk} data-testid="recontact-bulk-apply">
                Aplicar a {fmtInt(chosen.length)}
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </Panel>
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
                  <Fact k="Rechazados hoy (incluidos al congelar)" v={fmtInt(snap.suppressed_since_freeze ?? 0)} />
                  <Fact k="BAJA posterior al congelamiento" v={fmtInt(snap.unsubscribed_since_freeze ?? 0)} />
                </dl>
                <p className="text-[11px] text-ink-muted">
                  La instantánea no cambia. Lo que decide un envío futuro son los controles de hoy: una BAJA registrada después de congelar
                  deja al destino fuera aunque figure incluido.
                </p>
                <div className="divide-y divide-line rounded-md border border-line">
                  {snap.recipients.map((r) => (
                    <div key={r.recipient_id} className="flex flex-wrap items-center gap-2 px-3 py-1.5 text-[13px]" data-testid="frozen-row">
                      <Badge tone={r.inclusion === "included" ? "brand" : "neutral"}>{r.inclusion === "included" ? "Incluido" : "Excluido"}</Badge>
                      <span className="text-ink">{r.address}</span>
                      {r.organization_name ? <span className="text-ink-muted">{r.organization_name}</span> : null}
                      <span className="text-[11px] text-ink-muted">
                        {[...r.frozen_reasons.map((x) => REASON_LABEL[x] ?? x), ...r.frozen_notes.filter((n) => n in NOTE_LABEL && n !== "recontact_approved").map((n) => NOTE_LABEL[n])].join(" · ")}
                        {r.relevance === "sin_informacion" ? " Sin información" : ` ${r.interest_evidence.length} evidencia(s)`}
                        {r.evidence_observed_at ? ` · ${fmtDate(r.evidence_observed_at)}` : ""}
                        {r.identity_review ? ` · revisado: ${r.identity_review.decision === "include" ? "incluir" : "excluir"}` : ""}
                      </span>
                      {r.suppressed_since_freeze ? (
                        <Badge tone="bad" title={(r.send_time_refusals ?? []).map((x) => x.label).join(", ")}>
                          <span data-testid="refused-since-freeze">
                            {(r.send_time_refusals ?? []).some((x) => x.code === "unsubscribe")
                              ? "BAJA posterior al congelamiento"
                              : `Rechazado hoy: ${(r.send_time_refusals ?? []).map((x) => x.label).join(" · ")}`}
                          </span>
                        </Badge>
                      ) : null}
                      {r.recontact_review ? (
                        <span className="text-[11px] text-warn" data-testid="frozen-recontact">
                          W12: {r.recontact_review.decision === "approve" ? "recontacto aprobado" : "se mantiene excluido"}
                          {r.recontact_review.mode === "bulk" ? " (selección revisada)" : ""} — «{r.recontact_review.note}»
                        </span>
                      ) : null}
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
