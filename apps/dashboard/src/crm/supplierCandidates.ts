/**
 * Machine-detected supplier candidates, as `/v2/workspace/providers` returns them.
 *
 * The API reports each candidate's review state under `review.state` (`unresolved`,
 * `confirmed`, `rejected`, `ambiguous`, …). Every count and every row label in the dashboard
 * goes through `candidateState`, so a header can never disagree with the list under it.
 */

export interface SupplierCandidateReview {
  state: string;
  decided_at?: string | null;
  note?: string | null;
}

export interface SupplierCandidate {
  /** `evidence.assertion.id` — present on the current API; what confirm / reject act on. */
  assertion_id?: string;
  domain: string;
  trade_name: string | null;
  /** The canonical review state. */
  review?: SupplierCandidateReview | null;
  /** The field older API builds sent instead of `review`. Read only when `review` is absent. */
  resolution?: string;
}

export const UNREVIEWED = "unresolved";

export function candidateState(c: SupplierCandidate): string {
  return c.review?.state ?? c.resolution ?? UNREVIEWED;
}

export function isUnreviewed(c: SupplierCandidate): boolean {
  return candidateState(c) === UNREVIEWED;
}

export function countUnreviewed(list: readonly SupplierCandidate[]): number {
  return list.filter(isUnreviewed).length;
}

/** A stable React key: the assertion when known, else the domain (siblings may share one). */
export function candidateKey(c: SupplierCandidate, index: number): string {
  return c.assertion_id ?? `${c.domain}#${index}`;
}
