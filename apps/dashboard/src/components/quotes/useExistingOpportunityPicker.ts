import { useEffect, useMemo, useState } from "react";
import { fetchSalesOpportunities } from "../../api/commercialOperationsClient";
import type { SalesOpportunityListItem } from "../../api/commercialOperationsTypes";

const PICKER_FETCH_LIMIT = 200;

/**
 * `active` gates the fetch (typically the host dialog's `open` prop) so a
 * dialog that stays mounted with `open` toggling re-fetches a fresh
 * opportunity list every time it's reopened, instead of fetching once ever
 * and going stale.
 */
export function useExistingOpportunityPicker(active: boolean) {
  const [items, setItems] = useState<SalesOpportunityListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchText, setSearchText] = useState("");

  useEffect(() => {
    if (!active) return;

    let cancelled = false;
    setLoading(true);
    setError(null);

    void fetchSalesOpportunities({ limit: PICKER_FETCH_LIMIT, offset: 0 })
      .then((result) => {
        if (cancelled) return;
        setItems(result.items);
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : "No pudimos cargar las oportunidades.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [active]);

  const visibleItems = useMemo(() => {
    const search = searchText.trim().toLowerCase();
    if (!search) return items;
    return items.filter((item) =>
      [item.title, item.organization_display_name, item.contact_display_name, item.contact_primary_email]
        .filter((value): value is string => Boolean(value))
        .join(" ")
        .toLowerCase()
        .includes(search),
    );
  }, [items, searchText]);

  return { items, loading, error, searchText, setSearchText, visibleItems };
}
