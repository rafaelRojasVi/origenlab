/**
 * One case email, read here instead of in a new Gmail tab (owner 2026-10-25: «pressing Responder
 * opens a new window»). Gmail cannot be reused from the dashboard — its opener policy drops the
 * tab's name — so the email is shown in place and Gmail is one click away for the answer.
 *
 * Quiet by design: who and when on one line, the text, and the quoted earlier conversation folded
 * away. Plain text only (`GET /v2/workspace/opportunities/{id}/messages/{message_id}`); for the
 * roles that read addresses (`useMayReadMail`), everyone else keeps the Gmail link.
 */
import { useCallback, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useAuthSession } from "../../context/AuthSessionContext";
import { fetchCaseMessage } from "../crmApi";
import type { CaseMessage } from "../crmTypes";
import { contactAddressesRedacted } from "../redaction";
import { useResource } from "../useResource";
import { Drawer, fmtDateTime, isSafeExternalHref } from "../ui";

/** Whether the signed-in operator may open an email here (the API's rule: sales and admin). */
export function useMayReadMail(): boolean {
  const { session } = useAuthSession();
  return session.kind === "signed_in" && !contactAddressesRedacted(session);
}

function Paperclip() {
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16" className="h-3 w-3 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="m13.5 7.5-5.3 5.3a3.2 3.2 0 0 1-4.5-4.5l5.6-5.6a2.1 2.1 0 0 1 3 3L6.8 11.2a1 1 0 0 1-1.5-1.5l4.9-4.9" />
    </svg>
  );
}

function Body({ m, inline }: { m: CaseMessage; inline: boolean }) {
  const [quoted, setQuoted] = useState(false);
  const to = [...m.to, ...m.cc];
  return (
    <div data-testid="mail-message">
      {/* In a thread the line above already says who and when. */}
      {inline ? null : (
        <div className="flex items-baseline gap-2 text-xs">
          <span className="min-w-0 truncate font-semibold text-ink">{m.from ?? "Remitente"}</span>
          <span className="ml-auto shrink-0 tabular-nums text-ink-faint">{fmtDateTime(m.sent_at)}</span>
        </div>
      )}
      {to.length ? (
        <p className={`${inline ? "" : "mt-0.5 "}truncate text-[11px] text-ink-faint`} title={to.join(", ")}>
          Para {to.join(", ")}
        </p>
      ) : null}
      <p className={`${inline && !to.length ? "" : "mt-2.5 "}whitespace-pre-wrap break-words text-[13px] leading-relaxed text-ink`}>
        {m.text || <span className="text-ink-muted">Sin texto.</span>}
      </p>
      {m.truncated ? (
        <p className="mt-2 text-[11px] text-ink-faint">El correo sigue: el resto está en Gmail.</p>
      ) : null}
      {m.attachments.length ? (
        <ul className="mt-3 flex flex-wrap gap-1.5" aria-label="Adjuntos">
          {m.attachments.map((a, i) => (
            <li
              key={`${a.filename}-${i}`}
              className="inline-flex max-w-full items-center gap-1 rounded-md border border-line bg-canvas-sunken/60 px-1.5 py-0.5 text-[11px] text-ink-muted"
              title={a.filename}
            >
              <Paperclip />
              <span className="truncate">{a.filename}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {m.quoted ? (
        <div className="mt-3">
          <button
            type="button"
            onClick={() => setQuoted(!quoted)}
            aria-expanded={quoted}
            className="rounded-sm text-[11px] text-ink-faint hover:text-ink-muted focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
          >
            {quoted ? "Ocultar conversación anterior" : "··· Ver conversación anterior"}
          </button>
          {quoted ? (
            <p className="mt-1.5 whitespace-pre-wrap break-words border-l-2 border-line pl-3 text-xs leading-relaxed text-ink-muted">
              {m.quoted}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** The email itself; a short line and the Gmail link when it cannot be opened here. */
export function MailMessage({
  opportunityId,
  messageId,
  gmailUrl,
  inline = false,
}: {
  opportunityId: string;
  messageId: string;
  gmailUrl: string | null;
  /** Inside a thread line that already names the sender and the date. */
  inline?: boolean;
}) {
  const load = useCallback(() => fetchCaseMessage(opportunityId, messageId), [opportunityId, messageId]);
  const [state] = useResource(load, [opportunityId, messageId]);
  if (state.kind === "loading") {
    return (
      <div className="space-y-2" aria-label="Abriendo correo">
        <div className="h-3 w-1/3 animate-pulse rounded bg-canvas-sunken" />
        <div className="h-3 w-full animate-pulse rounded bg-canvas-sunken" />
        <div className="h-3 w-4/5 animate-pulse rounded bg-canvas-sunken" />
      </div>
    );
  }
  if (state.kind !== "ready") {
    return (
      <p className="text-xs text-ink-muted">
        Este correo no se puede mostrar aquí.
        {gmailUrl && isSafeExternalHref(gmailUrl) ? (
          <>
            {" "}
            <a href={gmailUrl} target="_blank" rel="noopener noreferrer" className="font-medium text-accent hover:underline">
              Ábrelo en Gmail ↗
            </a>
          </>
        ) : null}
      </p>
    );
  }
  return <Body m={state.data} inline={inline} />;
}

/**
 * «Responder» in Hoy: the client's email in a side panel, «Responder en Gmail» first. Portalled to
 * the body — the queue rows animate with a transform, which would trap a fixed panel inside them.
 */
export function MailReaderDrawer({
  open,
  onClose,
  title,
  subject,
  opportunityId,
  messageId,
  gmailUrl,
  extra,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  subject: string | null;
  opportunityId: string;
  messageId: string;
  gmailUrl: string | null;
  /** A secondary action beside «Responder en Gmail» (Hoy: «Abrir caso»). */
  extra?: ReactNode;
}) {
  if (!open) return null;
  return createPortal(
    <Drawer open onClose={onClose} title={title} subtitle={subject ?? undefined}>
      <div className="flex flex-wrap items-center gap-2">
        {gmailUrl && isSafeExternalHref(gmailUrl) ? (
          <a
            href={gmailUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex h-8 items-center rounded-md bg-brand-600 px-3 text-xs font-semibold text-on-solid shadow-sm hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 focus-visible:ring-offset-1"
          >
            Responder en Gmail ↗
          </a>
        ) : null}
        {extra}
      </div>
      <MailMessage opportunityId={opportunityId} messageId={messageId} gmailUrl={gmailUrl} />
    </Drawer>,
    document.body,
  );
}
