import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { WarmCasesResponse } from "../api/commercialTypes";
import {
  DASHBOARD_WARM_CASES_QUERY,
  fetchTodayPanel,
  fetchWarmCases,
} from "../api/operatorClient";
import type { TodayPanelData } from "../api/operatorTypes";
import type { CatalogProductsListUi } from "../api/catalogTypes";
import type { LeadResearchSummaryUi } from "../api/leadIntelTypes";
import type { CommercialDealsListUi } from "../api/commercialDealsTypes";
import type { CommercialWorkQueueResponse } from "../api/commercialOperationsTypes";
import type { ProcurementStatus } from "../api/institutionIntel/types";
import { institutionIntelAdapter } from "../api/institutionIntel/adapter";
import { fetchCommercialWorkQueue } from "../api/commercialOperationsClient";
import { fetchCatalogProductsMirror } from "../api/mirrorCatalogClient";
import { fetchLeadResearchSummaryMirror } from "../api/mirrorLeadIntelClient";
import { fetchCommercialDealsMirror } from "../api/mirrorCommercialClient";
import {
  getLegacyDevPortWarning,
  logLegacyDevPortWarningIfNeeded,
} from "../lib/devApiConfig";

import { formatMirrorLoadError } from "../lib/humanizeApiError";

function formatLoadError(label: string, e: unknown): string {
  return formatMirrorLoadError(label, e).message;
}

export interface DashboardDataState {
  data: TodayPanelData | null;
  panelLoading: boolean;
  panelError: string | null;
  warm: WarmCasesResponse | null;
  warmLoading: boolean;
  warmError: string | null;
  procurementStatus: ProcurementStatus | null;
  procurementStatusLoading: boolean;
  procurementStatusError: string | null;
  commercialDeals: CommercialDealsListUi | null;
  commercialDealsLoading: boolean;
  commercialDealsError: string | null;
  commercialDealsErrorDetail: string | null;
  catalogProducts: CatalogProductsListUi | null;
  catalogProductsLoading: boolean;
  catalogProductsError: string | null;
  leadResearchSummary: LeadResearchSummaryUi | null;
  leadResearchSummaryLoading: boolean;
  leadResearchSummaryError: string | null;
  commercialWorkQueue: CommercialWorkQueueResponse | null;
  commercialWorkQueueLoading: boolean;
  commercialWorkQueueError: string | null;
  contactEmail: string | null;
  setContactEmail: (email: string | null) => void;
  loadAll: () => void;
  loadPanel: () => Promise<void>;
  loadWarm: () => Promise<void>;
  loadProcurementStatus: () => Promise<void>;
  loadCommercialDeals: () => Promise<void>;
  loadCatalogProducts: () => Promise<void>;
  loadLeadResearchSummary: () => Promise<void>;
  loadCommercialWorkQueue: () => Promise<void>;
  refreshing: boolean;
  mirrorBackend: boolean;
  backend: TodayPanelData["health"]["backend"] | "sqlite";
  devConfigWarning: string | null;
}

/** @internal Test stubs may wrap pages with a fixed provider value. */
export const DashboardDataContext = createContext<DashboardDataState | null>(null);

export function DashboardDataProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<TodayPanelData | null>(null);
  const [panelLoading, setPanelLoading] = useState(true);
  const [panelError, setPanelError] = useState<string | null>(null);

  const [warm, setWarm] = useState<WarmCasesResponse | null>(null);
  const [warmLoading, setWarmLoading] = useState(true);
  const [warmError, setWarmError] = useState<string | null>(null);

  const [procurementStatus, setProcurementStatus] = useState<ProcurementStatus | null>(null);
  const [procurementStatusLoading, setProcurementStatusLoading] = useState(true);
  const [procurementStatusError, setProcurementStatusError] = useState<string | null>(null);

  const [commercialDeals, setCommercialDeals] = useState<CommercialDealsListUi | null>(null);
  const [commercialDealsLoading, setCommercialDealsLoading] = useState(true);
  const [commercialDealsError, setCommercialDealsError] = useState<string | null>(null);
  const [commercialDealsErrorDetail, setCommercialDealsErrorDetail] = useState<string | null>(null);

  const [catalogProducts, setCatalogProducts] = useState<CatalogProductsListUi | null>(null);
  const [catalogProductsLoading, setCatalogProductsLoading] = useState(true);
  const [catalogProductsError, setCatalogProductsError] = useState<string | null>(null);

  const [leadResearchSummary, setLeadResearchSummary] = useState<LeadResearchSummaryUi | null>(null);
  const [leadResearchSummaryLoading, setLeadResearchSummaryLoading] = useState(true);
  const [leadResearchSummaryError, setLeadResearchSummaryError] = useState<string | null>(null);

  const [commercialWorkQueue, setCommercialWorkQueue] =
    useState<CommercialWorkQueueResponse | null>(null);
  const [commercialWorkQueueLoading, setCommercialWorkQueueLoading] =
    useState(true);
  const [commercialWorkQueueError, setCommercialWorkQueueError] =
    useState<string | null>(null);

  const [contactEmail, setContactEmail] = useState<string | null>(null);

  const loadPanel = useCallback(async () => {
    setPanelLoading(true);
    setPanelError(null);
    try {
      setData(await fetchTodayPanel());
    } catch (e) {
      setPanelError(formatLoadError("Operator status", e));
      setData(null);
    } finally {
      setPanelLoading(false);
    }
  }, []);

  const loadWarm = useCallback(async () => {
    setWarmLoading(true);
    setWarmError(null);
    try {
      setWarm(await fetchWarmCases(DASHBOARD_WARM_CASES_QUERY));
    } catch (e) {
      setWarmError(formatLoadError("Warm cases", e));
      setWarm(null);
    } finally {
      setWarmLoading(false);
    }
  }, []);

  const loadProcurementStatus = useCallback(async () => {
    setProcurementStatusLoading(true);
    setProcurementStatusError(null);
    try {
      setProcurementStatus(await institutionIntelAdapter.getProcurementStatus());
    } catch (e) {
      setProcurementStatusError(formatLoadError("Procurement status", e));
      setProcurementStatus(null);
    } finally {
      setProcurementStatusLoading(false);
    }
  }, []);

  const loadCommercialDeals = useCallback(async () => {
    setCommercialDealsLoading(true);
    setCommercialDealsError(null);
    setCommercialDealsErrorDetail(null);
    try {
      setCommercialDeals(await fetchCommercialDealsMirror());
    } catch (e) {
      const formatted = formatMirrorLoadError("Negocios comerciales", e);
      setCommercialDealsError(formatted.message);
      setCommercialDealsErrorDetail(formatted.detail);
      setCommercialDeals(null);
    } finally {
      setCommercialDealsLoading(false);
    }
  }, []);

  const loadCatalogProducts = useCallback(async () => {
    setCatalogProductsLoading(true);
    setCatalogProductsError(null);
    try {
      setCatalogProducts(await fetchCatalogProductsMirror({ limit: 100 }));
    } catch (e) {
      setCatalogProductsError(formatLoadError("Catálogo", e));
      setCatalogProducts(null);
    } finally {
      setCatalogProductsLoading(false);
    }
  }, []);

  const loadLeadResearchSummary = useCallback(async () => {
    setLeadResearchSummaryLoading(true);
    setLeadResearchSummaryError(null);
    try {
      setLeadResearchSummary(await fetchLeadResearchSummaryMirror());
    } catch (e) {
      setLeadResearchSummaryError(formatLoadError("Prospectos", e));
      setLeadResearchSummary(null);
    } finally {
      setLeadResearchSummaryLoading(false);
    }
  }, []);

  const loadCommercialWorkQueue = useCallback(async () => {
    setCommercialWorkQueueLoading(true);
    setCommercialWorkQueueError(null);

    try {
      setCommercialWorkQueue(
        await fetchCommercialWorkQueue(100),
      );
    } catch (e) {
      setCommercialWorkQueueError(
        formatLoadError("Trabajo comercial", e),
      );
      setCommercialWorkQueue(null);
    } finally {
      setCommercialWorkQueueLoading(false);
    }
  }, []);

  const loadAll = useCallback(() => {
    void Promise.all([
      loadPanel(),
      loadWarm(),
      loadProcurementStatus(),
      loadCommercialDeals(),
      loadCatalogProducts(),
      loadLeadResearchSummary(),
      loadCommercialWorkQueue(),
    ]);
  }, [
    loadPanel,
    loadWarm,
    loadProcurementStatus,
    loadCommercialDeals,
    loadCatalogProducts,
    loadLeadResearchSummary,
    loadCommercialWorkQueue,
  ]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const devConfigWarning = useMemo(() => getLegacyDevPortWarning(), []);

  useEffect(() => {
    logLegacyDevPortWarningIfNeeded();
  }, [devConfigWarning]);

  const mirrorBackend = data?.health.backend === "postgres";
  const backend = data?.health.backend ?? "sqlite";
  const refreshing =
    panelLoading ||
    warmLoading ||
    procurementStatusLoading ||
    commercialDealsLoading ||
    catalogProductsLoading ||
    leadResearchSummaryLoading ||
    commercialWorkQueueLoading;

  const value = useMemo<DashboardDataState>(
    () => ({
      data,
      panelLoading,
      panelError,
      warm,
      warmLoading,
      warmError,
      procurementStatus,
      procurementStatusLoading,
      procurementStatusError,
      commercialDeals,
      commercialDealsLoading,
      commercialDealsError,
      commercialDealsErrorDetail,
      catalogProducts,
      catalogProductsLoading,
      catalogProductsError,
      leadResearchSummary,
      leadResearchSummaryLoading,
      leadResearchSummaryError,
      commercialWorkQueue,
      commercialWorkQueueLoading,
      commercialWorkQueueError,
      contactEmail,
      setContactEmail,
      loadAll,
      loadPanel,
      loadWarm,
      loadProcurementStatus,
      loadCommercialDeals,
      loadCatalogProducts,
      loadLeadResearchSummary,
      loadCommercialWorkQueue,
      refreshing,
      mirrorBackend,
      backend,
      devConfigWarning,
    }),
    [
      data,
      panelLoading,
      panelError,
      warm,
      warmLoading,
      warmError,
      procurementStatus,
      procurementStatusLoading,
      procurementStatusError,
      commercialDeals,
      commercialDealsLoading,
      commercialDealsError,
      commercialDealsErrorDetail,
      catalogProducts,
      catalogProductsLoading,
      catalogProductsError,
      leadResearchSummary,
      leadResearchSummaryLoading,
      leadResearchSummaryError,
      commercialWorkQueue,
      commercialWorkQueueLoading,
      commercialWorkQueueError,
      contactEmail,
      loadAll,
      loadPanel,
      loadWarm,
      loadProcurementStatus,
      loadCommercialDeals,
      loadCatalogProducts,
      loadLeadResearchSummary,
      loadCommercialWorkQueue,
      refreshing,
      mirrorBackend,
      backend,
      devConfigWarning,
    ],
  );

  return <DashboardDataContext.Provider value={value}>{children}</DashboardDataContext.Provider>;
}

export function useDashboardData(): DashboardDataState {
  const ctx = useContext(DashboardDataContext);
  if (!ctx) {
    throw new Error("useDashboardData must be used within DashboardDataProvider");
  }
  return ctx;
}
