/**
 * Hoy · «Solicitudes reenviadas» (spec 2026-10-14): requests Tatiana forwarded to contacto@ from
 * elsewhere — her Labdelivery mailbox, a supplier passing on a marketplace lead — read by the worker
 * for who really asked. One row per request (its «RV:» and «Fwd:» copies are one), with the
 * customer, what they ask for and where it came from. «Crear solicitud» opens a case at Solicitud
 * with its institution (the one the CRM knows for the customer's domain, or the company the
 * forward names, created «por confirmar»; empty is allowed) and goes to it; «Descartar» removes a
 * forward that is not a request. A forward that only came through the Labdelivery mailbox has no
 * email to open, by owner decision. Nothing renders when nothing is waiting.
 */
import { useCallback, useState } from "react";
import { fetchJsonGet, operatorApiUrl } from "../../api/operatorClient";
import {
  caseRefusalText,
  createRequestFromForward,
  dismissForwardedRequest,
  newCaseCommandKey,
  useMayRunCaseCommands,
} from "../caseCommands";
import type { CrmSection } from "../crmRoute";
import { inSharedMailbox } from "../gmailLinks";
import { Badge, Button, FormField, Panel, TextInput, fmtDate, toast } from "../ui";
import { useResource } from "../useResource";

export interface ForwardedRequest {
  id: string;
  ids: string[];
  received_at: string | null;
  customer: { name: string | null; email: string | null; company: string | null; rut: string | null; phone: string | null };
  requested_item: string | null;
  relay: string | null;
  origins: string[];
  gmail_url: string | null;
  institution: { id: string; name: string; version: number } | null;
}

const fetchForwarded = () =>
  fetchJsonGet<{ items: ForwardedRequest[] }>(operatorApiUrl("/v2/workspace/forwarded-requests"));

type Navigate = (s: CrmSection, id?: string | null) => void;

export function ForwardedRequestsPanel({ navigate, onChanged }: { navigate: Navigate; onChanged: () => void }) {
  const load = useCallback(fetchForwarded, []);
  const [state, reload] = useResource(load, [load]);
  const [gone, setGone] = useState<Set<string>>(new Set());
  const mayDecide = useMayRunCaseCommands();
  if (state.kind !== "ready" || !Array.isArray(state.data?.items)) return null;
  const items = state.data.items.filter((i) => !gone.has(i.id));
  if (items.length === 0) return null;
  const leave = (id: string) => setGone((g) => new Set(g).add(id));
  return (
    <Panel
      title="Solicitudes reenviadas"
      aside={<Badge tone="warn" glyph={false}>{items.length}</Badge>}
      bodyClassName="divide-y divide-line"
    >
      <p className="px-4 pt-2 text-[11px] text-ink-muted">
        Pedidos que llegaron a otro correo y se reenviaron a contacto@. Crea la solicitud con el cliente real, o
        descártalo si no es un pedido.
      </p>
      {items.map((it) => (
        <ForwardedRow key={it.id} item={it} mayDecide={mayDecide} navigate={navigate}
          onDone={(id) => { leave(id); onChanged(); reload(); }} />
      ))}
    </Panel>
  );
}

function ForwardedRow({ item, mayDecide, navigate, onDone }: {
  item: ForwardedRequest;
  mayDecide: boolean;
  navigate: Navigate;
  onDone: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(item.institution ? "" : (item.customer.company ?? ""));
  const [busy, setBusy] = useState<null | "create" | "dismiss">(null);
  const [createKey] = useState(newCaseCommandKey);
  const [dismissKey] = useState(newCaseCommandKey);
  const c = item.customer;
  const who = c.name ?? c.email ?? "Cliente sin nombre";
  const via = item.relay === "marketplace" ? "marketplace" : item.origins.includes("labdelivery") ? "otro correo" : "reenvío";

  async function create() {
    setBusy("create");
    try {
      const receipt = await createRequestFromForward({
        forwarded_request_id: item.id,
        note: "Solicitud reenviada a contacto@",
        ...(item.institution
          ? { organization_id: item.institution.id, organization_version: item.institution.version }
          : name.trim() ? { new_organization_name: name.trim() } : {}),
      }, createKey);
      toast("Solicitud creada.");
      onDone(item.id);
      navigate("oportunidades", String(receipt.opportunity_id));
    } catch (err) {
      toast(caseRefusalText(err), "bad");
    } finally {
      setBusy(null);
    }
  }

  async function dismiss() {
    setBusy("dismiss");
    try {
      await dismissForwardedRequest({ forwarded_request_id: item.id, note: "No es una solicitud" }, dismissKey);
      toast("Descartado.");
      onDone(item.id);
    } catch (err) {
      toast(caseRefusalText(err), "bad");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div data-testid="forwarded-row" className="space-y-2 px-4 py-2.5">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-1.5">
        <div className="min-w-[12rem] flex-1">
          <p className="text-[13px] font-medium text-ink">
            {who}
            {c.company ? <span className="font-normal text-ink-muted"> · {c.company}</span> : null}
          </p>
          <p className="mt-0.5 text-[11px] text-ink-muted">
            {item.requested_item ? <>Pide: <span className="text-ink">{item.requested_item}</span> · </> : null}
            {item.received_at ? `${fmtDate(item.received_at)} · ` : ""}vía {via}
            {c.email && c.name ? ` · ${c.email}` : ""}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {item.gmail_url ? (
            <a href={inSharedMailbox(item.gmail_url) ?? item.gmail_url} target="_blank" rel="noopener noreferrer"
               className="inline-flex h-8 items-center rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:border-line-strong">
              Abrir correo ↗
            </a>
          ) : null}
          {mayDecide ? (
            <>
              <Button variant="secondary" onClick={() => void dismiss()} busy={busy === "dismiss"} busyLabel="Descartando…"
                      disabled={busy !== null}>
                Descartar
              </Button>
              <Button variant={open ? "quiet" : "primary"} aria-expanded={open} onClick={() => setOpen(!open)}
                      disabled={busy !== null}>
                {open ? "Cerrar" : "Crear solicitud"}
              </Button>
            </>
          ) : null}
        </div>
      </div>
      {open && mayDecide ? (
        <div className="space-y-2 rounded-md border border-line bg-canvas-sunken/50 p-3">
          {item.institution ? (
            <p className="text-xs text-ink">
              Institución: <span className="font-medium">{item.institution.name}</span>{" "}
              <span className="text-ink-faint">(ya está en el CRM, por el dominio del correo)</span>
            </p>
          ) : (
            <FormField label="Institución" htmlFor={`fw-${item.id}`}
                       hint="Se crea «por confirmar» si no existe. Déjalo vacío si es una persona sin empresa.">
              <TextInput id={`fw-${item.id}`} value={name} placeholder="Nombre de la institución" onChange={setName} />
            </FormField>
          )}
          <p className="text-[11px] text-ink-muted">
            Se guarda en una nota: {[c.name, c.email, c.phone, c.rut ? `RUT ${c.rut}` : null].filter(Boolean).join(" · ") || "—"}
          </p>
          <Button variant="primary" onClick={() => void create()} busy={busy === "create"} busyLabel="Creando…"
                  disabled={busy !== null}>
            Crear
          </Button>
        </div>
      ) : null}
    </div>
  );
}
