/**
 * «Correos del caso» as Gmail cadenas (owner request 2026-10-11): a case often lives in several
 * threads (the request, a forward, the quote sent in a new email, the client's answer), and
 * showing only the newest email each way hid which cadena an email belonged to.
 *
 * One block per cadena, newest activity first: subject once, who it is with (cliente / proveedor
 * / interno), how many emails and the last date, «Abrir en Gmail»; then one line per email,
 * newest first, the last three shown and «Ver todo» for the rest. Senders come by name or domain
 * only. Read-only (`GET /v2/workspace/opportunities/{id}/threads`).
 */
import { useCallback, useState } from "react";
import { fetchCaseThreads } from "../crmApi";
import type { CaseThread } from "../crmTypes";
import { inSharedMailbox } from "../gmailLinks";
import { useResource } from "../useResource";
import { fmtDateTime, isSafeExternalHref } from "../ui";

const KIND: Record<CaseThread["kind"], { label: string; className: string }> = {
  cliente: { label: "Cliente", className: "bg-brand-50 text-brand-900 border-brand-600/25" },
  proveedor: { label: "Proveedor", className: "bg-canvas-sunken text-ink border-line-strong" },
  interno: { label: "Interno", className: "bg-canvas-sunken text-ink-muted border-line" },
};

const SHOWN = 3;

function Thread({ t }: { t: CaseThread }) {
  const [all, setAll] = useState(false);
  const messages = all ? t.messages : t.messages.slice(0, SHOWN);
  const url = inSharedMailbox(t.url);
  const kind = KIND[t.kind] ?? KIND.cliente;
  return (
    <article className="rounded-lg border border-line bg-canvas-raised" data-testid="case-thread">
      <header className="flex items-start gap-2 px-3 pt-2.5">
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13px] font-medium text-ink" title={t.subject ?? undefined}>
            {t.subject || "Sin asunto"}
          </p>
          <p className="mt-0.5 flex items-center gap-1.5 text-[11px] text-ink-muted">
            <span className={`rounded-full border px-1.5 leading-4 ${kind.className}`}>{kind.label}</span>
            {t.count === 1 ? "1 correo" : `${t.count} correos`}
          </p>
        </div>
        {url && isSafeExternalHref(url) ? (
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="shrink-0 rounded-md px-1.5 py-0.5 text-xs font-medium text-brand-700 hover:bg-brand-50"
            aria-label={`Abrir la cadena «${t.subject ?? "sin asunto"}» en Gmail`}
          >
            Abrir ↗
          </a>
        ) : null}
      </header>
      <ol className="mt-1.5 px-3 pb-2.5">
        {messages.map((m, i) => {
          const out = m.direction === "outbound";
          const href = inSharedMailbox(m.url);
          const body = (
            <>
              <span aria-hidden="true" className={`w-3 shrink-0 text-center ${out ? "text-brand-700" : "text-ink-muted"}`}>
                {out ? "↗" : "↙"}
              </span>
              <span className="min-w-0 flex-1 truncate text-ink">
                <span className="sr-only">{out ? "Enviado por " : "Recibido de "}</span>
                {out ? "OrigenLab" : m.from ?? "Remitente"}
                {m.has_pdf ? <span className="ml-1.5 text-[11px] font-medium text-ink-muted">PDF</span> : null}
              </span>
              <span className="shrink-0 tabular-nums text-ink-faint">{fmtDateTime(m.sent_at)}</span>
            </>
          );
          return (
            <li key={m.message_id ?? i} className="border-t border-line/60 first:border-t-0">
              {href && isSafeExternalHref(href) ? (
                <a
                  href={href}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={m.subject ?? undefined}
                  className="-mx-1.5 flex items-center gap-2 rounded px-1.5 py-1 text-xs hover:bg-canvas-sunken"
                >
                  {body}
                </a>
              ) : (
                <span className="flex items-center gap-2 py-1 text-xs">{body}</span>
              )}
            </li>
          );
        })}
      </ol>
      {t.messages.length > SHOWN ? (
        <button
          type="button"
          onClick={() => setAll(!all)}
          className="w-full border-t border-line px-3 py-1.5 text-left text-[11px] font-medium text-brand-700 hover:bg-canvas-sunken"
        >
          {all ? "Ver menos" : `Ver todo (${t.messages.length})`}
        </button>
      ) : null}
    </article>
  );
}

export function CaseThreads({ opportunityId }: { opportunityId: string }) {
  const load = useCallback(() => fetchCaseThreads(opportunityId), [opportunityId]);
  const [state] = useResource(load, [opportunityId]);
  if (state.kind === "loading") {
    return <div className="h-16 animate-pulse rounded-lg bg-canvas-sunken/70" aria-label="Cargando correos" />;
  }
  if (state.kind !== "ready") {
    return <p className="text-xs text-ink-muted">No se pudieron leer los correos de este caso.</p>;
  }
  const threads = state.data.threads;
  if (threads.length === 0) return <p className="text-xs text-ink-muted">No hay correos capturados para este caso.</p>;
  return (
    <div className="space-y-2.5" data-testid="case-threads">
      {threads.map((t) => (
        <Thread key={t.thread_id} t={t} />
      ))}
    </div>
  );
}
