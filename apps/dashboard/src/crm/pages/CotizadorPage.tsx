import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { OperatorApiError } from "../../api/operatorClient";
import { Badge, Button, ConfirmDialog, Skeleton, toast } from "../ui";
import { crmHash, type CrmSection } from "../crmRoute";
import { ClientSection } from "../quotes/ClientSection";
import { isEditable, isGone } from "../quotes/draftEditor";
import { ExpensesSection } from "../quotes/ExpensesSection";
import { IssuedView, RevisionSummary, RevisionSwitcher, statusOf } from "../quotes/IssuedView";
import { isPrintable, printableQuote } from "../quotes/printableQuote";
import { ProductsSection } from "../quotes/ProductsSection";
import { QuotePrintView } from "../quotes/QuotePrintView";
import {
  QuoteRefusal,
  createQuoteRevision,
  fetchQuoteWorkspace,
  newQuoteCommandKey,
  voidQuoteRevision,
  type QuoteWorkspace,
} from "../quotes/quoteApi";
import { quoteLabel } from "../quotes/quoteLabel";
import { quoteRefusalLines, quoteRefusalText } from "../quotes/quoteRefusal";
import { SaveStatus } from "../quotes/SaveStatus";
import { SummaryRail } from "../quotes/SummaryRail";
import { takeDiscarded } from "../quotes/tabEditors";
import { tabMetaOf, type TabMeta } from "../quotes/tabStore";
import { TermsSection, focusTerm, missingTerms } from "../quotes/TermsSection";
import { useQuoteDraft } from "../quotes/useQuoteDraft";
import { useResource } from "../useResource";

/**
 * «Cotizador»: the workspace of one quote — one tab of the Cotizador module
 * (`CotizadorModulePage`, `#/crm/cotizador/<quoteId>`), which keeps several mounted at once and
 * shows one. `onMeta` tells the module's tab the quote's label, client and state each time the
 * workspace loads or changes. Only sales and administration may quote (the API answers 403
 * `role_may_not_view_costs` to anyone else), and the page says so instead of showing a generic error.
 * Every number on the page comes from the API; the browser never prices.
 *
 * An authored draft is edited in place (`useQuoteDraft`: live preview, autosave); the header says
 * whether it is saved. The right rail (`SummaryRail`) shows the totals, margin, exchange rates,
 * warnings and the principal model's past prices. «Vista cliente» (B7), in the header for the draft
 * and an issued revision alike, hides costs and margins, the whole «Gastos de importación» card,
 * the rail's internal cards and approval's reasons, for screen sharing. Refusals are said in
 * Spanish (`quoteRefusalText`).
 *
 * «Vista previa PDF» (`QuotePrintView`) opens the client's document of the current revision — a
 * draft (marked «BORRADOR», from the live editor) or an approved or sent one (from what approval
 * froze) — for the browser to print or save as a PDF; it carries no cost data (`printableQuote`).
 *
 * Commands (Task 7): «Aprobar» saves what is pending first and approves with the version that
 * save returned (Review Focus 3); the API's reasons for refusing are listed under the header.
 * An issued revision is read-only (`IssuedView`); «Nueva revisión» opens the next draft and
 * «Anular» voids a draft or an approved revision with a reason. With more than one revision a
 * list lets the operator look at an older one (its summary: the workspace details the current one
 * only). Voiding any revision saves the draft's pending edits first, and asks before voiding over
 * edits that cannot be saved. The way back to the case saves pending edits first, and asks before
 * leaving edits that cannot be saved (an incomplete draft, a failed save, a conflict). While a
 * command runs, the editor's inputs are off. A draft approved or voided in another tab freezes the
 * editor like a conflict.
 */
type Navigate = (section: CrmSection, id?: string | null) => void;

function BackToCases({ navigate }: { navigate: Navigate }) {
  return (
    <a
      href={crmHash("oportunidades")}
      onClick={(e) => {
        e.preventDefault();
        navigate("oportunidades");
      }}
      className="mt-3 inline-flex min-h-[44px] items-center rounded-md border border-line bg-canvas-raised px-3 text-[13px] font-medium text-accent hover:bg-canvas-sunken focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
    >
      Volver a Oportunidades
    </a>
  );
}

function NotFound({ navigate }: { navigate: Navigate }) {
  return (
    <div role="status" className="rounded-lg border border-line bg-canvas-sunken/70 px-4 py-4">
      <p className="text-[13px] font-semibold text-ink">Esta cotización no existe.</p>
      <p className="mt-1 text-xs text-ink-muted">Puede que el enlace esté incompleto o que la cotización ya no esté disponible.</p>
      <BackToCases navigate={navigate} />
    </div>
  );
}

/** The page's shape while it loads: the header, then the two columns the editor will fill. */
function CotizadorSkeleton() {
  return (
    <div className="space-y-4" data-testid="cotizador-skeleton">
      <div className="space-y-2">
        <div className="crm-skeleton h-4 w-64 rounded-md" />
        <div className="crm-skeleton h-8 w-72 rounded-md" />
      </div>
      <Skeleton rows={4} />
    </div>
  );
}

/** What anyone but sales and administration sees instead of a quote. */
export function NoQuoteAccess() {
  return (
    <div role="alert" className="rounded-lg border border-warn/30 bg-warn-bg px-4 py-4">
      <p className="text-[13px] font-semibold text-warn">Sólo ventas y administración pueden cotizar.</p>
      <p className="mt-1 text-xs text-ink-muted">Si necesitas cotizar, pide a administración que cambie tu rol.</p>
    </div>
  );
}

export function CotizadorPage({
  quoteId,
  navigate,
  onMeta,
}: {
  quoteId: string | null;
  navigate: Navigate;
  onMeta?: (meta: TabMeta) => void;
}) {
  const [state, reload] = useResource(
    () => (quoteId ? fetchQuoteWorkspace(quoteId) : Promise.reject(new Error("quote_id_missing"))),
    [quoteId],
  );
  if (!quoteId) return <NotFound navigate={navigate} />;
  if (state.kind === "loading") return <CotizadorSkeleton />;
  if (state.kind === "permission") return <NoQuoteAccess />;
  if (state.kind === "unavailable") return <NotFound navigate={navigate} />;
  if (state.kind === "error") {
    return (
      <div role="alert" className="rounded-lg border border-bad/30 bg-bad-bg px-4 py-4">
        <p className="text-[13px] font-semibold text-bad">No se pudo abrir la cotización</p>
        <p className="mt-1 break-words text-xs text-ink-muted">{state.message}</p>
        <button
          type="button"
          onClick={reload}
          className="mt-2 min-h-[44px] rounded-md bg-ink px-4 text-xs font-medium text-on-solid hover:bg-ink-strong focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
        >
          Reintentar
        </button>
      </div>
    );
  }
  return <CotizadorWorkspace workspace={state.data} navigate={navigate} onMeta={onMeta} />;
}

/** A save refused because of whom the quote goes to: said under «Para». */
function isRecipientRefusal(code: string | null): boolean {
  return !!code && (code.startsWith("recipient_") || code === "labdelivery_refused");
}

/**
 * What to say about a refusal, as a list, in Spanish (`quoteRefusalLines`: approval joins several
 * reasons with « · »). A body that is not the API's envelope (a proxy page, a network failure) is
 * not shown; `fallback` is said instead.
 */
function refusalLines(e: unknown, fallback: string): string[] {
  if (e instanceof QuoteRefusal) return quoteRefusalLines(e, fallback);
  // The editor's own Spanish reasons (an incomplete draft, a save that did not go through).
  if (e instanceof Error && e.constructor === Error && !(e instanceof OperatorApiError)) return [e.message];
  return [fallback];
}

/** One `Idempotency-Key` per user action: the same action retried (same signature) reuses it. */
function useActionKey() {
  const ref = useRef<{ sig: string; key: string } | null>(null);
  return {
    for(sig: string): string {
      if (ref.current?.sig !== sig) ref.current = { sig, key: newQuoteCommandKey() };
      return ref.current.key;
    },
    done() {
      ref.current = null;
    },
  };
}

type Problems = { title: string; items: string[] };
type VoidTarget = { id: string; label: string; status: string; version: number; currentDraft: boolean };

const OPEN = new Set(["draft", "in_review"]);
const ISSUED = new Set(["approved", "sent"]);

/** A draft or an approved revision, of this app and not replaced yet, can be voided (a sent one is revised). */
function voidable(r: { origin: string; status: string; superseded_by_revision_no: number | null } | null | undefined): boolean {
  return !!r && r.origin === "authored" && (r.status === "draft" || r.status === "approved") && r.superseded_by_revision_no == null;
}

function CotizadorWorkspace({
  workspace,
  navigate,
  onMeta,
}: {
  workspace: QuoteWorkspace;
  navigate: Navigate;
  onMeta?: (meta: TabMeta) => void;
}) {
  const editor = useQuoteDraft(workspace);
  // «Vista cliente» was removed from the header; the editor always shows the internal view.
  const clientView = false;
  const [viewingId, setViewingId] = useState<string | null>(null);
  const [problems, setProblems] = useState<Problems | null>(null);
  const [approving, setApproving] = useState(false);
  const [revising, setRevising] = useState(false);
  const [voidTarget, setVoidTarget] = useState<VoidTarget | null>(null);
  const [voiding, setVoiding] = useState(false);
  const [voidError, setVoidError] = useState<string | null>(null);
  const [leaving, setLeaving] = useState(false);
  const [printing, setPrinting] = useState(false);
  const [askTerms, setAskTerms] = useState<{ key: string; label: string }[] | null>(null);
  // «Completar condiciones»: the first empty field is focused once the dialog has gone.
  const [focusAfter, setFocusAfter] = useState<string | null>(null);
  useEffect(() => {
    if (focusAfter && !askTerms) {
      focusTerm(focusAfter);
      setFocusAfter(null);
    }
  }, [focusAfter, askTerms]);
  const reviseKey = useActionKey();
  const voidKey = useActionKey();
  const { state } = editor;
  const ws = state.workspace;
  const cur = ws.current;
  const authored = isEditable(ws);
  // M1: nothing is edited while a command runs on this quote.
  const commandRunning = approving || revising || voiding;
  const disabled = !editor.editable || commandRunning;
  const refusal = state.error instanceof QuoteRefusal ? state.error : null;
  const recipientRefusal = refusal && isRecipientRefusal(refusal.code) ? quoteRefusalText(refusal) : null;
  // In Spanish; a bare body (a proxy error page) has no code and is not shown.
  const saveMessage = recipientRefusal ? "revisa «Para»" : refusal?.code ? quoteRefusalText(refusal) : null;
  const frozen = state.saveState === "conflict";
  const gone = frozen && isGone(state.error);
  const viewed = viewingId && viewingId !== cur?.id ? ws.revisions.find((r) => r.id === viewingId) ?? null : null;
  const labelOf = (revisionNo: number, label?: string | null) =>
    quoteLabel({ label, quote_number: ws.quote.quote_number, revision_no: revisionNo });

  // The module's tab shows this quote as it is now (a restored tab's stored label may be old).
  const meta = tabMetaOf(ws);

  // Leaving another way (the menu, a link elsewhere, closing its tab) unmounts the page: the editor
  // then sends what it can, but an incomplete draft or one in conflict cannot be sent — say so on
  // the next page, naming the quote (several can be open). Not after «Salir igual» / «Cerrar igual».
  const lossRef = useRef<string | null>(null);
  const confirmedRef = useRef(false);
  const lost = `Tus últimos cambios en la cotización ${meta.label} no se guardaron`;
  lossRef.current = !editor.pending
    ? null
    : state.saveState === "conflict"
      ? isGone(state.error)
        ? `${lost}: se aprobó o anuló en otra pestaña.`
        : `${lost}: otra persona la había cambiado.`
      : editor.invalid.length > 0
        ? `${lost}: el borrador estaba incompleto.`
        : null;
  const quoteIdRef = useRef(ws.quote.id);
  quoteIdRef.current = ws.quote.id;
  useEffect(
    () => () => {
      const closedAnyway = takeDiscarded(quoteIdRef.current);
      if (lossRef.current && !confirmedRef.current && !closedAnyway) toast(lossRef.current, "warn");
    },
    [],
  );

  const onMetaRef = useRef(onMeta);
  onMetaRef.current = onMeta;
  useEffect(() => {
    onMetaRef.current?.({ label: meta.label, client: meta.client, status: meta.status });
  }, [meta.label, meta.client, meta.status]);

  // A reason listed for an earlier attempt is not left standing once the draft changes.
  useEffect(() => {
    if (state.editSeq > 0) setProblems(null);
  }, [state.editSeq]);

  const revisions = ws.revisions.length ? ws.revisions : cur ? [cur] : [];
  const canRevise = !revisions.some((r) => OPEN.has(r.status)) && revisions.some((r) => r.origin === "authored" && ISSUED.has(r.status));

  const approve = async () => {
    if (editor.invalid.length) {
      setProblems({ title: "No se pudo emitir", items: [...new Set(editor.invalid.map((p) => p.message_es))] });
      return;
    }
    setProblems(null);
    setApproving(true);
    try {
      const res = await editor.approve();
      setViewingId(null);
      toast(`Cotización emitida · ${quoteLabel({ label: res.revision_label, quote_number: res.quote_number })}`);
      // «Emitir cotización»: approved, so the PDF opens at once, ready to save and send.
      setPrinting(true);
    } catch (e) {
      setProblems({ title: "No se pudo emitir", items: refusalLines(e, "No se pudo emitir. Intenta de nuevo.") });
    } finally {
      setApproving(false);
    }
  };

  const revise = async () => {
    setProblems(null);
    setRevising(true);
    const newest = Math.max(0, ...revisions.filter((r) => ISSUED.has(r.status)).map((r) => r.revision_no));
    try {
      const res = await createQuoteRevision(ws.quote.id, reviseKey.for(`${ws.quote.id}@${newest}`));
      reviseKey.done();
      setViewingId(null);
      await editor.reload().catch(() => toast("Se abrió la nueva revisión; recarga la página para verla.", "warn"));
      toast(`Nueva revisión ${labelOf(res.revision_no, res.revision_label)} abierta`);
    } catch (e) {
      setProblems({ title: "No se pudo abrir una nueva revisión", items: refusalLines(e, "No se pudo abrir una nueva revisión. Intenta de nuevo.") });
    } finally {
      setRevising(false);
    }
  };

  const confirmVoid = async (reason: string) => {
    if (!voidTarget) return;
    const target = voidTarget;
    setVoiding(true);
    setVoidError(null);
    try {
      // The draft's pending save always goes first: voiding the draft needs its latest version, and
      // voiding an older revision reloads the page, which would drop edits not saved yet (I4).
      const flushed = await editor.flush().catch(() => ({ ok: false, version: state.version }));
      if (!target.currentDraft && !flushed.ok && editor.pending && !window.confirm("Hay cambios sin guardar en el borrador. ¿Anular igual?")) {
        setVoidTarget(null);
        return;
      }
      const version = target.currentDraft ? flushed.version : target.version;
      const why = reason.trim();
      await voidQuoteRevision(target.id, version, why, voidKey.for(`${target.id}@${version}@${why}`));
      voidKey.done();
      setVoidTarget(null);
      setViewingId(null);
      toast(`Cotización anulada · ${target.label}`);
      await editor.reload().catch(() => toast("Se anuló; recarga la página para verla.", "warn"));
    } catch (e) {
      setVoidError(refusalLines(e, "No se pudo anular. Intenta de nuevo.").join(" · "));
    } finally {
      setVoiding(false);
    }
  };

  const askVoid = (target: VoidTarget) => {
    setVoidError(null);
    setVoidTarget(target);
  };

  // Review Focus 5 (in the app): the way back saves first; edits that cannot be saved are asked about.
  const leaveTo = async (go: () => void) => {
    if (!editor.pending) return go();
    setLeaving(true);
    const flushed = await editor.flush().catch(() => ({ ok: false, version: state.version }));
    setLeaving(false);
    if (flushed.ok) return go();
    if (window.confirm("Hay cambios sin guardar. ¿Salir igual?")) {
      confirmedRef.current = true;
      go();
    }
  };

  const currentLabel = cur ? labelOf(cur.revision_no, cur.label) : labelOf(1);
  // «Vista previa PDF»: the client's document of a draft (marked «BORRADOR»), an approved or a sent revision.
  // It prints the current revision, so it is not offered while an older one is looked at.
  const pdfButton = isPrintable(ws) && !viewed ? (
    <Button className="min-h-[44px] px-4 text-[14px]" disabled={commandRunning} onClick={() => setPrinting(true)}>
      Vista previa PDF
    </Button>
  ) : null;
  const printable = printing ? printableQuote(ws, authored ? { state, stale: editor.stale } : null) : null;
  // I3: «Vista cliente» is the page's, for the draft and an issued revision alike.
  // «Vista cliente» left the header (owner, 2026-10-17): «Vista previa PDF» is the client's view.
  const actions = cur ? (
    authored ? (
      <>
        {voidable(cur) ? (
          <Button
            variant="danger"
            className="min-h-[44px] px-4 text-[14px]"
            disabled={commandRunning}
            onClick={() => askVoid({ id: cur.id, label: currentLabel, status: cur.status, version: state.version, currentDraft: true })}
          >
            Anular
          </Button>
        ) : null}
        {pdfButton}
        <Button
          variant="primary"
          className="min-h-[44px] px-[18px] text-[14px]"
          busy={approving}
          busyLabel="Emitiendo…"
          disabled={!editor.editable || revising || voiding}
          onClick={() => {
            // Owner 2026-10-26: empty conditions are asked about first; issuing without them is a choice.
            const missing = missingTerms(state.draft);
            if (missing.length && !editor.invalid.length) setAskTerms(missing);
            else void approve();
          }}
        >
          Emitir cotización
        </Button>
      </>
    ) : (
      <>
        {voidable(cur) ? (
          <Button
            variant="danger"
            className="min-h-[44px] px-4 text-[14px]"
            disabled={commandRunning}
            onClick={() => askVoid({ id: cur.id, label: currentLabel, status: cur.status, version: cur.version, currentDraft: false })}
          >
            Anular
          </Button>
        ) : null}
        {pdfButton}
        {canRevise ? (
          <Button
            variant="primary"
            className="min-h-[44px] px-[18px] text-[14px]"
            busy={revising}
            busyLabel="Abriendo…"
            disabled={approving || voiding}
            onClick={() => void revise()}
          >
            Nueva revisión
          </Button>
        ) : null}
      </>
    )
  ) : null;

  return (
    <div className="space-y-[18px]">
      <CotizadorHeader
        workspace={ws}
        leaving={leaving}
        onBack={() => void leaveTo(() => navigate("oportunidades", ws.quote.opportunity_id))}
        actions={actions}
        status={
          authored ? (
            <SaveStatus
              saveState={state.saveState}
              lastSavedAt={state.lastSavedAt}
              incomplete={editor.invalid.length > 0}
              message={saveMessage}
              onRetry={() => void editor.retry()}
            />
          ) : null
        }
      />
      {/* Approval's reasons speak of costs and prices: not in «Vista cliente». */}
      {problems && !frozen && !clientView ? <ProblemList problems={problems} /> : null}
      {frozen ? <ConflictBanner gone={gone} onReload={editor.reload} /> : null}
      <RevisionSwitcher workspace={ws} selectedId={viewed ? viewed.id : cur?.id ?? null} onSelect={(id) => setViewingId(id === cur?.id ? null : id)} />
      {viewed ? (
        <RevisionSummary
          workspace={ws}
          revision={viewed}
          actions={
            voidable(viewed) ? (
              <Button
                variant="danger"
                className="min-h-[44px] px-4 text-[14px]"
                disabled={commandRunning}
                aria-label={`Anular ${labelOf(viewed.revision_no, viewed.label)}`}
                onClick={() =>
                  askVoid({ id: viewed.id, label: labelOf(viewed.revision_no, viewed.label), status: viewed.status, version: viewed.version, currentDraft: false })
                }
              >
                Anular
              </Button>
            ) : null
          }
        />
      ) : cur ? (
        <div className="flex flex-wrap items-start gap-[18px]">
          {authored ? (
            <div className="flex min-w-0 flex-[999_1_760px] flex-col gap-[18px]">
              <ClientSection
                workspace={ws}
                draft={state.draft}
                invalid={editor.invalid}
                disabled={disabled}
                refusal={recipientRefusal}
                edit={editor.edit}
                onInstitutionAdded={async () => {
                  await editor.flush().catch(() => undefined);
                  await editor.reload().catch(() => toast("Se agregó la institución; recarga la página para verla.", "warn"));
                }}
              />
              <ProductsSection
                workspace={ws}
                draft={state.draft}
                computed={state.computed}
                computedKeys={state.computedKeys}
                stale={editor.stale}
                invalid={editor.invalid}
                disabled={disabled}
                clientView={clientView}
                edit={editor.edit}
              />
              {/* Internal: import expenses are never shown to the client, not even in screen sharing. */}
              {clientView ? null : (
                <ExpensesSection
                  draft={state.draft}
                  computed={state.computed}
                  computedKeys={state.computedKeys}
                  stale={editor.stale}
                  invalid={editor.invalid}
                  disabled={disabled}
                  edit={editor.edit}
                />
              )}
              <TermsSection draft={state.draft} invalid={editor.invalid} disabled={disabled} edit={editor.edit} />
            </div>
          ) : (
            <IssuedView workspace={ws} />
          )}
          {/* Beside the editor on a wide screen, under it on a narrow one; an issued revision shows its stored totals. */}
          <SummaryRail
            state={state}
            stale={editor.stale}
            disabled={disabled}
            clientView={clientView}
            edit={editor.edit}
            onSaveNow={editor.saveNow}
          />
        </div>
      ) : null}
      {printable ? <QuotePrintView quote={printable} onClose={() => setPrinting(false)} /> : null}
      {askTerms ? (
        <MissingTermsDialog
          missing={askTerms}
          onComplete={() => {
            setFocusAfter(askTerms[0].key);
            setAskTerms(null);
          }}
          onIssue={() => {
            setAskTerms(null);
            void approve();
          }}
        />
      ) : null}
      {voidTarget ? (
        <ConfirmDialog
          title={`¿Anular la cotización ${voidTarget.label}?`}
          lines={
            voidTarget.status === "draft"
              ? ["El borrador se descarta: no se podrá aprobar ni enviar.", "Queda en el historial de la cotización, con el motivo."]
              : ["Esta revisión aprobada deja de valer y no se podrá enviar.", "Para corregir una cotización, usa «Nueva revisión»."]
          }
          requireReason
          reasonLabel="Motivo"
          confirmLabel="Anular"
          busy={voiding}
          error={voidError}
          onConfirm={(reason) => void confirmVoid(reason)}
          onCancel={() => setVoidTarget(null)}
        />
      ) : null}
    </div>
  );
}

/** «Emitir cotización» with conditions left empty: complete them first (the default) or issue without them. */
function MissingTermsDialog({
  missing,
  onComplete,
  onIssue,
}: {
  missing: { key: string; label: string }[];
  onComplete: () => void;
  onIssue: () => void;
}) {
  const titleId = useId();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onComplete();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onComplete]);
  return (
    <div role="alertdialog" aria-modal="true" aria-labelledby={titleId} className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-scrim" onClick={onComplete} aria-hidden="true" />
      <div className="relative w-full max-w-md min-w-0 rounded-xl border border-line bg-canvas-raised p-5 shadow-xl">
        <h2 id={titleId} className="text-base font-semibold text-ink">
          {missing.length === 1 ? "Falta una condición" : `Faltan ${missing.length} condiciones`}
        </h2>
        <p className="mt-2 text-[13px] text-ink-muted">Están vacías y no saldrán en la cotización. ¿Quieres completarlas antes de emitir?</p>
        <ul className="mt-2 list-disc space-y-0.5 pl-5 text-[13px] text-ink">
          {missing.map((m) => (
            <li key={m.key}>{m.label}</li>
          ))}
        </ul>
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <Button onClick={onIssue}>Emitir igual</Button>
          <Button variant="primary" autoFocus onClick={onComplete}>
            Completar condiciones
          </Button>
        </div>
      </div>
    </div>
  );
}

/** The API's reasons for refusing a command, one per line, under the header. */
function ProblemList({ problems }: { problems: Problems }) {
  const titleId = useId();
  return (
    <div role="alert" aria-labelledby={titleId} className="rounded-lg border border-bad/30 bg-bad-bg px-4 py-3">
      <p id={titleId} className="text-[13px] font-semibold text-bad">
        {problems.title}
      </p>
      <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-[13px] text-ink">
        {problems.items.map((t, i) => (
          <li key={i}>{t}</li>
        ))}
      </ul>
    </div>
  );
}

/**
 * B3: someone else saved this draft; nothing more is sent until the operator loads their version.
 * I6 (`gone`): the draft was approved or voided in another tab, so no save can succeed — the same
 * freeze, and the way out is the quote's current state.
 */
function ConflictBanner({ gone, onReload }: { gone: boolean; onReload: () => Promise<unknown> }) {
  const [busy, setBusy] = useState(false);
  return (
    <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-warn/30 bg-warn-bg px-4 py-3">
      <div>
        <p className="text-[13px] font-semibold text-warn">
          {gone ? "Esta cotización cambió en otra pestaña (se aprobó o anuló)." : "Otra persona cambió este borrador."}
        </p>
        <p className="mt-0.5 text-xs text-ink-muted">
          {gone ? "Tus últimos cambios no se guardaron: el borrador ya no se puede editar." : "Tus últimos cambios no se guardaron. Abre la versión guardada para seguir."}
        </p>
      </div>
      <Button
        variant="primary"
        busy={busy}
        busyLabel="Abriendo…"
        className="min-h-[44px] px-4"
        onClick={() => {
          setBusy(true);
          onReload()
            .catch((e: unknown) => toast(quoteRefusalText(e, "No se pudo abrir la versión guardada."), "bad"))
            .finally(() => setBusy(false));
        }}
      >
        {gone ? "Ver estado actual" : "Ver cambios"}
      </Button>
    </div>
  );
}

function CotizadorHeader({
  workspace,
  status,
  actions,
  leaving,
  onBack,
}: {
  workspace: QuoteWorkspace;
  status: ReactNode;
  actions: ReactNode;
  /** The way back is saving pending edits first. */
  leaving: boolean;
  onBack: () => void;
}) {
  const { quote, case: opportunity, current, revisions } = workspace;
  const badge = statusOf(current?.status ?? revisions[revisions.length - 1]?.status ?? "draft");
  const label = quoteLabel({ label: current?.label, quote_number: quote.quote_number, revision_no: current?.revision_no });
  const caseTitle = opportunity.title || opportunity.institution?.name || "la oportunidad";
  const caseHref = crmHash("oportunidades", quote.opportunity_id);
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <a
          href={caseHref}
          aria-busy={leaving || undefined}
          onClick={(e) => {
            e.preventDefault();
            if (!leaving) onBack();
          }}
          className="inline-block max-w-full truncate text-[13px] font-medium text-accent hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
        >
          ← {leaving ? "Guardando antes de salir…" : caseTitle}
        </a>
        <div className="mt-1.5 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-ink">
            Cotización <span className="font-mono font-medium tabular-nums">{label}</span>
          </h1>
          <Badge tone={badge.tone}>{badge.label}</Badge>
          {status}
        </div>
      </div>
      <div className="flex flex-wrap gap-2" data-testid="cotizador-actions">
        {actions}
      </div>
    </header>
  );
}
