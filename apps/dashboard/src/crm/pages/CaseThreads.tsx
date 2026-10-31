/**
 * «Correos del caso» as Gmail cadenas (owner request 2026-10-11): a case often lives in several
 * threads (the request, a forward, the quote sent in a new email, the client's answer), and
 * showing only the newest email each way hid which cadena an email belonged to.
 *
 * One block per cadena, newest activity first: subject once, who it is with (cliente / proveedor
 * / interno), how many emails and the last date, «Abrir en Gmail»; then one line per email,
 * newest first, the last three shown and «Ver todo» for the rest. Senders come by name or domain
 * only. Read-only (`GET /v2/workspace/opportunities/{id}/threads`).
 *
 * An email opens in place (`MailMessage`, owner 2026-10-25), one at a time; a role that does not
 * read addresses keeps the Gmail link.
 */
import { useCallback, useState } from "react";
import { fetchCaseThreads } from "../crmApi";
import type { CaseThread } from "../crmTypes";
import { inSharedMailbox } from "../gmailLinks";
import { useResource } from "../useResource";
import { fmtDateTime, isSafeExternalHref } from "../ui";
import { PersonGuessRow } from "../authoring/PersonGuessRow";
import { useMayRunCaseCommands } from "../caseCommands";
import { MailMessage, useMayReadMail } from "./MailMessage";

const KIND: Record<CaseThread["kind"], { label: string; className: string }> = {
  cliente: { label: "Cliente", className: "bg-brand-50 text-accent-strong border-brand-600/25" },
  proveedor: { label: "Proveedor", className: "bg-canvas-sunken text-ink border-line-strong" },
  interno: { label: "Interno", className: "bg-canvas-sunken text-ink-muted border-line" },
};

const SHOWN = 3;

function Thread({ t, opportunityId, mayRead }: { t: CaseThread; opportunityId: string; mayRead: boolean }) {
  const [all, setAll] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [answered, setAnswered] = useState<Set<string>>(new Set());
  const mayAnswer = useMayRunCaseCommands();
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
            className="shrink-0 rounded-md px-1.5 py-0.5 text-xs font-medium text-accent hover:bg-brand-50"
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
              <span aria-hidden="true" className={`w-3 shrink-0 text-center ${out ? "text-accent" : "text-ink-muted"}`}>
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
          const id = m.message_id;
          const expanded = id !== null && open === id;
          return (
            <li key={m.message_id ?? i} className="border-t border-line/60 first:border-t-0">
              {mayRead && id ? (
                <>
                  <button
                    type="button"
                    onClick={() => setOpen(expanded ? null : id)}
                    aria-expanded={expanded}
                    title={m.subject ?? undefined}
                    className={`-mx-1.5 flex w-[calc(100%+0.75rem)] items-center gap-2 rounded px-1.5 py-1 text-left text-xs hover:bg-canvas-sunken focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 ${expanded ? "bg-canvas-sunken/70" : ""}`}
                  >
                    {body}
                  </button>
                  {expanded ? (
                    <div className="crm-mail-open mb-1.5 mt-1 rounded-md border border-line bg-canvas px-3 py-2.5">
                      <MailMessage opportunityId={opportunityId} messageId={id} gmailUrl={href} inline />
                      {href && isSafeExternalHref(href) ? (
                        <div className="mt-3 flex justify-end border-t border-line/70 pt-2">
                          <a
                            href={href}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="rounded-md px-1.5 py-0.5 text-xs font-medium text-accent hover:bg-brand-50"
                          >
                            Responder en Gmail ↗
                          </a>
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </>
              ) : href && isSafeExternalHref(href) ? (
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
              {(m.person_guesses ?? []).filter((g) => !answered.has(g.guess_id)).map((g) => (
                <div key={g.guess_id} className="mb-1.5 ml-5">
                  <PersonGuessRow guess={g} mayAnswer={mayAnswer}
                    onAnswered={() => setAnswered((a) => new Set([...a, g.guess_id]))} />
                </div>
              ))}
            </li>
          );
        })}
      </ol>
      {t.messages.length > SHOWN ? (
        <button
          type="button"
          onClick={() => setAll(!all)}
          className="w-full border-t border-line px-3 py-1.5 text-left text-[11px] font-medium text-accent hover:bg-canvas-sunken"
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
  const mayRead = useMayReadMail();
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
        <Thread key={t.thread_id} t={t} opportunityId={opportunityId} mayRead={mayRead} />
      ))}
    </div>
  );
}
