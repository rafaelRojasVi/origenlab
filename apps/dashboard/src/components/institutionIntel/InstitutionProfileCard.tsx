import { useEffect, useState } from "react";
import type { EquipmentHistoryEntry, InstitutionProfile, InstitutionTenderRow } from "../../api/institutionIntel/types";
import { institutionIntelAdapter } from "../../api/institutionIntel/adapter";
import {
  CONTACT_GAP_STATUS_LABEL,
  DEMAND_RECURRENCE_LABEL,
  IDENTITY_KIND_LABEL,
  formatEquipmentCategory,
  formatNextAction,
} from "../../lib/institutionIntelLabels";
import type { ContactGapStatus } from "../../api/institutionIntel/types";
import { AvailabilityBlock } from "./AvailabilityBlock";
import { AxisScoreCard } from "./AxisScoreCard";
import { EvidenceDisclosure } from "./EvidenceDisclosure";
import { EligibilityBadge, QueueBadge } from "./IntelBadges";

function EquipmentHistoryCard({ entry }: { entry: EquipmentHistoryEntry }) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3.5 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-semibold text-slate-900">{formatEquipmentCategory(entry.category)}</span>
        <div className="flex flex-wrap gap-1.5">
          <span
            className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
              entry.demandRecurrence === "repeated_observed_demand_confirmed"
                ? "bg-emerald-50 text-emerald-700"
                : "bg-slate-100 text-slate-600"
            }`}
          >
            {DEMAND_RECURRENCE_LABEL[entry.demandRecurrence]} · {entry.distinctTenderCount} licitaciones
          </span>
          {entry.fromAnexoEvidence ? (
            <span className="rounded-full border border-brand-100 bg-brand-50 px-2 py-0.5 text-[10px] font-semibold text-brand-700">
              Detectado por anexo
            </span>
          ) : null}
        </div>
      </div>
      <p className="mt-1 text-xs text-[var(--color-muted)]">
        <span className="font-medium text-slate-700">Primera vez:</span>{" "}
        {entry.firstObservedDate ?? "—"} &nbsp;·&nbsp;{" "}
        <span className="font-medium text-slate-700">Más reciente:</span>{" "}
        {entry.mostRecentObservedDate ?? "—"}
      </p>
      {entry.snippets.length > 0 ? (
        <EvidenceDisclosure
          evidence={entry.snippets[0]}
          label={`Ver texto real de licitaciones (${entry.snippets.length})`}
          defaultOpen={entry.fromAnexoEvidence}
        />
      ) : null}
    </div>
  );
}

function TenderRowLine({ row }: { row: InstitutionTenderRow }) {
  const isOpen = row.lifecycleClass === "active_open";
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-[var(--color-border)] px-3 py-2 text-sm">
      <div className="min-w-0">
        <span
          className={`mr-2 rounded px-1.5 py-0.5 text-[10px] font-bold ${
            isOpen ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-500"
          }`}
        >
          {isOpen ? "Abierta" : "Cerrada"}
        </span>
        <span className="text-slate-800">{formatEquipmentCategory(row.categoryOrTitle)}</span>
        <div className="mt-1 flex items-center gap-2">
          <p className="font-mono text-[11px] text-[var(--color-muted)]">{row.tenderCode}</p>
          {isOpen ? <EligibilityBadge status={row.eligibilityStatus} /> : null}
        </div>
      </div>
      <span className="shrink-0 text-xs text-[var(--color-muted)]">
        {row.closeTimestamp ? new Date(row.closeTimestamp).toLocaleDateString("es-CL") : "—"}
      </span>
    </div>
  );
}

export function InstitutionProfileCard({ institutionId }: { institutionId: string }) {
  const [profile, setProfile] = useState<InstitutionProfile | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void institutionIntelAdapter.getInstitutionProfile(institutionId).then((data) => {
      if (!cancelled) {
        setProfile(data);
        setLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [institutionId]);

  if (loading) {
    return (
      <div className="space-y-3" role="status" aria-live="polite">
        <div className="h-20 animate-pulse rounded-lg bg-slate-100" />
        <div className="h-32 animate-pulse rounded-lg bg-slate-100" />
      </div>
    );
  }

  if (!profile) {
    return (
      <p className="rounded-lg border border-[var(--color-border)] bg-slate-50/80 px-4 py-3 text-sm text-[var(--color-muted)]">
        Institución no encontrada.
      </p>
    );
  }

  const { identity, axes, contactOverlay } = profile;

  return (
    <div
      className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-sm"
      data-testid="institution-profile-card"
    >
      <div className="border-b border-[var(--color-border)] px-5 py-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-brand-600">
          Institución · solo lectura
        </p>
        <h2 className="mt-0.5 text-lg font-semibold text-slate-900">{identity.displayName}</h2>
        <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-[var(--color-muted)]">
          <span className={identity.linkedAccountPresent ? "" : "font-medium text-amber-700"}>
            {identity.linkedAccountPresent ? "Cuenta OrigenLab vinculada" : "Cuenta OrigenLab no vinculada"}
          </span>
          <span>·</span>
          <span>Identidad: {IDENTITY_KIND_LABEL[identity.identityKind]}</span>
        </p>
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          {profile.queueMembership.map((queue) => (
            <QueueBadge key={queue} queue={queue} />
          ))}
        </div>
      </div>

      <div className="space-y-5 px-5 py-4">
        <section>
          <h3 className="mb-2 text-sm font-semibold text-slate-900">Fuerza, urgencia y contacto</h3>
          <p className="mb-2 text-xs text-[var(--color-muted)]">
            Tres ejes independientes — a propósito no se combinan en un solo puntaje.
          </p>
          <AxisScoreCard axes={axes} />
        </section>

        <section>
          <h3 className="mb-2 text-sm font-semibold text-slate-900">Historial de equipos</h3>
          <AvailabilityBlock
            availed={profile.equipmentHistory}
            emptyLabel="Sin historial de compra de equipos detectado todavía."
            render={(entries) => (
              <div className="space-y-2">
                {entries.map((entry) => (
                  <EquipmentHistoryCard key={entry.category} entry={entry} />
                ))}
              </div>
            )}
          />
        </section>

        <section>
          <h3 className="mb-2 text-sm font-semibold text-slate-900">Señal de mercado</h3>
          <AvailabilityBlock
            availed={profile.marketSignal}
            notAvailableLabel="Señal de mercado no disponible para esta categoría todavía."
            render={(signal) => (
              <div className="rounded-lg border border-brand-100 bg-brand-50 px-3.5 py-3 text-sm text-brand-900">
                <span className="text-lg font-bold">{signal.institutionCount}</span> instituciones han
                solicitado <b>{formatEquipmentCategory(signal.category)}</b> en licitaciones —{" "}
                {signal.repeatInstitutionCount} más de una vez.
                <p className="mt-1.5 text-[11px] text-brand-800/80">
                  Cuenta por categoría de equipo a nivel nacional — no hay datos de región o sector todavía.
                </p>
              </div>
            )}
          />
        </section>

        <section>
          <h3 className="mb-2 text-sm font-semibold text-slate-900">Licitaciones — actuales e históricas</h3>
          <div className="space-y-1.5">
            {profile.currentOpportunities.map((row) => (
              <TenderRowLine key={row.tenderCode} row={row} />
            ))}
            {profile.historicalSignals.map((row) => (
              <TenderRowLine key={row.tenderCode} row={row} />
            ))}
            {profile.currentOpportunities.length === 0 && profile.historicalSignals.length === 0 ? (
              <p className="text-xs text-[var(--color-muted)]">Sin licitaciones registradas.</p>
            ) : null}
          </div>
        </section>

        <section>
          <h3 className="mb-2 text-sm font-semibold text-slate-900">Contacto</h3>
          <div className="grid grid-cols-3 gap-2">
            <div className="rounded-md border border-[var(--color-border)] px-2 py-2 text-center">
              <p className="text-lg font-bold text-slate-900">{contactOverlay.knownContactCount}</p>
              <p className="text-[10px] text-[var(--color-muted)]">conocidos</p>
            </div>
            <div className="rounded-md border border-[var(--color-border)] px-2 py-2 text-center">
              <p className="text-lg font-bold text-slate-900">{contactOverlay.suitableContactCount}</p>
              <p className="text-[10px] text-[var(--color-muted)]">aptos</p>
            </div>
            <div className="rounded-md border border-[var(--color-border)] px-2 py-2 text-center">
              <p className="text-lg font-bold text-slate-900">{contactOverlay.verifiedContactCount}</p>
              <p className="text-[10px] text-[var(--color-muted)]">verificados</p>
            </div>
          </div>
          <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-950">
            <p className="font-medium">Acción recomendada: {formatNextAction(contactOverlay.nextAction)}</p>
            <p className="mt-0.5 text-amber-900">
              Estado: {CONTACT_GAP_STATUS_LABEL[contactOverlay.contactGapStatus]}
            </p>
          </div>

          {profile.mockOnlyContacts && profile.mockOnlyContacts.length > 0 ? (
            <div className="mt-3 space-y-1.5" data-testid="mock-only-contacts">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                Vista previa — solo mock, no un join real
              </p>
              {profile.mockOnlyContacts.map((contact) => (
                <div
                  key={contact.email}
                  className="rounded-md border border-dashed border-[var(--color-border)] px-3 py-2 text-xs"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium text-slate-800">
                      {contact.name ?? "Contacto sin nombre confirmado"}
                    </span>
                    <span className="text-[10px] text-[var(--color-muted)]">
                      {CONTACT_GAP_STATUS_LABEL[contact.roleStatus as ContactGapStatus] ?? contact.roleStatus}
                    </span>
                  </div>
                  <p className="mt-0.5 font-mono text-[11px] text-brand-700">{contact.email}</p>
                  <p className="mt-0.5 text-[var(--color-muted)]">
                    {contact.gmailHistory
                      ? `${contact.gmailHistory.sentCount} correo(s) enviado(s) · hace ${contact.gmailHistory.lastSentDaysAgo} días`
                      : "Sin correos previos con este contacto"}
                  </p>
                </div>
              ))}
              <p className="text-[10px] text-[var(--color-muted)]">
                Institución↔contacto no se resuelve en el frontend (sin coincidencia difusa de dominio/nombre) —
                pendiente de que W1 confirme si existe un join canónico exacto.
              </p>
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}
