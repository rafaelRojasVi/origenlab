import { useId, useRef, useState } from "react";
import { envelopeOfError } from "../../api/refusalEnvelope";
import { crmHash, type CrmSection } from "../crmRoute";
import { Button, FormField, Modal, TextInput } from "../ui";
import { newTenderCommandKey, openTenderCase, tenderRefusal, type OpenTenderCaseBody } from "./tenderApi";

/**
 * «Nueva licitación»: a Mercado Público code or link, and «Abrir licitación». The API reads the
 * tender itself; when it cannot (`buyer_required`) the dialog asks for the buying institution and an
 * optional RUT, and the same button sends both. A tender already open (`tender_case_exists`) offers
 * «Abrir» on that one. On success the new tender's page opens. Every refusal is said in Spanish; a
 * code this file does not know becomes one generic line, never the server's text.
 *
 * One `Idempotency-Key` per intended action: kept when the answer is uncertain (a gateway error, a
 * network failure) so the retry cannot open a second tender, dropped after a refusal for certain
 * (`definitive`) and whenever the operator changes what is asked.
 */
type Navigate = (section: CrmSection, id?: string | null) => void;

const GENERIC = "No se pudo abrir la licitación. Intenta de nuevo.";
const BUYER_PROMPT = "No pudimos leer la licitación en Mercado Público; escribe la institución compradora.";
const EXISTS = "Esta licitación ya está abierta";

const REFUSAL_ES: Record<string, string> = {
  tender_reference_invalid: "Eso no es un código de licitación ni un enlace de Mercado Público.",
  tender_code_not_in_link: "Ese enlace no trae el código; copia el código (ej. 1234567-12-LE26).",
  buyer_rut_invalid: "El RUT de la institución compradora no es válido; revisa el número y el dígito verificador.",
  organization_name_ambiguous: "Hay varias instituciones con ese nombre; pide a administración que revise cuál es.",
  supplier_exception_required:
    "Esta institución figura como proveedor de OrigenLab; pide a administración que abra esta licitación.",
  forbidden: "Solo ventas y administración pueden abrir licitaciones.",
  path_not_allowed: "Abrir licitaciones no está disponible por ahora.",
};

/** A 4xx with a code is final, so the next attempt may take a fresh key; anything else may have run. */
function definitive(err: unknown): boolean {
  const r = tenderRefusal(err);
  return !!r && r.status >= 400 && r.status < 500 && !r.code.startsWith("http_");
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function NewTenderDialog({ navigate, onClose }: { navigate: Navigate; onClose: () => void }) {
  const refId = useId();
  const nameId = useId();
  const rutId = useId();
  const [reference, setReference] = useState("");
  const [needsBuyer, setNeedsBuyer] = useState(false);
  const [buyerName, setBuyerName] = useState("");
  const [buyerRut, setBuyerRut] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [existing, setExisting] = useState<string | null>(null);
  const keyRef = useRef<string | null>(null);

  const edit = <T,>(set: (v: T) => void) => (v: T) => {
    keyRef.current = null; // a different request is a different action
    setError(null);
    setExisting(null);
    set(v);
  };

  const open = (id: string) => {
    onClose();
    navigate("licitaciones", id);
  };

  async function submit() {
    const code = reference.trim();
    if (!code || busy) return;
    const body: OpenTenderCaseBody = { reference: code };
    if (needsBuyer && buyerName.trim()) {
      body.buyer = { name: buyerName.trim(), ...(buyerRut.trim() ? { rut: buyerRut.trim() } : {}) };
    }
    keyRef.current ??= newTenderCommandKey();
    setBusy(true);
    setError(null);
    setExisting(null);
    try {
      const receipt = await openTenderCase(body, keyRef.current);
      const id = receipt.opportunity_id;
      if (typeof id === "string" && UUID.test(id)) {
        open(id);
        return;
      }
      setError(GENERIC);
    } catch (err) {
      const refusal = tenderRefusal(err);
      const code = refusal?.code ?? "";
      if (definitive(err)) keyRef.current = null;
      if (code === "buyer_required") {
        setNeedsBuyer(true);
        setError(BUYER_PROMPT);
      } else if (code === "tender_case_exists") {
        const found = envelopeOfError(err).details?.opportunity_id;
        if (typeof found === "string" && UUID.test(found)) setExisting(found);
        else setError(EXISTS);
      } else {
        setError(REFUSAL_ES[code] ?? GENERIC);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Nueva licitación" onClose={onClose} busy={busy}>
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <FormField label="Código o enlace de Mercado Público" htmlFor={refId} hint="Ej. 1234567-12-LE26">
          <TextInput id={refId} value={reference} onChange={edit(setReference)} disabled={busy} maxLength={2000} />
        </FormField>
        {needsBuyer ? (
          <>
            <FormField label="Institución compradora" htmlFor={nameId}>
              <TextInput id={nameId} value={buyerName} onChange={edit(setBuyerName)} disabled={busy} maxLength={200} />
            </FormField>
            <FormField label="RUT (opcional)" htmlFor={rutId}>
              <TextInput id={rutId} value={buyerRut} onChange={edit(setBuyerRut)} disabled={busy} maxLength={20} />
            </FormField>
          </>
        ) : null}
        {existing ? (
          <div role="status" className="flex flex-wrap items-center gap-2 rounded-lg border border-warn/30 bg-warn-bg px-3 py-2">
            <p className="min-w-0 flex-1 text-[13px] font-medium text-warn">{EXISTS}</p>
            <a
              href={crmHash("licitaciones", existing)}
              onClick={(e) => {
                e.preventDefault();
                open(existing);
              }}
              className="inline-flex min-h-[44px] items-center rounded-md border border-line bg-canvas-raised px-3 text-[13px] font-medium text-brand-700 hover:bg-canvas-sunken focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
            >
              Abrir
            </a>
          </div>
        ) : null}
        {error ? (
          <p role="alert" className="rounded-lg border border-bad/30 bg-bad-bg px-3 py-2 text-[13px] text-bad">
            {error}
          </p>
        ) : null}
        <div className="flex flex-wrap justify-end gap-2 pt-1">
          <Button onClick={onClose} disabled={busy}>Cancelar</Button>
          <Button type="submit" variant="primary" busy={busy} busyLabel="Abriendo…" disabled={!reference.trim() || (needsBuyer && !buyerName.trim())}>
            Abrir licitación
          </Button>
        </div>
      </form>
    </Modal>
  );
}
