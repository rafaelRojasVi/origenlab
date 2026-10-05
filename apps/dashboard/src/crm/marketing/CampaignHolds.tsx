import { useState } from "react";
import { ReloadButton } from "../CommandErrorNotice";
import { useCommandKey } from "../commandKey";
import { isStaleRefusal, refusalFromError, refusalText, type Refusal } from "../commandRefusal";
import { Badge, Panel } from "../ui";
import { useResource } from "../useResource";
import { fmtLongDay, santiagoDay, santiagoTime } from "./calendar";
import { blockCampaign, fetchCampaignBlocks, unblockCampaign } from "./marketingApi";
import type { CampaignBlock, CampaignHoldsResponse } from "./marketingTypes";

/** What the block commands refuse, in the operator's words (the API's text is English). */
const BLOCK_WORDS: Readonly<Record<string, string>> = {
  stale_block_version:
    "Otro administrador bloqueó o desbloqueó esta campaña mientras decidías. Carga la versión actual; tu motivo sigue en el formulario.",
  stale_version: "Otro administrador cambió este bloqueo mientras decidías. Carga la versión actual; tu motivo sigue en el formulario.",
  already_blocked: "Esa campaña ya tiene un bloqueo activo.",
  block_already_lifted: "Ese bloqueo ya fue levantado.",
  role_may_not_block: "Sólo un perfil de administración activo bloquea o levanta un bloqueo.",
};

/**
 * Campaign safety blocks (WORKFLOWS.md §W13). Every role sees whether a campaign is held; sales
 * and admin also see why and by whom; only an admin, on an API with the block commands enabled
 * (`may_decide`), places or lifts one — each with a mandatory reason and its own confirmation.
 * A block only stops things: nothing here sends, enqueues, approves or schedules.
 */

const EFFECT_SHORT = "Rechaza congelar, aprobar, probar en seco, reservar y enviar. No envía ni modifica nada. No vence.";

function when(iso: string): string {
  return `${fmtLongDay(santiagoDay(iso))} · ${santiagoTime(iso)}`;
}

function BlockLine({ b }: { b: CampaignBlock }) {
  return (
    <div className="space-y-0.5 text-xs" data-testid="block-line">
      <p className="font-medium text-ink">
        {b.scope === "legacy_campaign" ? `Campaña V1 «${b.legacy_campaign_key}»` : b.scope_label}
        {b.reference ? <span className="ml-1 font-mono text-[11px] text-ink-muted">{b.reference}</span> : null}
      </p>
      <p className="text-ink-muted">
        Desde {when(b.placed_at)}
        {b.placed_by ? ` · ${b.placed_by}` : ""}
      </p>
      {b.reason ? <p className="whitespace-pre-wrap text-ink" data-testid="block-reason">{b.reason}</p> : null}
      {b.redacted ? <p className="text-[11px] text-ink-faint">El motivo sólo es visible para Ventas y Administración.</p> : null}
    </div>
  );
}

/** A reason, a confirmation and one Idempotency-Key per intended decision. */
function DecisionForm({
  label,
  confirmText,
  submitLabel,
  testId,
  onSubmit,
  onReload,
}: {
  label: string;
  confirmText: string;
  submitLabel: string;
  testId: string;
  onSubmit: (reason: string, key: string) => Promise<void>;
  /** Re-read the holds after a stale version; the form and its reason stay. */
  onReload: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Refusal | null>(null);
  // One key per intended decision: resent only while the same reason's answer is missing.
  const key = useCommandKey();
  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="h-7 rounded-md border border-line px-3 text-xs font-medium text-ink hover:bg-canvas-sunken"
        data-testid={`${testId}-open`}
      >
        {label}
      </button>
    );
  }
  const clean = reason.trim();
  return (
    <form
      className="space-y-2 rounded-md border border-line bg-canvas-sunken p-2"
      data-testid={`${testId}-form`}
      onSubmit={async (e) => {
        e.preventDefault();
        if (!clean || !confirmed) return;
        setBusy(true);
        setError(null);
        try {
          await onSubmit(clean, key.keyFor(clean));
          key.settle();
          setOpen(false);
          setReason("");
          setConfirmed(false);
        } catch (err) {
          key.settle(err);
          setError(refusalFromError(err));
        } finally {
          setBusy(false);
        }
      }}
    >
      <label className="block text-[11px] text-ink-muted">
        Motivo (obligatorio)
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          maxLength={2000}
          rows={3}
          className="mt-0.5 block w-full rounded-md border border-line bg-canvas-raised px-2 py-1 text-xs text-ink"
          data-testid={`${testId}-reason`}
        />
      </label>
      <label className="flex items-start gap-1.5 text-[11px] text-ink">
        <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} data-testid={`${testId}-confirm`} />
        <span>{confirmText}</span>
      </label>
      <div className="flex items-center gap-2">
        <button
          type="submit"
          disabled={busy || !clean || !confirmed}
          className="h-7 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black disabled:opacity-40"
          data-testid={`${testId}-submit`}
        >
          {submitLabel}
        </button>
        <button type="button" disabled={busy} onClick={() => setOpen(false)} className="text-xs text-ink-muted hover:underline">
          Cancelar
        </button>
      </div>
      {error ? (
        <p role="alert" className="text-[11px] text-bad">
          {refusalText(error, { fallback: "No se pudo registrar la decisión", overrides: BLOCK_WORDS })}
          {isStaleRefusal(error) || error.code === "stale_block_version" ? (
            <>
              {" "}
              <ReloadButton onReload={() => { setError(null); onReload(); }} />
            </>
          ) : null}
        </p>
      ) : null}
    </form>
  );
}

function LiftForm({ b, onDone, onReload }: { b: CampaignBlock; onDone: () => void; onReload: () => void }) {
  return (
    <DecisionForm
      onReload={onReload}
      label="Levantar bloqueo"
      testId={`unblock-${b.block_id}`}
      submitLabel="Levantar bloqueo"
      confirmText="Levantar el bloqueo no envía nada: sólo deja de rechazar los pasos hacia un envío. Queda registrado con mi nombre y este motivo."
      onSubmit={async (reason, key) => {
        await unblockCampaign({ block_id: b.block_id, expected_version: b.version, reason }, key);
        onDone();
      }}
    />
  );
}

/**
 * The holds that are not about one campaign: a block on every campaign, and holds on V1
 * campaigns (the September wave-2 incident hold). Shown at the top of the Marketing section.
 */
export function HoldsBanner({ onChanged }: { onChanged?: () => void }) {
  const [state, reload] = useResource(fetchCampaignBlocks);
  if (state.kind !== "ready") return null;
  const h = state.data;
  const global = h.all_campaigns.block;
  const done = () => {
    reload();
    onChanged?.();
  };
  if (!global && h.legacy.length === 0 && !h.may_decide) return null;
  return (
    <section
      aria-label="Bloqueos de campañas"
      className={`rounded-lg border p-3 ${global ? "border-bad/40 bg-bad-bg" : "border-line bg-canvas-raised"}`}
      data-testid="holds-banner"
    >
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-[13px] font-semibold text-ink">
          {global ? "Todas las campañas están bloqueadas" : "Bloqueos de seguridad de campañas"}
        </p>
        {global ? <Badge tone="bad">Bloqueo general</Badge> : null}
        {h.legacy.length ? <Badge tone="warn">{`${h.legacy.length} retención(es) V1`}</Badge> : null}
      </div>
      <p className="mt-1 text-[11px] text-ink-muted">{EFFECT_SHORT}</p>
      <div className="mt-2 space-y-3">
        {global ? (
          <div className="space-y-1.5">
            <BlockLine b={global} />
            {h.may_decide ? <LiftForm b={global} onDone={done} onReload={reload} /> : null}
          </div>
        ) : h.may_decide ? (
          <DecisionForm
            onReload={reload}
            label="Bloquear todas las campañas"
            testId="block-all"
            submitLabel="Bloquear todas"
            confirmText="Bloquear todas las campañas no envía ni encola nada y no modifica ninguna audiencia congelada. Queda activo hasta que un administrador lo levante."
            onSubmit={async (reason, key) => {
              await blockCampaign(
                { scope: "all_campaigns", campaign_id: null, expected_block_version: h.all_campaigns.block_version, reason },
                key,
              );
              done();
            }}
          />
        ) : null}
        {h.legacy.map((b) => (
          <div key={b.block_id} className="space-y-1.5 border-t border-line/70 pt-2" data-testid="legacy-hold">
            <BlockLine b={b} />
            <p className="text-[11px] text-ink-faint">
              Registrada por la migración: la campaña vive en el registro V1 y no tiene fila en este CRM.
            </p>
            {h.may_decide ? <LiftForm b={b} onDone={done} onReload={reload} /> : null}
          </div>
        ))}
      </div>
    </section>
  );
}

/** One campaign's hold, and — for an admin — the block or lift decision. */
export function CampaignHoldPanel({ campaignId, onChanged }: { campaignId: string; onChanged?: () => void }) {
  const [state, reload] = useResource(fetchCampaignBlocks, [campaignId]);
  if (state.kind !== "ready") {
    return (
      <Panel title="Bloqueo de seguridad">
        <p className="px-3 py-2 text-xs text-ink-faint">{state.kind === "error" ? "No se pudo leer el estado de bloqueo." : "Cargando…"}</p>
      </Panel>
    );
  }
  const h: CampaignHoldsResponse = state.data;
  const mine = h.by_campaign[campaignId];
  const block = mine?.block ?? null;
  const global = h.all_campaigns.block;
  const done = () => {
    reload();
    onChanged?.();
  };
  return (
    <Panel title="Bloqueo de seguridad" bodyClassName="space-y-2 px-3 py-2.5">
      <div data-testid="campaign-hold" data-held={mine?.held ? "true" : "false"}>
        {mine?.held ? (
          <div className="flex flex-wrap gap-1">
            {mine.refusals.map((r) => (
              <Badge key={r.code} tone="bad">
                {r.label}
              </Badge>
            ))}
          </div>
        ) : (
          <p className="text-xs text-ink-muted">Sin bloqueo. Esta campaña sigue sin ruta de envío.</p>
        )}
      </div>
      {block ? <BlockLine b={block} /> : null}
      {global ? (
        <div className="rounded-md border border-bad/30 px-2 py-1.5">
          <BlockLine b={global} />
        </div>
      ) : null}
      <p className="text-[11px] text-ink-faint">{EFFECT_SHORT}</p>
      {h.may_decide ? (
        block ? (
          <LiftForm b={block} onDone={done} onReload={reload} />
        ) : (
          <DecisionForm
            onReload={reload}
            label="Bloquear campaña"
            testId="block-campaign"
            submitLabel="Bloquear campaña"
            confirmText="Bloquear no envía ni encola nada y no modifica la audiencia congelada. Queda activo hasta que un administrador lo levante con otro motivo."
            onSubmit={async (reason, key) => {
              await blockCampaign(
                { scope: "campaign", campaign_id: campaignId, expected_block_version: mine?.block_version ?? 0, reason },
                key,
              );
              done();
            }}
          />
        )
      ) : (
        <p className="text-[11px] text-ink-faint" data-testid="hold-read-only">
          {h.commands_enabled
            ? "Sólo un administrador bloquea o levanta un bloqueo."
            : "Bloquear y levantar bloqueos no está habilitado en este entorno."}
        </p>
      )}
    </Panel>
  );
}
