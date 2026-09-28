import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge, Segmented, fmtDate } from "../ui";
import { AuthorOnlyNotice } from "./AuthorOnlyNotice";
import { useMayAuthorCampaigns } from "./authoring";
import { EmailFrame } from "./EmailFrame";
import { TEMPLATES, renderTemplate, type TemplateId } from "./emailTemplates";
import { createCampaignDraft, refusalOf, saveCampaignDraft, type DraftFields } from "./marketingApi";
import type { CampaignContent, EquipmentTaxonomy } from "./marketingTypes";

/**
 * Where a draft lives right now. The editor always shows exactly one of these, because an
 * operator must never believe a draft is saved when it exists only in this browser tab.
 */
export type Persistence =
  | { kind: "memory"; reason: "new" | "duplicate" | "drafts_disabled" }
  | { kind: "saved"; campaignId: string; version: number; savedAt: string | null; database: string; table: string; dirty: boolean }
  | { kind: "read_only"; campaignId: string; status: string; database: string };

export interface EditorSeed {
  /** The stored campaign being opened, or null for a new draft. */
  stored: CampaignContent | null;
  /** When the draft starts as a copy of another campaign. */
  duplicateOf?: CampaignContent | null;
}

const EMPTY: DraftFields = { name: "", subject: "", preheader: "", body_html: "", max_sends: 0, recontact_interval_days: 0 };

function fieldsOf(c: CampaignContent): DraftFields {
  return {
    name: c.name,
    subject: c.subject ?? "",
    preheader: c.preheader ?? "",
    body_html: c.body_html ?? "",
    max_sends: c.max_sends,
    recontact_interval_days: c.recontact_interval_days ?? 0,
  };
}

function same(a: DraftFields, b: DraftFields): boolean {
  return (Object.keys(a) as (keyof DraftFields)[]).every((k) => a[k] === b[k]);
}

export function initialPersistence(seed: EditorSeed, draftsEnabled: boolean): Persistence {
  const s = seed.stored;
  if (s && s.status !== "draft") return { kind: "read_only", campaignId: s.campaign_id, status: s.status, database: s.database };
  if (s) {
    return {
      kind: "saved", campaignId: s.campaign_id, version: s.version, savedAt: s.updated_at, database: s.database,
      table: "outbound.campaign", dirty: false,
    };
  }
  if (!draftsEnabled) return { kind: "memory", reason: "drafts_disabled" };
  return { kind: "memory", reason: seed.duplicateOf ? "duplicate" : "new" };
}

export function PersistenceBanner({ p, draftsEnabled }: { p: Persistence; draftsEnabled: boolean }) {
  if (p.kind === "read_only") {
    return (
      <div role="status" data-testid="persistence" data-state="read_only" className="rounded-md border border-line bg-canvas-sunken px-3 py-2 text-xs text-ink-muted">
        <b className="text-ink">Sólo lectura.</b> Campaña en estado «{p.status}» en <code>outbound.campaign</code> (base{" "}
        <code>{p.database}</code>): su contenido ya no se edita. Use «Duplicar» para partir de ella.
      </div>
    );
  }
  if (p.kind === "memory") {
    return (
      <div role="status" data-testid="persistence" data-state="memory" className="rounded-md border border-warn/40 bg-warn-bg px-3 py-2 text-xs text-warn">
        <b>Sin guardar.</b> Este borrador existe sólo en esta pestaña del navegador y se pierde al cerrarla.{" "}
        {draftsEnabled
          ? "«Guardar borrador» lo registra en outbound.campaign."
          : "El guardado de borradores no está habilitado en este entorno (ORIGENLAB_V2_CAMPAIGN_DRAFTS_ENABLED)."}
      </div>
    );
  }
  return (
    <div
      role="status"
      data-testid="persistence"
      data-state={p.dirty ? "dirty" : "saved"}
      className={`rounded-md border px-3 py-2 text-xs ${p.dirty ? "border-warn/40 bg-warn-bg text-warn" : "border-good/30 bg-good-bg text-good"}`}
    >
      {p.dirty ? (
        <>
          <b>Cambios sin guardar.</b> La versión {p.version} guardada en <code>{p.table}</code> (base <code>{p.database}</code>) no los incluye.
        </>
      ) : (
        <>
          <b>Guardado</b> en <code>{p.table}</code> · base <code>{p.database}</code> · versión {p.version}
          {p.savedAt ? ` · ${fmtDate(p.savedAt)} ${p.savedAt.slice(11, 16)}` : ""}
        </>
      )}
    </div>
  );
}

/** Every control in the editor writes or leads to a write, so a reader gets none of it. */
export function CampaignEditor(props: Parameters<typeof CampaignEditorForm>[0]) {
  const mayAuthor = useMayAuthorCampaigns();
  return mayAuthor ? <CampaignEditorForm {...props} /> : <AuthorOnlyNotice />;
}

function CampaignEditorForm({
  seed,
  taxonomy,
  draftsEnabled,
  onSaved,
  onDuplicate,
  onFreeze,
}: {
  seed: EditorSeed;
  taxonomy: EquipmentTaxonomy | null;
  draftsEnabled: boolean;
  onSaved: (campaignId: string) => void;
  onDuplicate: (content: CampaignContent) => void;
  /** Open the audience freeze for a saved, unchanged draft, or the snapshot of a frozen one. */
  onFreeze?: (campaignId: string) => void;
}) {
  const start = useMemo<DraftFields>(() => {
    if (seed.stored) return fieldsOf(seed.stored);
    if (seed.duplicateOf) return { ...fieldsOf(seed.duplicateOf), name: `Copia de ${seed.duplicateOf.name}` };
    return EMPTY;
  }, [seed]);
  const [fields, setFields] = useState<DraftFields>(start);
  const [baseline, setBaseline] = useState<DraftFields>(start);
  const [persistence, setPersistence] = useState<Persistence>(() => initialPersistence(seed, draftsEnabled));
  const [device, setDevice] = useState<"desktop" | "mobile">("desktop");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [blocked, setBlocked] = useState<{ images: string[]; removed: string[] }>({ images: [], removed: [] });
  const [template, setTemplate] = useState<TemplateId>("producto");
  const [brandId, setBrandId] = useState<string>(taxonomy?.brands[0]?.id ?? "");
  const [modelId, setModelId] = useState<string>("");

  const readOnly = persistence.kind === "read_only";
  const dirty = !same(fields, baseline);

  useEffect(() => {
    setPersistence((p) => (p.kind === "saved" && p.dirty !== dirty ? { ...p, dirty } : p));
  }, [dirty]);

  // Warn before closing the tab with unsaved work.
  useEffect(() => {
    const unsaved = persistence.kind === "memory" ? fields !== EMPTY && !same(fields, EMPTY) : persistence.kind === "saved" && dirty;
    if (!unsaved) return;
    const onBefore = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", onBefore);
    return () => window.removeEventListener("beforeunload", onBefore);
  }, [persistence, dirty, fields]);

  const set = (k: keyof DraftFields) => (v: string) =>
    setFields((f) => ({ ...f, [k]: k === "max_sends" || k === "recontact_interval_days" ? Number(v) || 0 : v }));

  const missing: string[] = [];
  if (!fields.name.trim()) missing.push("nombre");
  if (!(fields.max_sends >= 1)) missing.push("máximo de envíos");
  if (!(fields.recontact_interval_days >= 1)) missing.push("días de espera para recontactar");
  const canSave = draftsEnabled && !readOnly && !saving && missing.length === 0 && (persistence.kind === "memory" || dirty);

  const save = useCallback(async () => {
    setSaving(true);
    setError(null);
    try {
      const result =
        persistence.kind === "saved"
          ? await saveCampaignDraft(persistence.campaignId, persistence.version, fields)
          : await createCampaignDraft(fields, seed.duplicateOf?.campaign_id ?? null);
      setBaseline(fields);
      setPersistence({
        kind: "saved", campaignId: result.campaign_id, version: result.version, savedAt: result.saved_at,
        database: result.storage.database, table: result.storage.table, dirty: false,
      });
      onSaved(result.campaign_id);
    } catch (err) {
      const r = refusalOf(err);
      setError(
        r?.code === "path_not_allowed" || r?.code === "http_404"
          ? "El guardado no está disponible en este entorno. El borrador sigue sólo en esta pestaña."
          : r?.code === "stale_version"
            ? `${r.message}`
            : r?.message ?? (err instanceof Error ? err.message : String(err)),
      );
    } finally {
      setSaving(false);
    }
  }, [persistence, fields, seed.duplicateOf, onSaved]);

  const applyTemplate = () => {
    if (!taxonomy) return;
    if (fields.body_html.trim() && !window.confirm("¿Reemplazar el HTML actual por la plantilla?")) return;
    setFields((f) => ({ ...f, body_html: renderTemplate(taxonomy, { template, brandId, modelId: modelId || undefined }) }));
  };

  const onBlocked = useCallback((images: string[], removed: string[]) => {
    setBlocked((b) => (b.images.join() === images.join() && b.removed.join() === removed.join() ? b : { images, removed }));
  }, []);

  const brandModels = taxonomy?.models.filter((m) => m.brand_id === brandId) ?? [];
  const inputCls =
    "w-full rounded-md border border-line bg-canvas-raised px-2.5 py-1.5 text-xs text-ink placeholder:text-ink-faint focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600 disabled:opacity-60";

  return (
    <div className="space-y-4" data-testid="campaign-editor">
      <PersistenceBanner p={persistence} draftsEnabled={draftsEnabled} />

      <div className="grid gap-4 xl:grid-cols-[minmax(0,26rem)_minmax(0,1fr)]">
        <div className="space-y-3">
          <Field label="Nombre interno" htmlFor="c-name">
            <input id="c-name" className={inputCls} value={fields.name} disabled={readOnly} onChange={(e) => set("name")(e.target.value)} />
          </Field>
          <Field label="Asunto" htmlFor="c-subject" hint={`${fields.subject.length}/300`}>
            <input id="c-subject" className={inputCls} maxLength={300} value={fields.subject} disabled={readOnly} onChange={(e) => set("subject")(e.target.value)} />
          </Field>
          <Field label="Preencabezado" htmlFor="c-preheader" hint={`${fields.preheader.length}/255 · texto que la bandeja muestra tras el asunto`}>
            <input id="c-preheader" className={inputCls} maxLength={255} value={fields.preheader} disabled={readOnly} onChange={(e) => set("preheader")(e.target.value)} />
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Máximo de envíos" htmlFor="c-max">
              <input id="c-max" type="number" min={1} className={inputCls} value={fields.max_sends || ""} disabled={readOnly} onChange={(e) => set("max_sends")(e.target.value)} />
            </Field>
            <Field label="Días para recontactar" htmlFor="c-recontact">
              <input id="c-recontact" type="number" min={1} className={inputCls} value={fields.recontact_interval_days || ""} disabled={readOnly} onChange={(e) => set("recontact_interval_days")(e.target.value)} />
            </Field>
          </div>
          <p className="text-[10px] leading-4 text-ink-faint">
            Los dos límites son obligatorios en el esquema y son política de envío: el editor no los rellena por usted.
          </p>

          {!readOnly && taxonomy ? (
            <fieldset className="space-y-2 rounded-md border border-line p-2.5">
              <legend className="px-1 text-[11px] font-semibold uppercase tracking-wide text-ink-faint">Plantilla OrigenLab</legend>
              <select aria-label="Plantilla" className={inputCls} value={template} onChange={(e) => setTemplate(e.target.value as TemplateId)}>
                {TEMPLATES.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.label} — {t.description}
                  </option>
                ))}
              </select>
              {template === "producto" || template === "familia" ? (
                <div className="grid grid-cols-2 gap-2">
                  <select aria-label="Marca de la plantilla" className={inputCls} value={brandId} onChange={(e) => { setBrandId(e.target.value); setModelId(""); }}>
                    {taxonomy.brands.map((b) => (
                      <option key={b.id} value={b.id}>{b.name}</option>
                    ))}
                  </select>
                  {template === "producto" ? (
                    <select aria-label="Modelo de la plantilla" className={inputCls} value={modelId} onChange={(e) => setModelId(e.target.value)}>
                      <option value="">Primer modelo de la marca</option>
                      {brandModels.map((m) => (
                        <option key={m.id} value={m.id}>{m.name}</option>
                      ))}
                    </select>
                  ) : null}
                </div>
              ) : null}
              <button type="button" onClick={applyTemplate} className="h-7 rounded-md border border-line bg-canvas-raised px-2.5 text-xs font-medium text-ink hover:bg-canvas-sunken">
                Usar plantilla
              </button>
              <p className="text-[10px] leading-4 text-ink-faint">
                Imágenes verificadas del sitio (origenlab.cl). La plantilla no afirma nada del equipo salvo su nombre y familia.
              </p>
            </fieldset>
          ) : null}

          <Field label="HTML" htmlFor="c-html" hint={`${new Blob([fields.body_html]).size.toLocaleString("es-CL")} bytes`}>
            <textarea
              id="c-html"
              spellCheck={false}
              className={`${inputCls} h-64 font-mono text-[11px] leading-4`}
              value={fields.body_html}
              disabled={readOnly}
              onChange={(e) => set("body_html")(e.target.value)}
            />
          </Field>

          <div className="flex flex-wrap items-center gap-2">
            {!readOnly ? (
              <button
                type="button"
                onClick={() => void save()}
                disabled={!canSave}
                data-testid="save-draft"
                className="h-7 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black disabled:cursor-not-allowed disabled:opacity-40"
              >
                {saving ? "Guardando…" : persistence.kind === "saved" ? "Guardar cambios" : "Guardar borrador"}
              </button>
            ) : null}
            {seed.stored ? (
              <button
                type="button"
                onClick={() => onDuplicate({ ...seed.stored!, ...fieldsForCopy(fields) })}
                className="h-7 rounded-md border border-line bg-canvas-raised px-2.5 text-xs font-medium text-ink hover:bg-canvas-sunken"
              >
                Duplicar
              </button>
            ) : null}
            {onFreeze && persistence.kind === "saved" && !dirty ? (
              <button
                type="button"
                onClick={() => onFreeze(persistence.campaignId)}
                data-testid="open-freeze"
                className="h-7 rounded-md border border-line bg-canvas-raised px-2.5 text-xs font-medium text-ink hover:bg-canvas-sunken"
              >
                Congelar audiencia…
              </button>
            ) : null}
            {onFreeze && persistence.kind === "read_only" && persistence.status !== "archived" && persistence.status !== "cancelled" ? (
              <button
                type="button"
                onClick={() => onFreeze(persistence.campaignId)}
                data-testid="open-snapshot"
                className="h-7 rounded-md border border-line bg-canvas-raised px-2.5 text-xs font-medium text-ink hover:bg-canvas-sunken"
              >
                Ver audiencia congelada
              </button>
            ) : null}
            {!draftsEnabled && !readOnly ? (
              <span className="text-[10px] text-ink-faint">Guardado no habilitado en este entorno.</span>
            ) : missing.length && !readOnly ? (
              <span className="text-[10px] text-ink-faint">Falta: {missing.join(", ")}.</span>
            ) : null}
          </div>
          {error ? (
            <p role="alert" className="rounded-md border border-bad/30 bg-bad-bg px-2.5 py-1.5 text-xs text-bad">
              {error}
            </p>
          ) : null}
        </div>

        <div className="min-w-0 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <Segmented
              label="Vista previa"
              value={device}
              onChange={setDevice}
              options={[
                { value: "desktop", label: "Escritorio" },
                { value: "mobile", label: "Móvil" },
              ]}
            />
            <Badge tone="info" title="Sin scripts, sin formularios; imágenes sólo de origenlab.cl">
              Vista aislada
            </Badge>
          </div>
          <InboxRow subject={fields.subject} preheader={fields.preheader} />
          <div className="overflow-x-auto rounded-md border border-line bg-canvas-sunken p-3">
            <div className="mx-auto" style={{ width: device === "desktop" ? 640 : 375 }}>
              {fields.body_html.trim() ? (
                <EmailFrame
                  html={fields.body_html}
                  width={device === "desktop" ? 640 : 375}
                  height={760}
                  title={`Vista previa ${device === "desktop" ? "escritorio" : "móvil"}`}
                  onBlocked={onBlocked}
                />
              ) : (
                <div className="flex h-60 items-center justify-center rounded border border-dashed border-line-strong bg-canvas-raised text-xs text-ink-muted">
                  {seed.stored && !seed.stored.body_html ? "Contenido no importado" : "Sin HTML todavía"}
                </div>
              )}
            </div>
          </div>
          {blocked.images.length || blocked.removed.length ? (
            <p className="text-[11px] text-ink-muted" data-testid="preview-blocked">
              {blocked.images.length ? `${blocked.images.length} recurso(s) remoto(s) no cargado(s) en la vista previa (posibles píxeles de seguimiento). ` : ""}
              {blocked.removed.length ? `Omitido en la vista previa: ${blocked.removed.join(", ")}.` : ""}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function fieldsForCopy(f: DraftFields): Partial<CampaignContent> {
  return {
    name: f.name, subject: f.subject || null, preheader: f.preheader || null, body_html: f.body_html || null,
    max_sends: f.max_sends, recontact_interval_days: f.recontact_interval_days || null,
  };
}

function Field({ label, htmlFor, hint, children }: { label: string; htmlFor: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 flex items-baseline gap-2">
        <label htmlFor={htmlFor} className="text-[11px] font-semibold text-ink">
          {label}
        </label>
        {hint ? <span className="ml-auto text-[10px] text-ink-faint">{hint}</span> : null}
      </div>
      {children}
    </div>
  );
}

function InboxRow({ subject, preheader }: { subject: string; preheader: string }) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-line bg-canvas-raised px-3 py-2 text-xs" aria-label="Cómo se ve en la bandeja">
      <span className="shrink-0 font-semibold text-ink">OrigenLab</span>
      <span className="min-w-0 truncate">
        <span className="font-semibold text-ink">{subject || "(sin asunto)"}</span>
        <span className="text-ink-faint"> — {preheader || "(sin preencabezado)"}</span>
      </span>
    </div>
  );
}
