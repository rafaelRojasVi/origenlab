/**
 * The case contact's phones, each a call link and a WhatsApp link. Only for the roles that read
 * contact details as recorded (sales, admin): anyone else gets masked values from the API, so the
 * block is not shown and the person is not even read.
 */
import { useCallback } from "react";
import { useAuthSession } from "../../context/AuthSessionContext";
import { fetchPersonAuthoring } from "../authoring/crmAuthoringApi";
import { contactAddressesRedacted } from "../redaction";
import { useResource } from "../useResource";

/** `https://wa.me/` takes the number as digits only, country code first (Chile's 56 stays). */
export function whatsAppUrl(phone: string): string | null {
  const digits = phone.replace(/\D/g, "");
  return digits.length >= 8 ? `https://wa.me/${digits}` : null;
}

const LINK =
  "inline-flex min-h-11 items-center rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:border-line-strong focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600";

function Phones({ personId }: { personId: string }) {
  const load = useCallback(() => fetchPersonAuthoring(personId), [personId]);
  const [state] = useResource(load, [personId]);
  if (state.kind !== "ready") return null;
  const phones = state.data.contact_points.filter((c) => c.kind === "phone" && c.status === "active");
  if (phones.length === 0) return null;
  return (
    <ul className="mt-2 space-y-2" data-testid="person-phones">
      {phones.map((p) => {
        const dial = p.value_norm || p.value_display.replace(/[^\d+]/g, "");
        const wa = whatsAppUrl(p.value_norm || p.value_display);
        return (
          <li key={p.id} className="flex flex-wrap items-center gap-2">
            <span className="min-w-0 flex-1 text-[13px] text-ink tabular-nums">
              <span className="mr-2 text-ink-faint">Teléfono</span>
              {p.value_display}
            </span>
            <a href={`tel:${dial}`} className={LINK} aria-label={`Llamar al ${p.value_display}`}>
              Llamar
            </a>
            {wa ? (
              <a href={wa} target="_blank" rel="noopener noreferrer" className={LINK} aria-label={`WhatsApp al ${p.value_display}`}>
                WhatsApp <span aria-hidden="true" className="ml-1 text-ink-faint">↗</span>
              </a>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

export function PersonPhones({ personId }: { personId: string }) {
  const { session } = useAuthSession();
  if (session.kind !== "signed_in" || contactAddressesRedacted(session)) return null;
  return <Phones personId={personId} />;
}
