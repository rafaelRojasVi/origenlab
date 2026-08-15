/**
 * Mock fixture data — PLACEHOLDER, backs institutionIntelAdapter.ts until W1 lands.
 * Continues the same example institutions used in the concept mockups
 * (Hospital Regional de Talca et al.) so the story stays consistent.
 */

import type {
  InstitutionListItem,
  InstitutionProfile,
  LicitacionIntel,
  ProspectQueueRow,
} from "./types";

export const MOCK_TALCA: InstitutionProfile = {
  identity: {
    institutionId: "inst-talca-01",
    displayName: "Hospital Regional de Talca",
    identityKind: "chilecompra_buyer_source_id",
    linkedAccountPresent: false,
    identityReviewRequired: false,
  },
  axes: {
    prospectStrength: {
      band: "high",
      score: 8,
      reasonCodes: ["multiple_purchase_tenders", "repeated_category_demand"],
    },
    opportunityUrgency: {
      band: "high",
      score: 7,
      reasonCodes: ["open_tender_present", "closing_soon"],
    },
    contactReadiness: {
      band: "low",
      score: 1,
      reasonCodes: ["account_unlinked", "zero_verified_contacts"],
    },
  },
  equipmentHistory: {
    status: "available",
    data: [
      {
        category: "centrifuge",
        distinctTenderCount: 3,
        firstObservedDate: "2025-11-04",
        mostRecentObservedDate: "2026-08-14",
        demandRecurrence: "repeated_observed_demand_confirmed",
        openCurrentTenderCount: 1,
        historicalTenderCount: 2,
        fromAnexoEvidence: false,
        snippets: [
          {
            excerpt: "Centrífuga refrigerada de alta velocidad con rotor incluido para laboratorio clínico",
            documentLabel: "1057898-51-LP26",
            locator: "actual",
          },
          {
            excerpt: "Adquisición de centrífuga de mesa para banco de sangre, 24 tubos",
            documentLabel: "0923456-08-LP26",
            locator: "mar 2026, cerrada",
          },
        ],
      },
      {
        category: "analytical_balance",
        distinctTenderCount: 1,
        firstObservedDate: "2026-08-14",
        mostRecentObservedDate: "2026-08-14",
        demandRecurrence: "observed_once_confirmed",
        openCurrentTenderCount: 1,
        historicalTenderCount: 0,
        fromAnexoEvidence: true,
        snippets: [
          {
            excerpt:
              "...además se requiere balanza analítica de precisión ≥0,1 mg según Anexo Técnico N.º 3...",
            documentLabel: "1057898-51-LP26",
            locator: "ANEXO_TECNICO_3.pdf, página 7",
          },
        ],
      },
    ],
  },
  currentOpportunities: [
    {
      tenderCode: "1057898-51-LP26",
      categoryOrTitle: "Centrífuga refrigerada",
      lifecycleClass: "active_open",
      closeTimestamp: "2026-08-18T15:00:00-04:00",
      eligibilityStatus: "open_public",
    },
  ],
  historicalSignals: [
    {
      tenderCode: "0923456-08-LP26",
      categoryOrTitle: "Centrífuga de mesa",
      lifecycleClass: "closed",
      closeTimestamp: "2026-03-10T15:00:00-04:00",
      eligibilityStatus: "open_public",
    },
    {
      tenderCode: "0847231-15-LP25",
      categoryOrTitle: "Centrífuga refrigerada",
      lifecycleClass: "closed",
      closeTimestamp: "2025-11-20T15:00:00-04:00",
      eligibilityStatus: "open_public",
    },
  ],
  contactOverlay: {
    contactGapStatus: "account_unlinked",
    knownContactCount: 2,
    suitableContactCount: 1,
    verifiedContactCount: 0,
    nextAction: "research_new_contact",
  },
  queueMembership: ["current_opportunity_queue", "historical_prospect_queue", "contact_gap_queue"],
  marketSignal: {
    status: "available",
    data: { category: "centrifuge", institutionCount: 23, repeatInstitutionCount: 6 },
  },
  mockOnlyContacts: [
    {
      name: null,
      email: "contacto@hospitaltalca.cl",
      roleStatus: "existing_contact_needs_role_review",
      gmailHistory: null,
    },
    {
      name: "M. Soto",
      email: "msoto@hospitaltalca.cl",
      roleStatus: "role_known_email_missing",
      gmailHistory: { sentCount: 1, lastSentDaysAgo: 61, replied: false },
    },
  ],
};

export const MOCK_CONCEPCION: InstitutionListItem = {
  identity: {
    institutionId: "inst-udec-01",
    displayName: "Universidad de Concepción",
    identityKind: "chilecompra_buyer_source_id",
    linkedAccountPresent: false,
    identityReviewRequired: false,
  },
  axes: {
    prospectStrength: { band: "medium", score: 4, reasonCodes: ["single_purchase_tender"] },
    opportunityUrgency: { band: "high", score: 6, reasonCodes: ["open_tender_present", "closing_soon"] },
    contactReadiness: { band: "none", score: 0, reasonCodes: ["account_unlinked"] },
  },
  categories: ["homogenizer"],
  queueMembership: ["current_opportunity_queue"],
};

export const MOCK_COPIAPO: InstitutionListItem = {
  identity: {
    institutionId: "inst-copiapo-01",
    displayName: "Municipalidad de Copiapó",
    identityKind: "identity_review",
    linkedAccountPresent: true,
    identityReviewRequired: true,
  },
  axes: {
    prospectStrength: { band: "medium", score: 4, reasonCodes: ["single_purchase_tender"] },
    opportunityUrgency: { band: "none", score: 0, reasonCodes: [] },
    contactReadiness: { band: "high", score: 5, reasonCodes: ["linked_account", "verified_contacts"] },
  },
  categories: ["homogenizer"],
  queueMembership: ["institution_match_review_queue"],
};

export const MOCK_NUBLE: InstitutionListItem = {
  identity: {
    institutionId: "inst-nuble-01",
    displayName: "Servicio de Salud Ñuble",
    identityKind: "normalized_buyer_name",
    linkedAccountPresent: false,
    identityReviewRequired: false,
  },
  axes: {
    prospectStrength: { band: "low", score: 2, reasonCodes: ["single_purchase_tender"] },
    opportunityUrgency: { band: "none", score: 0, reasonCodes: [] },
    contactReadiness: { band: "none", score: 0, reasonCodes: ["account_unlinked"] },
  },
  categories: ["analytical_balance"],
  queueMembership: ["historical_prospect_queue"],
};

export const MOCK_INSTITUTION_LIST: readonly InstitutionListItem[] = [
  {
    identity: MOCK_TALCA.identity,
    axes: MOCK_TALCA.axes,
    categories: ["centrifuge", "analytical_balance"],
    queueMembership: MOCK_TALCA.queueMembership,
  },
  MOCK_CONCEPCION,
  MOCK_NUBLE,
];

export const MOCK_LICITACION_TALCA: LicitacionIntel = {
  tenderCode: "1057898-51-LP26",
  buyerDisplayName: "Hospital Regional de Talca",
  eligibilityStatus: "open_public",
  procurementMethodRaw: "LP",
  totalBudgetReconciled: true,
  terms: {
    status: "available",
    data: [
      {
        fieldName: "total_budget",
        label: "Presupuesto",
        state: "explicit",
        value: 8_500_000,
        unit: "CLP",
        evidence: {
          excerpt: "Presupuesto Disponible: $ 8.500.000.- impuestos incluidos",
          documentLabel: "BASES_ADMINISTRATIVAS.pdf",
          locator: "página 3",
        },
      },
      {
        fieldName: "offer_validity_days",
        label: "Validez de oferta",
        state: "explicit",
        value: 30,
        unit: "días",
        evidence: {
          excerpt: "Las ofertas tendrán una validez de 30 días corridos",
          documentLabel: "BASES_ADMINISTRATIVAS.pdf",
          locator: "página 4",
        },
      },
      {
        fieldName: "contract_duration_months",
        label: "Duración contrato",
        state: "derived",
        value: 12,
        unit: "meses",
        evidence: {
          excerpt: "Calculado desde fecha de adjudicación estimada + vigencia de garantía técnica declarada.",
          documentLabel: "ANEXO_TECNICO_1.pdf",
          locator: "página 2",
        },
      },
      {
        fieldName: "payment_deadline_days",
        label: "Plazo de pago",
        state: "explicit",
        value: 30,
        unit: "días",
        evidence: {
          excerpt: "Pago máximo 30 días corridos desde recepción conforme",
          documentLabel: "BASES_ADMINISTRATIVAS.pdf",
          locator: "página 6",
        },
      },
      {
        fieldName: "maximum_delivery_days",
        label: "Plazo de entrega",
        state: "explicit",
        value: 45,
        unit: "días hábiles",
        evidence: {
          excerpt: "Plazo máximo de entrega: 45 días hábiles desde orden de compra",
          documentLabel: "ANEXO_TECNICO_1.pdf",
          locator: "página 1",
        },
      },
      {
        fieldName: "faithful_performance_guarantee_percent",
        label: "Garantía fiel cumplimiento",
        state: "explicit",
        value: 5,
        unit: "%",
        evidence: {
          excerpt: "Garantía de Fiel Cumplimiento de Contrato equivalente al 5% del monto neto adjudicado",
          documentLabel: "BASES_ADMINISTRATIVAS.pdf",
          locator: "página 7",
        },
      },
    ],
  },
  itemBudget: {
    status: "available",
    data: [
      {
        itemLabel: "Centrífuga refrigerada",
        quantity: 1,
        amount: 6_200_000,
        currency: "CLP",
        evidence: {
          excerpt: "3.1 Equipos solicitados — Ítem 1: Centrífuga refrigerada · Cantidad: 1 · Presupuesto estimado: $6.200.000",
          documentLabel: "ANEXO_TECNICO_1.pdf",
          locator: "tabla, página 2",
        },
      },
      {
        itemLabel: "Balanza analítica",
        quantity: 1,
        amount: 2_300_000,
        currency: "CLP",
        evidence: {
          excerpt: "3.1 Equipos solicitados — Ítem 2: Balanza analítica · Cantidad: 1 · Presupuesto estimado: $2.300.000",
          documentLabel: "ANEXO_TECNICO_3.pdf",
          locator: "tabla, página 7",
        },
      },
      {
        itemLabel: "Kit de reactivos asociado",
        quantity: 3,
        amount: null,
        evidence: undefined,
      },
    ],
  },
  recognitionDelta: {
    status: "available",
    data: {
      changeClass: "annex_strengthened_existing",
      baselineCategories: ["centrifuge"],
      augmentedCategories: ["centrifuge", "analytical_balance"],
      addedCategories: ["analytical_balance"],
      confidenceBand: "high",
      evidence: {
        excerpt:
          "...además se requiere balanza analítica de precisión ≥0,1 mg según especificaciones del Anexo Técnico N.º 3, no incluida en el resumen de la ficha de Mercado Público...",
        documentLabel: "ANEXO_TECNICO_3.pdf",
        locator: "página 7",
      },
    },
  },
  coverage: {
    status: "available",
    data: { documentsDiscovered: 4, documentsRead: 4, incompleteReasonCodes: [] },
  },
};

export const MOCK_QUEUE_ROWS: readonly ProspectQueueRow[] = [
  {
    queue: "current_opportunity_queue",
    rowId: "cur-1",
    institutionId: "inst-talca-01",
    institutionDisplayName: "Hospital Regional de Talca",
    tenderCode: "1057898-51-LP26",
    categoryOrTitle: "Centrífuga refrigerada",
    closeTimestamp: "2026-08-18T15:00:00-04:00",
    eligibilityStatus: "open_public",
    prospectStrengthBand: "high",
  },
  {
    queue: "current_opportunity_queue",
    rowId: "cur-2",
    institutionId: "inst-udec-01",
    institutionDisplayName: "Universidad de Concepción",
    tenderCode: "2201-33-LP26",
    categoryOrTitle: "Homogeneizador de tejidos",
    closeTimestamp: "2026-08-20T15:00:00-04:00",
    eligibilityStatus: "open_public",
    prospectStrengthBand: "medium",
  },
  {
    queue: "contact_gap_queue",
    rowId: "gap-1",
    institutionId: "inst-talca-01",
    institutionDisplayName: "Hospital Regional de Talca",
    contactGapStatus: "account_unlinked",
    prospectStrengthBand: "high",
    opportunityUrgencyBand: "high",
    queueEntryReason: "current_catalog_opportunity",
  },
  {
    queue: "historical_prospect_queue",
    rowId: "hist-1",
    institutionId: "inst-nuble-01",
    institutionDisplayName: "Servicio de Salud Ñuble",
    category: "analytical_balance",
    commercialSignalType: "equipment_purchase_signal",
    mostRecentObservedDate: "2026-03-15",
    tenderCount: 1,
  },
  {
    queue: "institution_match_review_queue",
    rowId: "review-1",
    institutionId: "inst-copiapo-01",
    institutionDisplayName: "Municipalidad de Copiapó",
    conflictReason: "Dos IDs de comprador ChileCompra distintos apuntan al mismo nombre normalizado",
    candidateDisplayNames: ["Municipalidad de Copiapó", "I. Municipalidad de Copiapó"],
  },
  {
    queue: "line_evidence_review_queue",
    rowId: "evidence-1",
    institutionId: "inst-biobio-01",
    institutionDisplayName: "Clínica Bío Bío",
    tenderCode: "3312-7-LE26",
    clauseText: "mantención de centrífugas existentes y adquisición de un procesador ultrasónico nuevo",
  },
  {
    queue: "retender_review_queue",
    rowId: "retender-1",
    institutionId: "inst-valdivia-01",
    institutionDisplayName: "Hospital Base de Valdivia",
    tenderCodes: ["4410-2-LP26", "4410-1-LP25"],
    resolutionReason: "Podría ser una reedición de una licitación declarada desierta — no confirmado",
  },
];
