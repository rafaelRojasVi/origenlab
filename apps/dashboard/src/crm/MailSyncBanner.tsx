import { fetchMailSync } from "./crmApi";
import type { MailSyncStatus } from "./crmTypes";
import { santiagoDay, santiagoTime, todayInSantiago } from "./marketing/calendar";
import { fmtDate } from "./ui";
import { useResource } from "./useResource";

/**
 * The Gmail capture's state, said only when it needs a person (spec §6): stopped — consent revoked,
 * or paused after it had run — or late, no completed run for 30 minutes. Nothing while it runs,
 * before go-live, or where the read is not deployed (`unavailable`).
 */
export function mailSyncNotice(s: MailSyncStatus, now: Date = new Date()): { tone: "bad" | "warn"; text: string } | null {
  const at = s.last_synced_at ? when(s.last_synced_at, now) : null;
  if (s.state === "stopped") {
    return { tone: "bad", text: at ? `Sincronización de correo detenida desde ${at}` : "Sincronización de correo detenida" };
  }
  if (s.state === "late") {
    return { tone: "warn", text: at ? `Sincronización de correo atrasada (última: ${at})` : "Sincronización de correo atrasada" };
  }
  return null;
}

function when(iso: string, now: Date): string {
  const time = santiagoTime(iso);
  return santiagoDay(iso) === todayInSantiago(now) ? time : `${fmtDate(iso)} ${time}`;
}

export function MailSyncBanner({ refreshKey }: { refreshKey: string }) {
  const [state] = useResource(fetchMailSync, [refreshKey]);
  if (state.kind !== "ready") return null;
  const notice = mailSyncNotice(state.data);
  if (!notice) return null;
  const tone = notice.tone === "bad" ? "border-bad/30 bg-bad-bg text-bad" : "border-warn/30 bg-warn-bg text-warn";
  return (
    <div role="status" data-testid="mail-sync-banner" className={`border-b px-4 py-2 text-[13px] font-medium sm:px-6 ${tone}`}>
      <p className="mx-auto max-w-[1600px]">{notice.text}. Mientras tanto, los correos nuevos no aparecen en el CRM.</p>
    </div>
  );
}
