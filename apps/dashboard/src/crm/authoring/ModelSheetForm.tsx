/**
 * «Catálogo web» form: one model's sheet for origenlab.cl, built from `modelos.schema.json`. It always
 * starts from the sheet the API read (or from a new one when the model has none), shows the schema's
 * own errors next to the field they name, and publishes through `webPublishApi`.
 * There is no price, stock, warranty or lead-time field: the site's schema rejects them.
 */
import { useState, type ReactNode } from "react";
import { Badge, Button, FormField, Panel, SelectInput, TextInput, TextareaInput, toast } from "../ui";
import { PublishStatus } from "./PublishStatus";
import {
  BRANDS,
  FAMILIES,
  LIMITS,
  MAX_APPLICATIONS,
  errorsAt,
  fromStored,
  normalizeErrors,
  pathLabel,
  toPayload,
  translateMessage,
  type ModelSheet,
  type SchemaError,
} from "../web/modelSheet";
import { publishModelSheet, publishRefusal, type ModelSheetRead, type PublishReceipt } from "../web/webPublishApi";

const today = () => new Date().toISOString().slice(0, 10);

function publishFailure(err: unknown): { text: string; errors: SchemaError[] } {
  const r = publishRefusal(err);
  if (!r) return { text: "No se pudo publicar. Revisa la conexión y vuelve a intentar.", errors: [] };
  if (r.code === "sheet_invalid") return { text: "No se publicó: la ficha no cumple el esquema. Revisa los campos marcados.", errors: normalizeErrors(r.errors) };
  if (r.code === "slug_mismatch") return { text: "No se publicó: el identificador de la ficha no coincide con el del modelo.", errors: [] };
  if (r.status === 429 || r.code === "publish_rate_limited") return { text: "Demasiadas publicaciones seguidas. Espera un momento y vuelve a intentar.", errors: [] };
  if (r.code === "commit_failed") return { text: "GitHub no guardó la ficha. No se publicó nada; vuelve a intentar.", errors: [] };
  if (r.code === "dispatch_failed") {
    const saved = r.commitSha ? ` (cambio ${r.commitSha.slice(0, 7)})` : "";
    return { text: `La ficha quedó guardada en GitHub${saved}, pero no se pudo iniciar el despliegue del sitio. Vuelve a publicar para reintentar.`, errors: [] };
  }
  if (r.status === 404 || r.code === "path_not_allowed") return { text: "Publicar web no está activado.", errors: [] };
  if (r.status === 401 || r.status === 403) return { text: "Sólo administración puede publicar en el sitio.", errors: [] };
  return { text: "No se pudo publicar. Revisa la conexión y vuelve a intentar.", errors: [] };
}

/** An input with a screen-reader label and the error of its own path under it. */
function Row({ id, label, errors, children }: { id: string; label: string; errors: string[]; children: ReactNode }) {
  return (
    <div className="min-w-0 flex-1">
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      {children}
      {errors.map((e) => (
        <p key={e} className="mt-0.5 text-[11px] text-bad">
          {e}
        </p>
      ))}
    </div>
  );
}

const move = <T,>(list: T[], i: number, next: T): T[] => list.map((v, j) => (j === i ? next : v));
const drop = <T,>(list: T[], i: number): T[] => list.filter((_, j) => j !== i);

function StringList({
  field, singular, addLabel, items, onChange, errors, max, maxLength, placeholder, disabled,
}: {
  field: string;
  singular: string;
  addLabel: string;
  items: string[];
  onChange: (items: string[]) => void;
  errors: SchemaError[];
  max?: number;
  maxLength?: number;
  placeholder?: string;
  disabled: boolean;
}) {
  const full = max !== undefined && items.length >= max;
  return (
    <div className="space-y-2">
      {items.map((item, i) => (
        <div key={i} className="flex items-start gap-2">
          <Row id={`${field}-${i}`} label={`${singular} ${i + 1}`} errors={errorsAt(errors, `${field}/${i}`)}>
            <TextInput id={`${field}-${i}`} value={item} onChange={(v) => onChange(move(items, i, v))} maxLength={maxLength} placeholder={placeholder} disabled={disabled} />
          </Row>
          <Button variant="quiet" aria-label={`Quitar ${singular.toLowerCase()} ${i + 1}`} onClick={() => onChange(drop(items, i))} disabled={disabled}>
            Quitar
          </Button>
        </div>
      ))}
      <Button onClick={() => onChange([...items, ""])} disabled={disabled || full}>
        {addLabel}
      </Button>
      {full ? <p className="text-[11px] text-ink-faint">Máximo {max}.</p> : null}
    </div>
  );
}

export function ModelSheetForm({ modelId, read }: { modelId: string; read: ModelSheetRead | null }) {
  const [initial] = useState(() => (read ? fromStored(read.sheet, modelId, today()) : { sheet: fromStored(null, modelId, today()).sheet, dropped: [] as string[] }));
  const [draft, setDraft] = useState<ModelSheet>(initial.sheet);
  const [errors, setErrors] = useState<SchemaError[]>(() => (read && !read.valid ? normalizeErrors(read.errors) : []));
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [published, setPublished] = useState<{ receipt: PublishReceipt; at: number } | null>(null);
  const dirty = JSON.stringify(toPayload(draft)) !== JSON.stringify(toPayload(initial.sheet));

  const set = <K extends keyof ModelSheet>(key: K, value: ModelSheet[K]) => setDraft((d) => ({ ...d, [key]: value }));
  const at = (path: string) => errorsAt(errors, path).join(" ") || null;

  async function publish() {
    setBusy(true);
    setFailure(null);
    try {
      const receipt = await publishModelSheet({ model_id: modelId, sheet: toPayload(draft) });
      setErrors([]);
      setPublished({ receipt, at: Date.now() });
      toast("Ficha enviada al sitio", "good");
    } catch (err) {
      const f = publishFailure(err);
      setFailure(f.text);
      setErrors(f.errors);
      setPublished(null);
    } finally {
      setBusy(false);
    }
  }

  function discard() {
    setDraft(initial.sheet);
    setErrors(read && !read.valid ? normalizeErrors(read.errors) : []);
    setFailure(null);
  }

  const withKnown = (value: string, options: readonly { value: string; label: string }[]) =>
    value && !options.some((o) => o.value === value) ? [...options, { value, label: `${value} (no válido)` }] : [...options];

  return (
    <form
      className="space-y-4"
      aria-label={`Ficha del modelo ${modelId}`}
      onSubmit={(e) => {
        e.preventDefault();
        void publish();
      }}
    >
      {read === null ? (
        <p role="status" className="rounded-lg border border-info/30 bg-info-bg px-3 py-2 text-xs text-ink">
          Ficha nueva: este modelo aún no tiene ficha en el sitio. Completa los campos obligatorios (*).
        </p>
      ) : (
        <div className="flex flex-wrap items-center gap-2 text-xs text-ink-muted">
          <Badge tone={read.valid ? "good" : "bad"}>{read.valid ? "Ficha válida" : "Ficha con errores"}</Badge>
          <a href={read.html_url} target="_blank" rel="noopener noreferrer" className="text-accent underline underline-offset-2">
            Ver el archivo en GitHub ↗
          </a>
        </div>
      )}
      {read && !read.valid ? (
        <p role="alert" className="rounded-lg border border-bad/30 bg-bad-bg px-3 py-2 text-xs text-ink">
          La ficha publicada no cumple el esquema del sitio. Corrige los campos marcados y publica de nuevo.
        </p>
      ) : null}
      {initial.dropped.length > 0 ? (
        <p role="status" className="rounded-lg border border-warn/30 bg-warn-bg px-3 py-2 text-xs text-ink">
          Se quitaron campos que la ficha no admite: {initial.dropped.join(", ")}. La ficha no lleva precio, stock, plazo ni garantía.
        </p>
      ) : null}

      <Panel title="Equipo" bodyClassName="grid grid-cols-1 gap-3 p-3 sm:grid-cols-2">
        <FormField label="Identificador (slug)" htmlFor="ms-slug" required hint="Igual al nombre del archivo; no se cambia." error={at("slug")}>
          <input id="ms-slug" value={draft.slug} readOnly className="w-full min-w-0 rounded-md border border-line bg-canvas-sunken px-2.5 py-1.5 font-mono text-xs text-ink-muted" />
        </FormField>
        <FormField label="Familia" htmlFor="ms-family" required error={at("family")}>
          <SelectInput id="ms-family" value={draft.family} onChange={(v) => set("family", v)} options={withKnown(draft.family, FAMILIES)} placeholder="Elige una familia" disabled={busy} />
        </FormField>
        <FormField label="Marca" htmlFor="ms-brand" required error={at("brand")}>
          <SelectInput id="ms-brand" value={draft.brand} onChange={(v) => set("brand", v)} options={withKnown(draft.brand, BRANDS)} placeholder="Elige una marca" disabled={busy} />
        </FormField>
        <FormField label="Nombre del fabricante" htmlFor="ms-name" required hint={`Nombre exacto del fabricante, hasta ${LIMITS.name} caracteres.`} error={at("name")}>
          <TextInput id="ms-name" value={draft.name} onChange={(v) => set("name", v)} maxLength={LIMITS.name} disabled={busy} />
        </FormField>
        <div className="sm:col-span-2">
          <FormField label="Titular" htmlFor="ms-headline" required hint={`Hasta ${LIMITS.headline} caracteres.`} error={at("headline")}>
            <TextInput id="ms-headline" value={draft.headline} onChange={(v) => set("headline", v)} maxLength={LIMITS.headline} disabled={busy} />
          </FormField>
        </div>
        <div className="sm:col-span-2">
          <FormField label="Texto de apertura" htmlFor="ms-lead" required hint={`${draft.lead.length} de ${LIMITS.lead} caracteres.`} error={at("lead")}>
            <TextareaInput id="ms-lead" value={draft.lead} onChange={(v) => set("lead", v)} maxLength={LIMITS.lead} rows={3} disabled={busy} />
          </FormField>
        </div>
      </Panel>

      <Panel title="Aplicaciones" note={`hasta ${MAX_APPLICATIONS}`} bodyClassName="p-3">
        <StringList field="applications" singular="Aplicación" addLabel="Añadir aplicación" items={draft.applications} onChange={(v) => set("applications", v)} errors={errors} max={MAX_APPLICATIONS} maxLength={LIMITS.application} disabled={busy} />
      </Panel>

      <Panel title="Especificaciones" note="cada cifra con la página o PDF del fabricante donde figura" bodyClassName="space-y-3 p-3">
        {draft.specs.map((s, i) => (
          <div key={i} className="grid grid-cols-1 items-start gap-2 sm:grid-cols-[1fr_1fr_2fr_auto]">
            <Row id={`spec-${i}-label`} label={`Especificación ${i + 1}: etiqueta`} errors={errorsAt(errors, `specs/${i}/label`)}>
              <TextInput id={`spec-${i}-label`} value={s.label} onChange={(v) => set("specs", move(draft.specs, i, { ...s, label: v }))} placeholder="Etiqueta (p. ej. Potencia)" disabled={busy} />
            </Row>
            <Row id={`spec-${i}-value`} label={`Especificación ${i + 1}: valor`} errors={errorsAt(errors, `specs/${i}/value`)}>
              <TextInput id={`spec-${i}-value`} value={s.value} onChange={(v) => set("specs", move(draft.specs, i, { ...s, value: v }))} placeholder="Valor" disabled={busy} />
            </Row>
            <Row id={`spec-${i}-source`} label={`Especificación ${i + 1}: fuente`} errors={[...errorsAt(errors, `specs/${i}/source`), ...errorsAt(errors, `specs/${i}`)]}>
              <TextInput id={`spec-${i}-source`} value={s.source} onChange={(v) => set("specs", move(draft.specs, i, { ...s, source: v }))} placeholder="https://… (fuente del fabricante)" disabled={busy} />
            </Row>
            <Button variant="quiet" aria-label={`Quitar especificación ${i + 1}`} onClick={() => set("specs", drop(draft.specs, i))} disabled={busy}>
              Quitar
            </Button>
          </div>
        ))}
        <Button onClick={() => set("specs", [...draft.specs, { label: "", value: "", source: "" }])} disabled={busy}>
          Añadir especificación
        </Button>
      </Panel>

      <Panel title="Accesorios y modelos relacionados" bodyClassName="grid grid-cols-1 gap-4 p-3 sm:grid-cols-2">
        <div>
          <p className="mb-2 text-xs font-medium text-ink">Accesorios</p>
          <StringList field="accessories" singular="Accesorio" addLabel="Añadir accesorio" items={draft.accessories} onChange={(v) => set("accessories", v)} errors={errors} maxLength={LIMITS.accessory} disabled={busy} />
        </div>
        <div>
          <p className="mb-2 text-xs font-medium text-ink">Modelos relacionados (identificadores)</p>
          <StringList field="related" singular="Modelo relacionado" addLabel="Añadir modelo relacionado" items={draft.related} onChange={(v) => set("related", v)} errors={errors} placeholder="otro-modelo" disabled={busy} />
        </div>
      </Panel>

      <Panel title="Preguntas frecuentes" bodyClassName="space-y-3 p-3">
        {draft.faqs.map((f, i) => (
          <div key={i} className="grid grid-cols-1 items-start gap-2 sm:grid-cols-[1fr_2fr_auto]">
            <Row id={`faq-${i}-q`} label={`Pregunta ${i + 1}`} errors={errorsAt(errors, `faqs/${i}/q`)}>
              <TextInput id={`faq-${i}-q`} value={f.q} onChange={(v) => set("faqs", move(draft.faqs, i, { ...f, q: v }))} placeholder="Pregunta" disabled={busy} />
            </Row>
            <Row id={`faq-${i}-a`} label={`Respuesta ${i + 1}`} errors={[...errorsAt(errors, `faqs/${i}/a`), ...errorsAt(errors, `faqs/${i}`)]}>
              <TextareaInput id={`faq-${i}-a`} value={f.a} onChange={(v) => set("faqs", move(draft.faqs, i, { ...f, a: v }))} placeholder="Respuesta" rows={2} disabled={busy} />
            </Row>
            <Button variant="quiet" aria-label={`Quitar pregunta ${i + 1}`} onClick={() => set("faqs", drop(draft.faqs, i))} disabled={busy}>
              Quitar
            </Button>
          </div>
        ))}
        <Button onClick={() => set("faqs", [...draft.faqs, { q: "", a: "" }])} disabled={busy}>
          Añadir pregunta
        </Button>
      </Panel>

      <Panel title="Fuentes y verificación" bodyClassName="grid grid-cols-1 gap-3 p-3 sm:grid-cols-2">
        <FormField label="Página oficial" htmlFor="ms-official" required hint="Página del fabricante, con https://" error={at("official_url")}>
          <TextInput id="ms-official" value={draft.official_url} onChange={(v) => set("official_url", v)} placeholder="https://…" disabled={busy} />
        </FormField>
        <FormField label="Página PDF oficial" htmlFor="ms-pdf" hint="Opcional." error={at("official_pdf_url")}>
          <TextInput id="ms-pdf" value={draft.official_pdf_url ?? ""} onChange={(v) => set("official_pdf_url", v)} placeholder="https://…" disabled={busy} />
        </FormField>
        <FormField label="Verificado el" htmlFor="ms-verified" required hint="Día en que se leyó la fuente oficial." error={at("verified_on")}>
          <TextInput id="ms-verified" type="date" value={draft.verified_on} onChange={(v) => set("verified_on", v)} disabled={busy} />
        </FormField>
        <FormField label="Sala 3D" htmlFor="ms-showroom" hint="Opcional: identificador de la escena." error={at("showroom_id")}>
          <TextInput id="ms-showroom" value={draft.showroom_id ?? ""} onChange={(v) => set("showroom_id", v)} disabled={busy} />
        </FormField>
        <FormField label="Imagen" htmlFor="ms-image" hint="Opcional: identificador de una imagen verificada." error={at("image_id")}>
          <TextInput id="ms-image" value={draft.image_id ?? ""} onChange={(v) => set("image_id", v)} disabled={busy} />
        </FormField>
      </Panel>

      {failure ? (
        <div role="alert" className="rounded-lg border border-bad/30 bg-bad-bg px-3 py-2 text-xs text-ink">
          <p className="font-semibold text-bad">{failure}</p>
          {errors.length > 0 ? (
            <ul className="mt-1 list-disc space-y-0.5 pl-4">
              {errors.map((e, i) => (
                <li key={`${e.path}-${i}`} title={e.message}>
                  {pathLabel(e.path)}: {translateMessage(e.message)}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {published ? (
        <PublishStatus modelId={modelId} commitSha={published.receipt.commit_sha} runUrl={published.receipt.run_url} since={published.at} />
      ) : null}

      <div className="sticky bottom-0 z-10 -mx-1 flex flex-wrap items-center justify-end gap-2 border-t border-line bg-canvas/95 px-1 py-3 backdrop-blur">
        {dirty ? <span className="mr-auto text-[11px] text-ink-faint">Cambios sin publicar</span> : null}
        <Button onClick={discard} disabled={busy || !dirty}>
          Descartar cambios
        </Button>
        <Button type="submit" variant="primary" busy={busy} busyLabel="Publicando…">
          Publicar en el sitio
        </Button>
      </div>
    </form>
  );
}
