/**
 * «Datos para licitaciones» (Preferencias): who bids in Mercado Público — the bidder's data — and
 * the standard documents a tender asks for. Sales and administration only: a viewer sees no card
 * and nothing is read for them (the API refuses them too: 403 `role_may_not_view_bidder`).
 *
 * The profile is append-only: «Guardar» adds a new version and asks for a «Motivo» (at least 3
 * characters). The save names the profile the operator was looking at; if someone saved meanwhile the
 * API answers 409 `bidder_profile_changed` and the card says so, with the reload. «Persona natural»
 * is the default; «Empresa» adds the legal representative. A save that may have run keeps its
 * `Idempotency-Key` for the same retry. Documents (PDF, JPEG, PNG, DOCX or XLSX, up to 20 MiB) are
 * added once and retired once; «Abrir» asks for a link valid ten minutes.
 */
import { useId, useRef, useState, type FormEvent } from "react";
import { useAuthSession } from "../../context/AuthSessionContext";
import { Button, ResourceGate, Skeleton, fmtDateTime, toast } from "../ui";
import { useResource } from "../useResource";
import {
  BIDDER_DOCUMENT_KINDS,
  MAX_BIDDER_DOCUMENT_BYTES,
  addBidderDocument,
  bidderDocumentUrl,
  fetchBidder,
  newTenderCommandKey,
  retireBidderDocument,
  saveBidderProfile,
  tenderRefusal,
  type BankAccountType,
  type BidderDocument,
  type BidderDocumentKind,
  type BidderKind,
  type BidderProfile,
  type BidderProfileBody,
  type ChileProveedoresStatus,
} from "./tenderApi";

const REASON_MIN = 3;
const FIELD_CLS =
  "w-full min-w-0 rounded-lg border border-line-strong bg-canvas-raised px-3 text-[14px] text-ink placeholder:text-ink-faint focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600 aria-[invalid=true]:border-bad disabled:cursor-not-allowed disabled:bg-canvas-sunken disabled:text-ink-muted";

const REFUSAL_ES: Record<string, string> = {
  rut_invalid: "El RUT no es válido; revisa el número y el dígito verificador.",
  legal_rep_only_for_company: "Solo una empresa tiene representante legal.",
  unsupported_document: "Solo se aceptan PDF, JPEG, PNG, DOCX o XLSX.",
  document_too_large: "El archivo pesa más de 20 MB.",
  bidder_document_already_added: "Ese documento ya está en los datos del oferente.",
  document_already_retired: "Ese documento ya estaba retirado.",
  document_not_found: "No existe ese documento; recarga la página.",
  role_may_not_view_bidder: "Solo ventas y administración ven los datos del oferente.",
  forbidden: "Solo ventas y administración pueden cambiar los datos del oferente.",
};
const GENERIC = "No se pudo completar. Intenta de nuevo.";

function refusalText(err: unknown): string {
  const r = tenderRefusal(err);
  if (!r) return GENERIC;
  return REFUSAL_ES[r.code] ?? (r.status >= 400 && r.status < 500 && r.message && !r.message.startsWith("{") ? r.message : GENERIC);
}

/** A 4xx with a code is final, so the next attempt may take a fresh key; anything else may have run. */
function definitive(err: unknown): boolean {
  const r = tenderRefusal(err);
  return !!r && r.status >= 400 && r.status < 500 && !r.code.startsWith("http_");
}

export function BidderPreferences() {
  const { session } = useAuthSession();
  if (session.kind !== "signed_in" || session.operator.role === "viewer") return null;
  return <BidderCard />;
}

function BidderCard() {
  const headingId = useId();
  const [state, reload] = useResource(fetchBidder);
  // The API refuses a viewer (or a session it no longer trusts): the card is simply not there.
  if (state.kind === "permission") return null;
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-4 rounded-[14px] border border-line bg-canvas-raised px-[18px] py-4">
      <div>
        <h2 id={headingId} className="text-[15px] font-semibold text-ink">
          Datos para licitaciones
        </h2>
        <p className="mt-0.5 text-[12.5px] text-ink-muted">
          Quién postula en Mercado Público y sus documentos. Cada cambio pide un motivo y queda guardado como una versión nueva.
        </p>
      </div>
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={4} />}>
        {(view) => (
          <>
            <ProfileForm key={view.profile?.id ?? "none"} profile={view.profile} onSaved={reload} onReload={reload} />
            <DocumentsBlock documents={view.documents} onChanged={reload} />
          </>
        )}
      </ResourceGate>
    </section>
  );
}

/* ── the profile ──────────────────────────────────────────────────────────── */

const text = (v: string | null | undefined) => v ?? "";
const orNull = (v: string) => (v.trim() === "" ? null : v.trim());

function ProfileForm({ profile, onSaved, onReload }: { profile: BidderProfile | null; onSaved: () => void; onReload: () => void }) {
  const [kind, setKind] = useState<BidderKind>(profile?.bidder_kind ?? "natural_person");
  const [v, setV] = useState({
    legal_name: text(profile?.legal_name),
    rut: text(profile?.rut && formatRut(profile.rut)),
    giro: text(profile?.giro),
    address: text(profile?.address),
    comuna: text(profile?.comuna),
    region: text(profile?.region),
    phone: text(profile?.phone),
    email: text(profile?.email),
    legal_rep_name: text(profile?.legal_rep_name),
    legal_rep_rut: text(profile?.legal_rep_rut && formatRut(profile.legal_rep_rut)),
    bank_name: text(profile?.bank_name),
    bank_account_type: text(profile?.bank_account_type),
    bank_account_number: text(profile?.bank_account_number),
    chileproveedores_status: text(profile?.chileproveedores_status),
    chileproveedores_checked_on: text(profile?.chileproveedores_checked_on),
    reason: "",
  });
  const [isEmt, setIsEmt] = useState(profile?.is_emt ?? false);
  const [isLocal, setIsLocal] = useState(profile?.is_local_supplier ?? false);
  const [problems, setProblems] = useState<Partial<Record<"legal_name" | "rut" | "legal_rep_rut" | "reason" | "form", string>>>({});
  const [busy, setBusy] = useState(false);
  const [changed, setChanged] = useState(false);
  const keyRef = useRef<{ sig: string; key: string } | null>(null);

  const set = (name: keyof typeof v) => (value: string) => {
    setV((prev) => ({ ...prev, [name]: value }));
    setProblems({});
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy || changed) return;
    const found: typeof problems = {};
    if (!v.legal_name.trim()) found.legal_name = "Escribe el nombre o la razón social.";
    if (!v.rut.trim()) found.rut = "Escribe el RUT.";
    if (v.reason.trim().length < REASON_MIN) found.reason = `Escribe un motivo de al menos ${REASON_MIN} caracteres.`;
    if (Object.keys(found).length > 0) {
      setProblems(found);
      return;
    }
    const company = kind === "company";
    const body: BidderProfileBody = {
      bidder_kind: kind,
      legal_name: v.legal_name.trim(),
      rut: v.rut.trim(),
      giro: orNull(v.giro),
      address: orNull(v.address),
      comuna: orNull(v.comuna),
      region: orNull(v.region),
      phone: orNull(v.phone),
      email: orNull(v.email),
      ...(company ? { legal_rep_name: orNull(v.legal_rep_name), legal_rep_rut: orNull(v.legal_rep_rut) } : {}),
      bank_name: orNull(v.bank_name),
      bank_account_type: (orNull(v.bank_account_type) as BankAccountType | null) ?? null,
      bank_account_number: orNull(v.bank_account_number),
      chileproveedores_status: (orNull(v.chileproveedores_status) as ChileProveedoresStatus | null) ?? null,
      chileproveedores_checked_on: orNull(v.chileproveedores_checked_on),
      is_emt: isEmt,
      is_local_supplier: isLocal,
      reason: v.reason.trim(),
      expected_current_id: profile?.id ?? null,
    };
    const sig = JSON.stringify(body);
    if (keyRef.current?.sig !== sig) keyRef.current = { sig, key: newTenderCommandKey() };
    setBusy(true);
    setProblems({});
    try {
      await saveBidderProfile(body, keyRef.current.key);
      keyRef.current = null;
      toast("Datos para licitaciones guardados");
      onSaved();
    } catch (err) {
      if (definitive(err)) keyRef.current = null;
      const r = tenderRefusal(err);
      if (r?.code === "bidder_profile_changed") setChanged(true);
      else if (r?.code === "rut_invalid") {
        const text = REFUSAL_ES.rut_invalid;
        setProblems(/representante/i.test(r.message) ? { legal_rep_rut: "El RUT del representante no es válido; revisa el número y el dígito verificador." } : { rut: text });
      } else setProblems({ form: refusalText(err) });
    } finally {
      setBusy(false);
    }
  };

  const company = kind === "company";
  return (
    <form onSubmit={(e) => void submit(e)} noValidate aria-label="Datos del oferente" className="flex flex-col gap-3">
      <div role="radiogroup" aria-label="Tipo de oferente" className="flex flex-wrap gap-2">
        {(
          [
            ["natural_person", "Persona natural"],
            ["company", "Empresa"],
          ] as const
        ).map(([value, label]) => (
          <label
            key={value}
            className={`flex min-h-[44px] cursor-pointer items-center gap-2 rounded-lg border px-4 text-[14px] ${kind === value ? "border-brand-600 bg-canvas-sunken font-semibold text-ink" : "border-line-strong text-ink-muted"}`}
          >
            <input type="radio" name="bidder-kind" value={value} checked={kind === value} onChange={() => setKind(value)} />
            {label}
          </label>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        <Field label="Nombre o razón social" value={v.legal_name} onChange={set("legal_name")} problem={problems.legal_name} maxLength={200} />
        <Field label="RUT" value={v.rut} onChange={set("rut")} problem={problems.rut} hint="Ejemplo: 61.111.111-8" maxLength={20} />
        <Field label="Giro" value={v.giro} onChange={set("giro")} maxLength={200} />
        <Field label="Dirección" value={v.address} onChange={set("address")} maxLength={200} />
        <Field label="Comuna" value={v.comuna} onChange={set("comuna")} maxLength={200} />
        <Field label="Región" value={v.region} onChange={set("region")} maxLength={200} />
        <Field label="Teléfono" value={v.phone} onChange={set("phone")} type="tel" maxLength={40} />
        <Field label="Correo" value={v.email} onChange={set("email")} type="email" maxLength={200} />
        {company ? (
          <>
            <Field label="Representante legal" value={v.legal_rep_name} onChange={set("legal_rep_name")} maxLength={200} />
            <Field label="RUT representante" value={v.legal_rep_rut} onChange={set("legal_rep_rut")} problem={problems.legal_rep_rut} maxLength={20} />
          </>
        ) : null}
        <Field label="Banco" value={v.bank_name} onChange={set("bank_name")} maxLength={200} />
        <SelectField
          label="Tipo de cuenta"
          value={v.bank_account_type}
          onChange={set("bank_account_type")}
          options={[
            ["", "Sin dato"],
            ["corriente", "Cuenta corriente"],
            ["vista", "Cuenta vista"],
            ["ahorro", "Cuenta de ahorro"],
            ["rut", "Cuenta RUT"],
          ]}
        />
        <Field label="Número de cuenta" value={v.bank_account_number} onChange={set("bank_account_number")} inputMode="numeric" maxLength={40} />
        <SelectField
          label="ChileProveedores"
          value={v.chileproveedores_status}
          onChange={set("chileproveedores_status")}
          options={[
            ["", "Sin dato"],
            ["habil", "Hábil"],
            ["no_habil", "No hábil"],
            ["no_inscrito", "No inscrito"],
          ]}
        />
        <Field label="Revisado el" value={v.chileproveedores_checked_on} onChange={set("chileproveedores_checked_on")} type="date" />
      </div>

      <div className="flex flex-wrap gap-x-6 gap-y-1">
        <Check label="Empresa de menor tamaño (EMT)" checked={isEmt} onChange={setIsEmt} />
        <Check label="Proveedor local" checked={isLocal} onChange={setIsLocal} />
      </div>

      <div className="flex max-w-xl flex-col gap-1">
        <Field label="Motivo" value={v.reason} onChange={set("reason")} problem={problems.reason} hint="Por qué cambia, al menos 3 caracteres; queda en el historial." maxLength={2000} />
      </div>

      {changed ? (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-warn/30 bg-warn-bg px-3 py-2">
          <span className="text-[13px] font-semibold text-warn">Otra persona cambió estos datos. Recarga para ver la versión actual.</span>
          <Button variant="primary" className="min-h-[44px] px-4" onClick={onReload}>
            Recargar
          </Button>
        </div>
      ) : null}
      {problems.form ? (
        <p role="alert" className="text-[12.5px] text-bad">
          {problems.form}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" variant="primary" className="min-h-[44px] px-4 text-[14px]" busy={busy} busyLabel="Guardando…" disabled={changed}>
          Guardar
        </Button>
        {profile ? (
          <span className="text-[12px] text-ink-muted">
            Versión vigente: {profile.set_by ?? "carga inicial"} · {fmtDateTime(profile.created_at)}
          </span>
        ) : (
          <span className="text-[12px] text-ink-muted">Todavía no hay datos guardados.</span>
        )}
      </div>
    </form>
  );
}

function formatRut(rut: string): string {
  const m = /^(\d{1,8})-([0-9K])$/.exec(rut);
  return m ? `${m[1].replace(/\B(?=(\d{3})+(?!\d))/g, ".")}-${m[2]}` : rut;
}

function Field({
  label,
  value,
  onChange,
  problem,
  hint,
  type = "text",
  inputMode,
  maxLength,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  problem?: string;
  hint?: string;
  type?: string;
  inputMode?: "numeric";
  maxLength?: number;
}) {
  const id = useId();
  const describedBy = [hint ? `${id}-hint` : null, problem ? `${id}-problem` : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className="flex min-w-0 flex-col gap-[5px]">
      <label htmlFor={id} className="text-[12.5px] font-medium text-ink-muted">
        {label}
      </label>
      <input
        id={id}
        type={type}
        inputMode={inputMode}
        value={value}
        maxLength={maxLength}
        aria-invalid={problem ? true : undefined}
        aria-describedby={describedBy}
        onChange={(e) => onChange(e.target.value)}
        className={`${FIELD_CLS} h-11`}
      />
      {hint ? (
        <span id={`${id}-hint`} className="text-[11.5px] text-ink-muted">
          {hint}
        </span>
      ) : null}
      {problem ? (
        <span id={`${id}-problem`} role="alert" className="text-[12px] text-bad">
          {problem}
        </span>
      ) : null}
    </div>
  );
}

function SelectField({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: readonly (readonly [string, string])[];
}) {
  const id = useId();
  return (
    <div className="flex min-w-0 flex-col gap-[5px]">
      <label htmlFor={id} className="text-[12.5px] font-medium text-ink-muted">
        {label}
      </label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={`${FIELD_CLS} h-11`}>
        {options.map(([optionValue, optionLabel]) => (
          <option key={optionValue} value={optionValue}>
            {optionLabel}
          </option>
        ))}
      </select>
    </div>
  );
}

function Check({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return (
    <label className="flex min-h-[44px] cursor-pointer items-center gap-2 text-[14px] text-ink">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="h-4 w-4" />
      {label}
    </label>
  );
}

/* ── the documents ────────────────────────────────────────────────────────── */

const KIND_LABEL: Record<string, string> = Object.fromEntries(BIDDER_DOCUMENT_KINDS.map((k) => [k.value, k.label]));

function DocumentsBlock({ documents, onChanged }: { documents: BidderDocument[]; onChanged: () => void }) {
  const headingId = useId();
  return (
    <div aria-labelledby={headingId} role="group" className="flex flex-col gap-3 border-t border-line pt-4">
      <h3 id={headingId} className="text-[14px] font-semibold text-ink">
        Documentos del oferente
      </h3>
      {documents.length === 0 ? (
        <p className="text-[12.5px] text-ink-muted">Todavía no hay documentos.</p>
      ) : (
        <ul className="rounded-lg border border-line">
          {documents.map((d) => (
            <DocumentRow key={d.id} document={d} onChanged={onChanged} />
          ))}
        </ul>
      )}
      <UploadForm onAdded={onChanged} />
    </div>
  );
}

function DocumentRow({ document: d, onChanged }: { document: BidderDocument; onChanged: () => void }) {
  const reasonId = useId();
  const [retiring, setRetiring] = useState(false);
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState<string | null>(null);
  const keyRef = useRef<{ sig: string; key: string } | null>(null);

  // A window opened after an await is popup-blocked (Safari): fetch the link, then show it as a real link.
  const open = async () => {
    try {
      setLink(await bidderDocumentUrl(d.id));
    } catch (err) {
      toast(refusalText(err), "bad");
    }
  };

  const retire = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    const why = reason.trim();
    if (why.length < REASON_MIN) {
      setProblem(`Escribe un motivo de al menos ${REASON_MIN} caracteres.`);
      return;
    }
    const sig = JSON.stringify([d.id, why]);
    if (keyRef.current?.sig !== sig) keyRef.current = { sig, key: newTenderCommandKey() };
    setBusy(true);
    setProblem(null);
    try {
      await retireBidderDocument(d.id, why, keyRef.current.key);
      keyRef.current = null;
      toast("Documento retirado");
      onChanged();
    } catch (err) {
      if (definitive(err)) keyRef.current = null;
      setProblem(refusalText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="border-t border-line px-3 py-2 first:border-t-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-[13.5px] font-medium text-ink">{d.title}</p>
          <p className="text-[12px] text-ink-muted">
            {KIND_LABEL[d.kind] ?? d.kind} · {d.original_filename} · {fmtDateTime(d.added_at)}
          </p>
        </div>
        <div className="flex gap-2">
          <Button className="min-h-[44px] px-3 text-[13px]" aria-label={`Abrir ${d.title}`} onClick={() => void open()}>
            Abrir
          </Button>
          <Button
            variant="quiet"
            className="min-h-[44px] px-3 text-[13px]"
            aria-label={`Retirar ${d.title}`}
            aria-expanded={retiring}
            onClick={() => setRetiring((x) => !x)}
          >
            Retirar
          </Button>
        </div>
      </div>
      {link ? (
        <p className="mt-2 text-[13px]">
          <a
            href={link}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`Abrir ${d.title} ahora`}
            className="inline-flex min-h-[44px] items-center font-semibold text-brand-600 underline"
          >
            Abrir ahora
          </a>
          <span className="ml-2 text-[12px] text-ink-muted">El enlace vale 10 minutos.</span>
        </p>
      ) : null}
      {retiring ? (
        <form onSubmit={(e) => void retire(e)} noValidate className="mt-2 flex flex-col gap-2">
          <div className="flex max-w-xl flex-col gap-[5px]">
            <label htmlFor={reasonId} className="text-[12.5px] font-medium text-ink-muted">
              Motivo del retiro
            </label>
            <input
              id={reasonId}
              value={reason}
              maxLength={2000}
              aria-invalid={problem ? true : undefined}
              aria-describedby={problem ? `${reasonId}-problem` : undefined}
              onChange={(e) => {
                setReason(e.target.value);
                setProblem(null);
              }}
              className={`${FIELD_CLS} h-11`}
            />
            {problem ? (
              <span id={`${reasonId}-problem`} role="alert" className="text-[12px] text-bad">
                {problem}
              </span>
            ) : null}
          </div>
          <div className="flex gap-2">
            <Button type="submit" variant="primary" className="min-h-[44px] px-4 text-[14px]" busy={busy} busyLabel="Retirando…">
              Confirmar retiro
            </Button>
            <Button className="min-h-[44px] px-4 text-[14px]" disabled={busy} onClick={() => setRetiring(false)}>
              Cancelar
            </Button>
          </div>
        </form>
      ) : null}
    </li>
  );
}

function UploadForm({ onAdded }: { onAdded: () => void }) {
  const kindId = useId();
  const titleId = useId();
  const fileId = useId();
  const [kind, setKind] = useState<BidderDocumentKind>("cedula");
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [inputKey, setInputKey] = useState(0);
  const keyRef = useRef<{ sig: string; key: string } | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    if (!file) return setProblem("Elige el archivo.");
    if (!title.trim()) return setProblem("Escribe un título para el documento.");
    if (file.size > MAX_BIDDER_DOCUMENT_BYTES) return setProblem(REFUSAL_ES.document_too_large);
    const sig = JSON.stringify([kind, title.trim(), file.name, file.size, file.lastModified]);
    if (keyRef.current?.sig !== sig) keyRef.current = { sig, key: newTenderCommandKey() };
    setBusy(true);
    setProblem(null);
    try {
      await addBidderDocument(file, kind, title.trim(), keyRef.current.key);
      keyRef.current = null;
      toast("Documento agregado");
      setTitle("");
      setFile(null);
      setInputKey((n) => n + 1);
      onAdded();
    } catch (err) {
      if (definitive(err)) keyRef.current = null;
      setProblem(refusalText(err));
    } finally {
      setBusy(false);
    }
  };

  const changed = (set: () => void) => {
    set();
    setProblem(null);
  };
  return (
    <form onSubmit={(e) => void submit(e)} noValidate aria-label="Agregar un documento" className="flex flex-col gap-3">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <div className="flex min-w-0 flex-col gap-[5px]">
          <label htmlFor={kindId} className="text-[12.5px] font-medium text-ink-muted">
            Tipo de documento
          </label>
          <select id={kindId} value={kind} onChange={(e) => changed(() => setKind(e.target.value as BidderDocumentKind))} className={`${FIELD_CLS} h-11`}>
            {BIDDER_DOCUMENT_KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </select>
        </div>
        <div className="flex min-w-0 flex-col gap-[5px]">
          <label htmlFor={titleId} className="text-[12.5px] font-medium text-ink-muted">
            Título del documento
          </label>
          <input id={titleId} value={title} maxLength={200} onChange={(e) => changed(() => setTitle(e.target.value))} className={`${FIELD_CLS} h-11`} />
        </div>
        <div className="flex min-w-0 flex-col gap-[5px]">
          <label htmlFor={fileId} className="text-[12.5px] font-medium text-ink-muted">
            Archivo
          </label>
          <input
            key={inputKey}
            id={fileId}
            type="file"
            accept=".pdf,.jpg,.jpeg,.png,.docx,.xlsx"
            onChange={(e) => changed(() => setFile(e.target.files?.[0] ?? null))}
            className="min-h-[44px] text-[13px] text-ink"
          />
          <span className="text-[11.5px] text-ink-muted">PDF, JPEG, PNG, DOCX o XLSX; hasta 20 MB.</span>
        </div>
      </div>
      {problem ? (
        <p role="alert" className="text-[12.5px] text-bad">
          {problem}
        </p>
      ) : null}
      <div>
        <Button type="submit" variant="primary" className="min-h-[44px] px-4 text-[14px]" busy={busy} busyLabel="Subiendo…">
          Subir
        </Button>
      </div>
    </form>
  );
}

