/**
 * Gmail links that open the shared mailbox. The API writes `mail/u/0/#…`, which opens whichever
 * Google account is first in the browser; the team works in contacto@origenlab.cl, so every link
 * names it (`authuser`), as the top bar's Gmail shortcut does.
 */
export const SHARED_MAILBOX = "contacto@origenlab.cl";

const GMAIL = /^https:\/\/mail\.google\.com\/mail\/(?:u\/\d+\/)?(?:\?[^#]*)?(#.*)?$/;

/** The same message or thread, opened as the shared mailbox. Any other URL is returned as is. */
export function inSharedMailbox(url: string | null | undefined): string | null {
  if (!url) return null;
  const m = url.match(GMAIL);
  if (!m) return url;
  return `https://mail.google.com/mail/?authuser=${encodeURIComponent(SHARED_MAILBOX)}${m[1] ?? ""}`;
}

/** A new email from the shared mailbox, addressed and titled; for a case with no thread to reply on. */
export function composeInSharedMailbox(to: string | null, subject: string): string {
  const q = new URLSearchParams({ authuser: SHARED_MAILBOX, view: "cm", fs: "1", su: subject });
  if (to) q.set("to", to);
  return `https://mail.google.com/mail/?${q.toString()}`;
}
