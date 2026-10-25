import { useEffect } from "react";

/** Shown when the dashboard is opened on a non-allowed hostname (e.g. raw Render URL). */
export function PrivateDashboardPlaceholder() {
  useEffect(() => {
    document.title = "Private dashboard";
  }, []);

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-3 p-8 text-center">
      <h1 className="text-xl font-semibold text-ink">Private dashboard</h1>
      <p className="max-w-md text-sm text-ink-muted">
        Use the protected OrigenLab dashboard domain.
      </p>
    </main>
  );
}
