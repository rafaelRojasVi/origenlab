/**
 * Which Idempotency-Key a command carries.
 *
 * The API keeps one receipt per (operator, key): the same request sent again under its key gets
 * the stored answer, and the same key with a different body is refused (409
 * `idempotency_key_reused`). So a form keeps its key only to resend the identical request whose
 * answer never arrived — the network failed, the Worker timed out, the server answered 5xx — and
 * takes a new one once an answer settled the attempt: a success, or a 4xx refusal (a refused
 * command writes nothing, its receipt included).
 */
import { useRef } from "react";
import { OperatorApiError } from "../api/operatorClient";
import { parseRefusal } from "./commandRefusal";

export function newIdempotencyKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `cmd-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** Refusals that mean "the first request under this key may still be running": keep the key. */
const STILL_RUNNING = new Set(["command_in_progress", "idempotency_conflict"]);

/** True when the answer settled the attempt: a success (no error) or a 4xx refusal. */
export function settlesAttempt(err?: unknown): boolean {
  if (err === undefined) return true;
  if (!(err instanceof OperatorApiError) || err.status < 400 || err.status >= 500) return false;
  return !STILL_RUNNING.has(parseRefusal(err.status, err.message).code);
}

/** One form's key: reused only to retry the identical body after an unanswered attempt. */
export class CommandKey {
  private key = newIdempotencyKey();
  private sent: string | null = null;

  /** The key to send `body` under: the pending one for the identical request, else a new one. */
  keyFor(body: unknown): string {
    const sent = JSON.stringify(body) ?? "";
    if (this.sent !== null && this.sent !== sent) this.key = newIdempotencyKey();
    this.sent = sent;
    return this.key;
  }

  /** After the answer: `err` undefined is a success. Rotates unless the attempt is unsettled. */
  settle(err?: unknown): void {
    if (!settlesAttempt(err)) return;
    this.key = newIdempotencyKey();
    this.sent = null;
  }
}

/** A `CommandKey` that lives as long as the component. */
export function useCommandKey(): CommandKey {
  const ref = useRef<CommandKey | null>(null);
  if (ref.current === null) ref.current = new CommandKey();
  return ref.current;
}
