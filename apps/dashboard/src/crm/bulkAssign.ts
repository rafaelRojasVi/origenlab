/**
 * «Asignar» several Seguimiento cases at once: one `set-case-follow-up` per case, one after the
 * other, each under its own idempotency key. A refusal stops nothing; the result counts both, and
 * says whether any was a version conflict (409), so the page reads the cases again.
 */
import { OperatorApiError } from "../api/operatorClient";
import { newCaseCommandKey, setCaseFollowUp, type SetCaseFollowUpBody } from "./caseCommands";

export interface BulkResult {
  ok: number;
  failed: number;
  conflict: boolean;
}

export async function runBulkFollowUp(
  bodies: SetCaseFollowUpBody[],
  onProgress: (done: number, total: number) => void = () => undefined,
  send: (body: SetCaseFollowUpBody, key: string) => Promise<unknown> = setCaseFollowUp,
): Promise<BulkResult> {
  const out: BulkResult = { ok: 0, failed: 0, conflict: false };
  for (const [i, body] of bodies.entries()) {
    try {
      await send(body, newCaseCommandKey());
      out.ok += 1;
    } catch (err) {
      out.failed += 1;
      if (err instanceof OperatorApiError && err.status === 409) out.conflict = true;
    }
    onProgress(i + 1, bodies.length);
  }
  return out;
}

/** «12 asignados, 1 falló». */
export function bulkResultText(r: BulkResult, skipped = 0): string {
  const parts = [`${r.ok} ${r.ok === 1 ? "asignado" : "asignados"}`];
  if (r.failed) parts.push(`${r.failed} ${r.failed === 1 ? "falló" : "fallaron"}`);
  if (skipped) parts.push(`${skipped} sin cambio`);
  return parts.join(", ");
}
