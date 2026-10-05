import "@testing-library/jest-dom";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { MailSyncStatus } from "./crmTypes";
import { MailSyncBanner, mailSyncNotice } from "./MailSyncBanner";

const BASE: MailSyncStatus = {
  state: "ok",
  authorization_state: "authorized",
  last_synced_at: "2026-10-12T13:05:00Z",
  minutes_since_sync: 3,
  late_after_minutes: 30,
};
const NOW = new Date("2026-10-12T15:00:00Z");

function respond(body: unknown, status = 200) {
  const fetchMock = vi.fn(() =>
    Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("mailSyncNotice", () => {
  it("says nothing while the capture runs, before go-live, or where it is not configured", () => {
    for (const state of ["ok", "not_started", "not_configured"] as const) {
      expect(mailSyncNotice({ ...BASE, state }, NOW)).toBeNull();
    }
  });

  it("says since when it stopped, in Santiago time", () => {
    expect(mailSyncNotice({ ...BASE, state: "stopped", authorization_state: "revoked" }, NOW)).toEqual({
      tone: "bad",
      text: "Sincronización de correo detenida desde 10:05",
    });
  });

  it("says it is late, with the date when the last run was another day", () => {
    const notice = mailSyncNotice({ ...BASE, state: "late", last_synced_at: "2026-10-11T13:05:00Z" }, NOW);
    expect(notice?.tone).toBe("warn");
    expect(notice?.text).toMatch(/^Sincronización de correo atrasada \(última: .*2026 10:05\)$/);
  });
});

describe("MailSyncBanner", () => {
  it("shows a stopped capture", async () => {
    respond({ ...BASE, state: "stopped", authorization_state: "revoked" });
    render(<MailSyncBanner refreshKey="resumen" />);
    expect(await screen.findByTestId("mail-sync-banner")).toHaveTextContent(/Sincronización de correo detenida desde/);
  });

  it("renders nothing when the capture runs or the read is not deployed yet", async () => {
    const ok = respond(BASE);
    const { unmount } = render(<MailSyncBanner refreshKey="a" />);
    await waitFor(() => expect(ok).toHaveBeenCalled());
    expect(screen.queryByTestId("mail-sync-banner")).toBeNull();
    unmount();
    const refused = respond({ detail: "path_not_allowed" }, 403);
    render(<MailSyncBanner refreshKey="b" />);
    await waitFor(() => expect(refused).toHaveBeenCalled());
    expect(screen.queryByTestId("mail-sync-banner")).toBeNull();
  });
});
