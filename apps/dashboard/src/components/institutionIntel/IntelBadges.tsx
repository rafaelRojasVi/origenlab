import type { ProcurementEligibilityStatus, QueueName } from "../../api/institutionIntel/types";
import { ELIGIBILITY_LABEL, formatQueueName } from "../../lib/institutionIntelLabels";

const ELIGIBILITY_TONE: Record<ProcurementEligibilityStatus, string> = {
  open_public: "bg-emerald-50 text-emerald-800 ring-1 ring-emerald-200",
  restricted_invitation_unconfirmed: "bg-amber-50 text-amber-800 ring-1 ring-amber-200",
  unknown: "bg-slate-100 text-slate-600 ring-1 ring-slate-200",
};

export function EligibilityBadge({ status }: { status: ProcurementEligibilityStatus }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ${ELIGIBILITY_TONE[status]}`}
    >
      {ELIGIBILITY_LABEL[status]}
    </span>
  );
}

const QUEUE_TONE: Record<QueueName, string> = {
  current_opportunity_queue: "bg-emerald-50 text-emerald-800 ring-1 ring-emerald-200",
  historical_prospect_queue: "bg-sky-50 text-sky-800 ring-1 ring-sky-200",
  contact_gap_queue: "bg-amber-50 text-amber-800 ring-1 ring-amber-200",
  institution_match_review_queue: "bg-violet-50 text-violet-800 ring-1 ring-violet-200",
  line_evidence_review_queue: "bg-slate-100 text-slate-700 ring-1 ring-slate-200",
  retender_review_queue: "bg-slate-100 text-slate-700 ring-1 ring-slate-200",
};

export function QueueBadge({ queue }: { queue: QueueName }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ${QUEUE_TONE[queue]}`}
    >
      {formatQueueName(queue)}
    </span>
  );
}
