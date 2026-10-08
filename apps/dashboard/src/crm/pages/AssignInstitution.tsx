import { useRef, useState, type FormEvent } from "react";
import { fetchV2Organizations } from "../../api/v2Client";
import { fetchOrganizationAuthoring } from "../authoring/crmAuthoringApi";
import {
  addCaseOrganization,
  setCaseOrganizationRole,
  caseRefusalText,
  isStaleRefusal,
  newCaseCommandKey,
} from "../caseCommands";
import type { OpportunityCardData } from "../crmTypes";
import { Button, FormField, TextInput, TextareaInput } from "../ui";

type Candidate = {
  id: string;
  name: string;
};

export function AssignInstitution({
  card,
  onCancel,
  onDone,
}: {
  card: OpportunityCardData;
  onCancel: () => void;
  onDone: (
    outcome: { tone: "good" | "bad"; lines: string[] },
    refetch: boolean,
  ) => void;
}) {
  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [selected, setSelected] = useState("");
  const [supplierReason, setSupplierReason] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keyRef = useRef(newCaseCommandKey());

  async function search(e: FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;

    setBusy(true);
    setError(null);
    setSelected("");
    setCandidates([]);

    try {
      const result = await fetchV2Organizations({
        q: query.trim(),
        limit: 50,
      });
      setCandidates(
        result.items.map((o) => ({
          id: o.organization_id,
          name: o.name,
        })),
      );
    } catch (err) {
      setError(caseRefusalText(err));
    } finally {
      setBusy(false);
    }
  }

  async function assign(e: FormEvent) {
    e.preventDefault();
    if (!selected || !note.trim() || busy || card.version == null) return;

    setBusy(true);
    setError(null);

    try {
      const details = await fetchOrganizationAuthoring(selected);
      const org = details.organization;

      if (org.status !== "active") {
        setError("La institución seleccionada no está activa.");
        return;
      }

      if (org.confirmation !== "confirmed") {
        setError(
          "Confirma primero la ficha de esta institución desde Organizaciones.",
        );
        return;
      }

      await addCaseOrganization(
        {
          opportunity_id: card.opportunity_id,
          opportunity_version: card.version,
          organization_id: org.id,
          organization_version: org.version,
          role: "requesting_institution",
          note: note.trim(),
          ...(supplierReason.trim()
            ? { supplier_exception_reason: supplierReason.trim() }
            : {}),
        },
        keyRef.current,
      );

      onDone(
        {
          tone: "good",
          lines: [`Institución solicitante «${org.name}» asociada al caso.`],
        },
        true,
      );
    } catch (err) {
      keyRef.current = newCaseCommandKey();
      setError(caseRefusalText(err));
      if (isStaleRefusal(err)) {
        onDone({ tone: "bad", lines: [caseRefusalText(err)] }, true);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      className="space-y-3 rounded-md border border-line p-3"
      aria-label="Asignar institución solicitante"
    >
      {(card.pending_institution_mentions?.length ?? 0) > 0 ? (
        <ReviewMentionedInstitution card={card} onDone={onDone} />
      ) : null}
      <form onSubmit={(e) => void search(e)} className="space-y-2">
        <FormField label="Buscar institución" htmlFor="assign-institution-search">
          <TextInput
            id="assign-institution-search"
            value={query}
            onChange={setQuery}
            placeholder="Nombre de la institución"
            maxLength={200}
            disabled={busy}
          />
        </FormField>
        <Button type="submit" disabled={busy || !query.trim()}>
          Buscar
        </Button>
      </form>

      {candidates.length > 0 ? (
        <form onSubmit={(e) => void assign(e)} className="space-y-3">
          <FormField label="Institución solicitante">
            <select
              aria-label="Institución solicitante"
              className="w-full rounded-md border border-line bg-surface px-3 py-2"
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
              disabled={busy}
            >
              <option value="">Seleccionar institución</option>
              {candidates.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
          </FormField>

          <FormField label="Motivo de la asignación" htmlFor="assign-institution-note">
            <TextareaInput
              id="assign-institution-note"
              value={note}
              onChange={setNote}
              rows={2}
              maxLength={2000}
            />
          </FormField>

          <p className="text-xs text-ink-muted">
            Confirma quién solicita la cotización. No selecciones una
            institución basándote únicamente en el dominio del correo.
          </p>

          <FormField label="Excepción de proveedor (solo si corresponde)" htmlFor="assign-supplier-reason">
            <TextareaInput
              id="assign-supplier-reason"
              value={supplierReason}
              onChange={setSupplierReason}
              rows={2}
              maxLength={2000}
            />
          </FormField>
          <Button
            type="submit"
            variant="primary"
            busy={busy}
            disabled={!selected || !note.trim() || busy}
          >
            Confirmar asignación
          </Button>
        </form>
      ) : null}

      {error ? <p role="alert">{error}</p> : null}

      <Button onClick={onCancel} disabled={busy}>
        Cancelar
      </Button>
    </section>
  );
}


/** The automation may only propose "mentioned". A human can make it a requester. */
function ReviewMentionedInstitution({
  card,
  onDone,
}: {
  card: OpportunityCardData;
  onDone: (
    outcome: { tone: "good" | "bad"; lines: string[] },
    refetch: boolean,
  ) => void;
}) {
  const mentions = card.pending_institution_mentions ?? [];
  const [mentionId, setMentionId] = useState("");
  const [note, setNote] = useState("");
  const [supplierReason, setSupplierReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keyRef = useRef(newCaseCommandKey());

  async function confirm(e: FormEvent) {
    e.preventDefault();
    const mention = mentions.find(
      (m) => m.opportunity_organization_id === mentionId,
    );
    if (!mention || !note.trim() || busy || card.version == null) return;
    setBusy(true);
    setError(null);
    try {
      await setCaseOrganizationRole(
        {
          opportunity_id: card.opportunity_id,
          opportunity_version: card.version,
          opportunity_organization_id: mention.opportunity_organization_id,
          role: "requesting_institution",
          note: note.trim(),
          ...(supplierReason.trim()
            ? { supplier_exception_reason: supplierReason.trim() }
            : {}),
        },
        keyRef.current,
      );
      onDone({
        tone: "good",
        lines: ["Institución «" + mention.name + "» confirmada como solicitante del caso."],
      }, true);
    } catch (err) {
      keyRef.current = newCaseCommandKey();
      setError(caseRefusalText(err));
      if (isStaleRefusal(err)) {
        onDone({ tone: "bad", lines: [caseRefusalText(err)] }, true);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="space-y-3 rounded-md border border-warn/40 p-3"
      aria-label="Revisar institución mencionada"
      onSubmit={(e) => void confirm(e)}
    >
      <p className="text-xs text-ink-muted">
        El caso contiene instituciones mencionadas por una regla automática.
        Aún no está confirmado quién solicita. Revisa la evidencia antes
        de cambiar el rol de una de ellas.
      </p>
      <FormField label="Institución mencionada" htmlFor="mention-requester-selection">
        <select
          id="mention-requester-selection"
          className="w-full rounded-md border border-line bg-surface px-3 py-2"
          value={mentionId}
          onChange={(e) => setMentionId(e.target.value)}
          disabled={busy}
        >
          <option value="">Seleccionar institución mencionada</option>
          {mentions.map((m) => (
            <option key={m.opportunity_organization_id} value={m.opportunity_organization_id}>
              {m.name} · {m.organization_id.slice(0, 8)}
            </option>
          ))}
        </select>
      </FormField>
      <FormField label="Evidencia de que es solicitante" htmlFor="mention-requester-note">
        <TextareaInput id="mention-requester-note" value={note}
          onChange={setNote} rows={2} maxLength={2000} />
      </FormField>
      <FormField label="Excepción de proveedor (solo si corresponde)" htmlFor="mention-supplier-reason">
        <TextareaInput id="mention-supplier-reason" value={supplierReason}
          onChange={setSupplierReason} rows={2} maxLength={2000} />
      </FormField>
      <Button type="submit" variant="primary" disabled={!mentionId || !note.trim() || busy}
        busy={busy}>
        Confirmar rol de solicitante
      </Button>
      {error ? <p role="alert">{error}</p> : null}
    </form>
  );
}
