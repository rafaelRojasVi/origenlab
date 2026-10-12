/**
 * The case's purchase order in the panel (owner request 2026-10-12): which OC closes it, open it,
 * attach one when the case is won (from the computer or from a case email), remove a wrong one.
 *
 * - `OcPicker` — «Sin OC» / «De un correo del caso» / «Subir archivo»; used by «Marcar ganada»
 *   and by «Adjuntar OC». Likely purchase orders from the case's emails come first.
 * - `CaseDocumentsBlock` — the attached OCs (number, file, who and when, «Abrir», «Quitar») and
 *   «Adjuntar OC» on a won or negotiating case.
 */
import { useCallback, useRef, useState, type FormEvent } from "react";
import {
  ACCEPTED_DOCUMENT_TYPES,
  MAX_DOCUMENT_BYTES,
  type EmailDocumentOption,
  type OcChoice,
  attachPurchaseOrder,
  documentRefusalText,
  emailDocumentOptions,
  fetchCaseDocumentUrl,
  ocLabel,
  removeCaseDocument,
  useMayAttachCaseDocuments,
} from "../caseDocuments";
import { newCaseCommandKey } from "../caseCommands";
import { fetchCaseMailDocuments, fetchCasePurchaseOrderCandidates } from "../crmApi";
import type { CaseDocument, OpportunityCardData } from "../crmTypes";
import { Button, FormField, SelectInput, TextInput, fmtDate, fmtDateTime } from "../ui";
import { useResource } from "../useResource";

/* ── the picker ───────────────────────────────────────────────────────────── */

type Mode = "none" | "email" | "file";

function optionLabel(o: EmailDocumentOption): string {
  const when = o.sent_at ? ` · ${fmtDate(o.sent_at)}` : "";
  return `${o.filename ?? "PDF sin nombre"}${when}${o.subject ? ` · ${o.subject}` : ""}`;
}

export function OcPicker({
  opportunityId,
  onChange,
  number,
  onNumber,
  allowNone = true,
  idPrefix = "oc",
}: {
  opportunityId: string;
  onChange: (choice: OcChoice | null) => void;
  number: string;
  onNumber: (n: string) => void;
  allowNone?: boolean;
  idPrefix?: string;
}) {
  const load = useCallback(async () => {
    const [mail, candidates] = await Promise.all([
      fetchCaseMailDocuments(opportunityId).catch(() => null),
      fetchCasePurchaseOrderCandidates(opportunityId).catch(() => null),
    ]);
    return emailDocumentOptions(mail, candidates);
  }, [opportunityId]);
  const [state] = useResource(load, [opportunityId, "oc-options"]);
  const options = state.kind === "ready" ? state.data : [];
  const [mode, setMode] = useState<Mode>(allowNone ? "none" : "email");
  const [picked, setPicked] = useState("");
  const [fileError, setFileError] = useState<string | null>(null);

  function choose(next: Mode) {
    setMode(next);
    setFileError(null);
    if (next === "none") onChange(null);
    if (next === "email") pickEmail(picked || (options[0] ? `${options[0].source_record_id}:${options[0].document_sha256}` : ""));
    if (next === "file") onChange(null);
  }

  function pickEmail(value: string) {
    setPicked(value);
    const o = options.find((x) => `${x.source_record_id}:${x.document_sha256}` === value);
    onChange(o ? { kind: "email", source_record_id: o.source_record_id, document_sha256: o.document_sha256, filename: o.filename } : null);
  }

  function pickFile(file: File | null) {
    setFileError(null);
    if (!file) return onChange(null);
    if (file.size > MAX_DOCUMENT_BYTES) {
      setFileError("El archivo pesa más de 20 MB.");
      return onChange(null);
    }
    onChange({ kind: "file", file });
  }

  const modes: { value: Mode; label: string }[] = [
    ...(allowNone ? [{ value: "none" as Mode, label: "Sin OC" }] : []),
    { value: "email", label: "De un correo del caso" },
    { value: "file", label: "Subir archivo" },
  ];

  return (
    <fieldset className="space-y-2 rounded-md bg-canvas-sunken/50 p-2.5" data-testid="oc-picker">
      <legend className="px-1 text-xs font-medium text-ink">Orden de compra{allowNone ? " (opcional)" : ""}</legend>
      <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Origen de la orden de compra">
        {modes.map((m) => (
          <button
            key={m.value}
            type="button"
            role="radio"
            aria-checked={mode === m.value}
            onClick={() => choose(m.value)}
            className={`rounded-md border px-2.5 py-1 text-xs font-medium ${
              mode === m.value ? "border-brand-600 bg-brand-50 text-brand-900" : "border-line bg-canvas-raised text-ink-muted hover:text-ink"
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>
      {mode === "email" ? (
        state.kind === "loading" ? (
          <p className="text-xs text-ink-muted">Buscando PDF en los correos del caso…</p>
        ) : options.length === 0 ? (
          <p className="text-xs text-ink-muted">No hay PDF en los correos del caso. Usa «Subir archivo».</p>
        ) : (
          <FormField label="PDF del correo" htmlFor={`${idPrefix}-email`}>
            <SelectInput
              id={`${idPrefix}-email`}
              value={picked}
              onChange={pickEmail}
              options={[
                { value: "", label: "Elige el PDF de la OC…" },
                ...options.map((o) => ({
                  value: `${o.source_record_id}:${o.document_sha256}`,
                  label: `${o.looks_like_po ? "OC · " : ""}${optionLabel(o)}`,
                })),
              ]}
            />
          </FormField>
        )
      ) : null}
      {mode === "file" ? (
        <FormField label="Archivo (PDF, JPG o PNG, hasta 20 MB)" htmlFor={`${idPrefix}-file`}>
          <input
            id={`${idPrefix}-file`}
            type="file"
            accept={ACCEPTED_DOCUMENT_TYPES}
            onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
            className="block w-full text-xs text-ink file:mr-2 file:rounded-md file:border file:border-line file:bg-canvas-raised file:px-2.5 file:py-1 file:text-xs"
          />
        </FormField>
      ) : null}
      {fileError ? <p role="alert" className="text-xs text-bad">{fileError}</p> : null}
      {mode !== "none" ? (
        <FormField label="Nº de OC" htmlFor={`${idPrefix}-number`} hint="Si lo dejas vacío, se toma del nombre del archivo cuando lo dice.">
          <TextInput id={`${idPrefix}-number`} value={number} onChange={onNumber} maxLength={64} />
        </FormField>
      ) : null}
    </fieldset>
  );
}

/* ── the block in the panel ───────────────────────────────────────────────── */

function DocumentRow({ card, doc, mayEdit, onChanged }: {
  card: OpportunityCardData;
  doc: CaseDocument;
  mayEdit: boolean;
  onChanged: () => void;
}) {
  const [opening, setOpening] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const quote = card.quotes.find((q) => q.revisions.some((r) => r.revision_id === doc.quote_revision_id));
  const revision = quote?.revisions.find((r) => r.revision_id === doc.quote_revision_id);

  async function open() {
    setOpening(true);
    setError(null);
    // The tab opens on the click itself (a later window.open would be blocked as a pop-up).
    const tab = window.open("about:blank", "_blank");
    try {
      const url = await fetchCaseDocumentUrl(card.opportunity_id, doc.document_id);
      if (tab) {
        tab.opener = null;
        tab.location.href = url;
      } else window.location.assign(url);
    } catch (err) {
      tab?.close();
      setError(documentRefusalText(err));
    } finally {
      setOpening(false);
    }
  }

  async function remove(e: FormEvent) {
    e.preventDefault();
    if (!reason.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await removeCaseDocument(doc.document_id, reason.trim());
      onChanged();
    } catch (err) {
      setError(documentRefusalText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="py-2" data-testid="case-document">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="rounded bg-good-bg px-1.5 py-0.5 text-[11px] font-semibold text-good">OC</span>
        <span className="text-[13px] font-medium text-ink">{ocLabel(doc)}</span>
        {quote && revision ? (
          <span className="text-[11px] text-ink-muted">cierra {quote.quote_number} r{revision.revision_no}</span>
        ) : null}
        <span className="ml-auto flex gap-1.5">
          <Button onClick={() => void open()} busy={opening} busyLabel="Abriendo…">
            Abrir
          </Button>
          {mayEdit ? (
            <Button onClick={() => setRemoving(!removing)} aria-expanded={removing}>
              Quitar
            </Button>
          ) : null}
        </span>
      </div>
      <p className="mt-0.5 text-[11px] text-ink-muted">
        {doc.original_filename} · adjuntada {fmtDateTime(doc.attached_at)}
        {doc.attached_by ? ` por ${doc.attached_by}` : ""}
        {doc.source === "email" ? " · desde un correo" : ""}
      </p>
      {removing ? (
        <form onSubmit={(e) => void remove(e)} className="mt-2 flex items-end gap-2" aria-label="Quitar OC">
          <div className="flex-1">
            <FormField label="Por qué se quita" htmlFor={`remove-${doc.document_id}`} required>
              <TextInput id={`remove-${doc.document_id}`} value={reason} onChange={setReason} maxLength={500} />
            </FormField>
          </div>
          <Button type="submit" disabled={!reason.trim() || busy} busy={busy} busyLabel="Quitando…">
            Quitar OC
          </Button>
        </form>
      ) : null}
      {error ? <p role="alert" className="mt-1 text-xs text-bad">{error}</p> : null}
    </li>
  );
}

function AttachForm({ card, onCancel, onDone }: { card: OpportunityCardData; onCancel: () => void; onDone: () => void }) {
  const sent = card.quotes.flatMap((q) =>
    q.revisions.filter((r) => r.status === "sent").map((r) => ({ ...r, quote_number: q.quote_number })),
  );
  // The quotation the case's amount comes from (its last sent one) is the likeliest to be closed.
  const last = card.money?.[0];
  const likely = sent.find((r) => r.quote_number === last?.quote_number && r.revision_no === last?.revision_no);
  const [revisionId, setRevisionId] = useState<string>(likely?.revision_id ?? sent[sent.length - 1]?.revision_id ?? "");
  const [choice, setChoice] = useState<OcChoice | null>(null);
  const [number, setNumber] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = useRef(newCaseCommandKey());

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!choice) return;
    setBusy(true);
    setError(null);
    try {
      await attachPurchaseOrder(
        choice,
        { opportunity_id: card.opportunity_id, quote_revision_id: revisionId || null, document_number: number, note: "OC adjuntada al caso" },
        key.current,
      );
      onDone();
    } catch (err) {
      key.current = newCaseCommandKey();
      setError(documentRefusalText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} className="mt-2 space-y-2 rounded-md border border-line p-3" aria-label="Adjuntar OC">
      <OcPicker opportunityId={card.opportunity_id} onChange={setChoice} number={number} onNumber={setNumber} allowNone={false} idPrefix="attach-oc" />
      {sent.length > 0 ? (
        <FormField label="Cotización que cierra" htmlFor="attach-oc-revision">
          <SelectInput
            id="attach-oc-revision"
            value={revisionId}
            onChange={setRevisionId}
            options={[
              { value: "", label: "Ninguna en particular" },
              ...sent.map((r) => ({ value: r.revision_id, label: `${r.quote_number} r${r.revision_no}${r.sent_at ? ` · ${fmtDate(r.sent_at)}` : ""}` })),
            ]}
          />
        </FormField>
      ) : null}
      {error ? <p role="alert" className="text-xs text-bad">{error}</p> : null}
      <div className="flex justify-end gap-2">
        <Button onClick={onCancel} disabled={busy}>
          Cancelar
        </Button>
        <Button type="submit" variant="primary" disabled={!choice || busy} busy={busy} busyLabel="Adjuntando…">
          Adjuntar OC
        </Button>
      </div>
    </form>
  );
}

export function CaseDocumentsBlock({ card, onChanged }: { card: OpportunityCardData; onChanged: () => void }) {
  const mayEdit = useMayAttachCaseDocuments();
  const [attaching, setAttaching] = useState(false);
  const docs = card.documents ?? [];
  const offer = mayEdit && (card.stage === "won" || card.stage === "negotiating");
  if (docs.length === 0 && !offer) return null;
  return (
    <section aria-label="Orden de compra" className="rounded-lg border border-line bg-canvas-raised px-4 py-3" data-testid="case-documents">
      <div className="flex items-center gap-2">
        <p className="text-[11px] font-medium text-ink-muted">{docs.length > 1 ? "Órdenes de compra" : "Orden de compra"}</p>
        {offer && !attaching ? (
          <span className="ml-auto">
            <Button onClick={() => setAttaching(true)}>{docs.length === 0 ? "Adjuntar OC" : "Adjuntar otra"}</Button>
          </span>
        ) : null}
      </div>
      {docs.length === 0 && !attaching ? (
        <p className="mt-1 text-xs text-ink-muted">{card.stage === "won" ? "Caso ganado sin OC adjunta." : "Sin OC adjunta."}</p>
      ) : null}
      {docs.length > 0 ? (
        <ul className="divide-y divide-line/70">
          {docs.map((d) => (
            <DocumentRow key={d.document_id} card={card} doc={d} mayEdit={mayEdit} onChanged={onChanged} />
          ))}
        </ul>
      ) : null}
      {attaching ? (
        <AttachForm
          card={card}
          onCancel={() => setAttaching(false)}
          onDone={() => {
            setAttaching(false);
            onChanged();
          }}
        />
      ) : null}
    </section>
  );
}
