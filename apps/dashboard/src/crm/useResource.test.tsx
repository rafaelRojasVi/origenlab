import "@testing-library/jest-dom";
import { act, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { noteWrite } from "../api/operatorClient";
import { OperatorApiError } from "../api/operatorClient";
import type { AuthSessionState } from "../api/authClient";
import { AuthSessionContext } from "../context/AuthSessionContext";
import { clearResourceCache, useResource } from "./useResource";

// Every value below is invented; the repository is public.
function signedIn(operatorId: string, role = "admin", profileId: string | null = null): AuthSessionState {
  return {
    kind: "signed_in",
    operator: { operatorId, email: "operador@ejemplo.invalid", displayName: "Operador", role },
    method: "google_session",
    profile: profileId ? { id: profileId, displayName: "Perfil", roleLabel: role } : null,
  };
}

function withSession(session: AuthSessionState, children: ReactNode) {
  return (
    <AuthSessionContext.Provider value={{ session, signOut: async () => true }}>{children}</AuthSessionContext.Provider>
  );
}

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** A loader whose next answer the test decides; stable identity, like the module-level fetchers. */
function controllableLoader() {
  let next = deferred<string>();
  const load = () => next.promise;
  return {
    load,
    get pending() {
      return next;
    },
    reset() {
      next = deferred<string>();
    },
  };
}

function Probe({ load, freshMs = 0 }: { load: () => Promise<string>; freshMs?: number }) {
  const [state, reload] = useResource(load, [], { freshMs });
  return (
    <>
      <p data-testid="probe">{state.kind === "ready" ? `ready:${state.data}` : state.kind}</p>
      <button type="button" onClick={reload}>
        reload
      </button>
    </>
  );
}

/** A loader that counts its calls and answers `v<n>` at once. */
function countingLoader() {
  const calls = { n: 0 };
  const load = () => {
    calls.n += 1;
    return Promise.resolve(`v${calls.n}`);
  };
  return { load, calls };
}

afterEach(() => {
  clearResourceCache();
  vi.useRealTimers();
});

describe("useResource page memory", () => {
  it("shows the last loaded data at once when the page is opened again, then refreshes it", async () => {
    const loader = controllableLoader();
    const first = render(withSession(signedIn("op-a"), <Probe load={loader.load} />));
    expect(screen.getByTestId("probe")).toHaveTextContent("loading");
    await act(async () => loader.pending.resolve("v1"));
    expect(screen.getByTestId("probe")).toHaveTextContent("ready:v1");
    first.unmount();

    loader.reset();
    render(withSession(signedIn("op-a"), <Probe load={loader.load} />));
    expect(screen.getByTestId("probe")).toHaveTextContent("ready:v1");
    await act(async () => loader.pending.resolve("v2"));
    expect(screen.getByTestId("probe")).toHaveTextContent("ready:v2");
  });

  it("never shows one operator's or profile's data to another", async () => {
    const loader = controllableLoader();
    const first = render(withSession(signedIn("op-a", "admin", "profile-1"), <Probe load={loader.load} />));
    await act(async () => loader.pending.resolve("admin-view"));
    first.unmount();

    loader.reset();
    const other = render(withSession(signedIn("op-a", "viewer", "profile-2"), <Probe load={loader.load} />));
    expect(screen.getByTestId("probe")).toHaveTextContent("loading");
    other.unmount();

    render(withSession(signedIn("op-b"), <Probe load={loader.load} />));
    expect(screen.getByTestId("probe")).toHaveTextContent("loading");
  });

  it("keeps nothing outside a signed-in session", async () => {
    const loader = controllableLoader();
    const first = render(<Probe load={loader.load} />);
    await act(async () => loader.pending.resolve("v1"));
    first.unmount();

    loader.reset();
    render(<Probe load={loader.load} />);
    expect(screen.getByTestId("probe")).toHaveTextContent("loading");
  });

  it("drops the remembered data when the refresh is refused", async () => {
    const loader = controllableLoader();
    const first = render(withSession(signedIn("op-a"), <Probe load={loader.load} />));
    await act(async () => loader.pending.resolve("v1"));
    first.unmount();

    loader.reset();
    const second = render(withSession(signedIn("op-a"), <Probe load={loader.load} />));
    expect(screen.getByTestId("probe")).toHaveTextContent("ready:v1");
    await act(async () => loader.pending.reject(new OperatorApiError("no autorizado", 401)));
    expect(screen.getByTestId("probe")).toHaveTextContent("permission");
    second.unmount();

    loader.reset();
    render(withSession(signedIn("op-a"), <Probe load={loader.load} />));
    expect(screen.getByTestId("probe")).toHaveTextContent("loading");
  });

  it("forgets everything on clearResourceCache", async () => {
    const loader = controllableLoader();
    const first = render(withSession(signedIn("op-a"), <Probe load={loader.load} />));
    await act(async () => loader.pending.resolve("v1"));
    first.unmount();

    clearResourceCache();
    loader.reset();
    render(withSession(signedIn("op-a"), <Probe load={loader.load} />));
    expect(screen.getByTestId("probe")).toHaveTextContent("loading");
  });
});

describe("useResource freshness window", () => {
  it("shows a recent answer again without asking the API", async () => {
    const loader = countingLoader();
    const first = render(withSession(signedIn("op-a"), <Probe load={loader.load} freshMs={30_000} />));
    await act(async () => {});
    expect(screen.getByTestId("probe")).toHaveTextContent("ready:v1");
    first.unmount();

    render(withSession(signedIn("op-a"), <Probe load={loader.load} freshMs={30_000} />));
    await act(async () => {});
    expect(screen.getByTestId("probe")).toHaveTextContent("ready:v1");
    expect(loader.calls.n).toBe(1);
  });

  it("asks again once the window has passed, showing the old answer meanwhile", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    const loader = countingLoader();
    const first = render(withSession(signedIn("op-a"), <Probe load={loader.load} freshMs={30_000} />));
    await act(async () => {});
    first.unmount();

    vi.setSystemTime(Date.now() + 30_001);
    render(withSession(signedIn("op-a"), <Probe load={loader.load} freshMs={30_000} />));
    expect(screen.getByTestId("probe")).toHaveTextContent("ready:v1");
    await act(async () => {});
    expect(screen.getByTestId("probe")).toHaveTextContent("ready:v2");
    expect(loader.calls.n).toBe(2);
  });

  it("uses the default window of 30 seconds when the read does not set one", async () => {
    const loader = countingLoader();
    function DefaultProbe() {
      const [state] = useResource(loader.load);
      return <p data-testid="probe">{state.kind === "ready" ? `ready:${state.data}` : state.kind}</p>;
    }
    const first = render(withSession(signedIn("op-a"), <DefaultProbe />));
    await act(async () => {});
    first.unmount();
    render(withSession(signedIn("op-a"), <DefaultProbe />));
    await act(async () => {});
    expect(loader.calls.n).toBe(1);
  });

  it("reload always asks, inside the window too", async () => {
    const loader = countingLoader();
    render(withSession(signedIn("op-a"), <Probe load={loader.load} freshMs={30_000} />));
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "reload" }));
    await act(async () => {});
    expect(screen.getByTestId("probe")).toHaveTextContent("ready:v2");
    expect(loader.calls.n).toBe(2);
  });

  it("a write ends every window: the next visit asks again", async () => {
    const loader = countingLoader();
    const first = render(withSession(signedIn("op-a"), <Probe load={loader.load} freshMs={30_000} />));
    await act(async () => {});
    first.unmount();

    noteWrite();
    render(withSession(signedIn("op-a"), <Probe load={loader.load} freshMs={30_000} />));
    await act(async () => {});
    expect(screen.getByTestId("probe")).toHaveTextContent("ready:v2");
    expect(loader.calls.n).toBe(2);
  });

  it("an answer requested before a write is never treated as fresh", async () => {
    const loader = controllableLoader();
    const first = render(withSession(signedIn("op-a"), <Probe load={loader.load} freshMs={30_000} />));
    noteWrite(); // a command lands while the read is in flight
    await act(async () => loader.pending.resolve("before-write"));
    first.unmount();

    loader.reset();
    render(withSession(signedIn("op-a"), <Probe load={loader.load} freshMs={30_000} />));
    expect(screen.getByTestId("probe")).toHaveTextContent("ready:before-write");
    await act(async () => loader.pending.resolve("after-write"));
    expect(screen.getByTestId("probe")).toHaveTextContent("ready:after-write");
  });
});
