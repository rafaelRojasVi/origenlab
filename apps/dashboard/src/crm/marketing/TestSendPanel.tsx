import { useCallback, useId, useState } from "react";
import { OperatorApiError } from "../../api/operatorClient";
import { useAuthSession } from "../../context/AuthSessionContext";
import { fmtDate } from "../ui";
import { useResource } from "../useResource";
import { fetchTestSendHistory, sendCampaignTest, type TestSendTarget } from "./marketingApi";

const PLAIN_ADDRESS = /^[^@\s,;<>"']+@[^@\s,;<>"']+\.[^@\s,;<>"']+$/;

const HOUR_SANTIAGO = new Intl.DateTimeFormat("es-CL", {
  timeZone: "America/Santiago",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function errorText(err: unknown): string {
  if (err instanceof OperatorApiError) {
    let detail: { message?: string; next_allowed_at?: string } | undefined;
    try {
      detail = JSON.parse(err.message)?.detail;
    } catch {
      /* not JSON */
    }
    if (err.status === 429) {
      const at = detail?.next_allowed_at ? new Date(detail.next_allowed_at) : null;
      if (at && !Number.isNaN(at.getTime())) {
        return `Límite de pruebas alcanzado. La próxima se puede enviar a las ${HOUR_SANTIAGO.format(at)}.`;
      }
      return "Límite de pruebas alcanzado. Intenta más tarde.";
    }
    if (err.status === 403) return "Sólo un perfil de administración puede enviar pruebas.";
    try {
      if (detail?.message) return String(detail.message);
    } catch {
      /* not JSON */
    }
  }
  return "No se pudo enviar la prueba.";
}

/** «Enviar prueba»: the campaign's own email, to one address, from the shared account. Admin only. */
export function TestSendPanel({
  target,
  config,
}: {
  target: TestSendTarget;
  config: { enabled: boolean; per_hour: number; per_day: number; sender: string } | undefined;
}) {
  const { session } = useAuthSession();
  const isAdmin = session.kind === "signed_in" && session.operator.role === "admin";
  if (!config?.enabled || !isAdmin) return null;
  return <Panel target={target} sender={config.sender} />;
}

function Panel({ target, sender }: { target: TestSendTarget; sender: string }) {
  const inputId = useId();
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const key = JSON.stringify(target);
  const [history, reload] = useResource(() => fetchTestSendHistory(target), [key]);
  const valid = PLAIN_ADDRESS.test(to.trim());
  const send = useCallback(async () => {
    setBusy(true);
    setResult(null);
    try {
      const r = await sendCampaignTest(target, to.trim());
      setResult({ ok: true, text: `Enviada a ${r.to}. Revisa la bandeja de entrada y el spam.` });
      reload();
    } catch (err) {
      setResult({ ok: false, text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }, [target, to, reload]);
  return (
    <div className="space-y-2 rounded-lg border border-line bg-canvas-sunken/50 p-3">
      <label htmlFor={inputId} className="block text-[12px] font-medium text-ink">
        Enviar una prueba a
      </label>
      <div className="flex gap-2">
        <input id={inputId} type="email" inputMode="email" autoComplete="email" value={to}
               onChange={(e) => setTo(e.target.value)} placeholder="nombre@gmail.com"
               className="h-8 min-w-0 flex-1 rounded-md border border-line bg-canvas-raised px-2 text-[13px] text-ink focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-600/20" />
        <button type="button" onClick={() => void send()} disabled={!valid || busy}
                className="h-8 shrink-0 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black disabled:cursor-not-allowed disabled:opacity-50">
          Enviar prueba
        </button>
      </div>
      <p className="text-[11px] text-ink-faint">
        Llega desde {sender} con «[PRUEBA]» en el asunto, igual que el correo de la campaña.
        {history.kind === "ready" ? ` Quedan ${history.data.remaining.hour} esta hora.` : ""}
      </p>
      {result ? (
        <p role={result.ok ? "status" : "alert"} className={`text-xs ${result.ok ? "text-good" : "text-bad"}`}>{result.text}</p>
      ) : null}
      {history.kind === "ready" && history.data.tests.length > 0 ? (
        <ul className="divide-y divide-line text-[11px] text-ink-muted">
          {history.data.tests.map((t) => (
            <li key={`${t.at}-${t.to}`} className="flex justify-between gap-2 py-1">
              <span className="truncate">{t.to}</span>
              <span className="shrink-0">{t.status === "sent" ? "enviada" : "falló"} · {t.by ?? "—"} · {fmtDate(t.at)}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
