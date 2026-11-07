/**
 * The deployment that follows «Publicar en el sitio»: asks the API for the run every 15 seconds while
 * it is queued, running or waiting for the production approval in GitHub, and stops on success or
 * failure and when the page is closed.
 */
import { useEffect, useState } from "react";
import { Badge, Button, ExternalLink, fmtDateTime, type Tone } from "../ui";
import { OperatorApiError } from "../../api/operatorClient";
import { fetchPublishStatus, type PublishState, type PublishStatus as Status } from "../web/webPublishApi";

export const STATUS_POLL_MS = 15_000;
/** One hour at 15 s: an approval nobody gives is not polled forever. */
const MAX_POLLS = 240;
/** When the run's commit is not the one we published (a later commit may have moved `main`), a run last
 *  touched before the publish is the previous deployment's, not this one's. */
const STALE_MS = 30_000;

const COPY: Record<PublishState, { tone: Tone; line: string }> = {
  queued: { tone: "info", line: "En cola en GitHub." },
  running: { tone: "info", line: "Construyendo el sitio…" },
  waiting: { tone: "warn", line: "Esperando aprobación en GitHub" },
  success: { tone: "good", line: "El sitio ya está actualizado." },
  failure: { tone: "bad", line: "El despliegue falló. Revisa el detalle en GitHub." },
  unknown: { tone: "neutral", line: "Buscando el despliegue en GitHub…" },
};

export function PublishStatus({ modelId, commitSha, runUrl, since }: { modelId: string; commitSha: string; runUrl: string; since: number }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [readFailed, setReadFailed] = useState(false);
  const [gaveUp, setGaveUp] = useState(false);
  const [check, setCheck] = useState(0);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let polls = 0;
    setGaveUp(false);
    const tick = async () => {
      try {
        const s = await fetchPublishStatus(modelId);
        if (!alive) return;
        const updated = s.updated_at ? Date.parse(s.updated_at) : NaN;
        const ours = !!s.head_sha && s.head_sha === commitSha;
        const previousRun = !ours && Number.isFinite(updated) && updated < since - STALE_MS;
        const shown: Status = previousRun ? { ...s, status: "unknown" } : s;
        setStatus(shown);
        setReadFailed(false);
        if (shown.status === "success" || shown.status === "failure") return;
      } catch (err) {
        if (!alive) return;
        setReadFailed(true);
        // Not allowed or not mounted: asking again changes nothing.
        if (err instanceof OperatorApiError && (err.status === 403 || err.status === 404)) return;
      }
      polls += 1;
      if (polls < MAX_POLLS) timer = setTimeout(() => void tick(), STATUS_POLL_MS);
      else if (alive) setGaveUp(true);
    };
    void tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [modelId, commitSha, since, check]);

  const state: PublishState = status?.status ?? "unknown";
  const copy = COPY[state];
  const done = state === "success" || state === "failure";
  const link = status?.run_url || runUrl;
  return (
    <div role="status" className="rounded-lg border border-line bg-canvas-raised px-3 py-3" data-testid="publish-status">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-[13px] font-semibold text-ink">
          {state === "success" ? "Publicado" : state === "failure" ? "Publicado en GitHub, pero el despliegue falló" : "Publicado — despliegue en curso"}
        </p>
        <Badge tone={copy.tone}>{state === "waiting" ? "Aprobación pendiente" : state === "success" ? "Listo" : state === "failure" ? "Falló" : "En curso"}</Badge>
        <ExternalLink href={link}>Ver el despliegue en GitHub</ExternalLink>
        <Button variant="quiet" className="ml-auto" onClick={() => setCheck((n) => n + 1)}>
          Verificar estado
        </Button>
      </div>
      <p className="mt-1 text-xs text-ink">{copy.line}</p>
      {state === "waiting" ? (
        <p className="mt-1 text-xs text-ink-muted">
          El despliegue a producción necesita que alguien lo apruebe en GitHub; el sitio cambia cuando se apruebe.
        </p>
      ) : null}
      {readFailed ? <p className="mt-1 text-[11px] text-warn">No se pudo consultar el estado.</p> : null}
      {gaveUp && !done ? <p className="mt-1 text-[11px] text-warn">Sin novedades del despliegue; revise el enlace</p> : null}
      {status?.updated_at && !done ? <p className="mt-1 text-[11px] text-ink-faint">Última novedad: {fmtDateTime(status.updated_at)}</p> : null}
    </div>
  );
}
