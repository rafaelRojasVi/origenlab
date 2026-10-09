import "@testing-library/jest-dom";
import { act, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it } from "vitest";
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

function Probe({ load }: { load: () => Promise<string> }) {
  const [state] = useResource(load);
  return <p data-testid="probe">{state.kind === "ready" ? `ready:${state.data}` : state.kind}</p>;
}

afterEach(() => clearResourceCache());

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

function ReloadProbe({ load, deps }: { load: () => Promise<string>; deps: readonly unknown[] }) {
  const [state, reload, refreshing] = useResource(load, deps);
  return (
    <>
      <p data-testid="probe">{state.kind === "ready" ? `ready:${state.data}` : state.kind}</p>
      <p data-testid="refreshing">{refreshing ? "yes" : "no"}</p>
      <button type="button" onClick={reload}>
        reload
      </button>
    </>
  );
}

describe("useResource refreshing", () => {
  it("keeps the data on screen during a reload and says it is refreshing", async () => {
    const loader = controllableLoader();
    render(<ReloadProbe load={loader.load} deps={["a"]} />);
    await act(async () => loader.pending.resolve("v1"));
    expect(screen.getByTestId("refreshing")).toHaveTextContent("no");

    loader.reset();
    await act(async () => screen.getByRole("button", { name: "reload" }).click());
    expect(screen.getByTestId("probe")).toHaveTextContent("ready:v1");
    expect(screen.getByTestId("refreshing")).toHaveTextContent("yes");
    await act(async () => loader.pending.resolve("v2"));
    expect(screen.getByTestId("probe")).toHaveTextContent("ready:v2");
    expect(screen.getByTestId("refreshing")).toHaveTextContent("no");
  });

  it("never shows another read's rows while a new read loads", async () => {
    const loader = controllableLoader();
    const view = render(<ReloadProbe load={loader.load} deps={["a"]} />);
    await act(async () => loader.pending.resolve("rows of a"));
    loader.reset();
    view.rerender(<ReloadProbe load={loader.load} deps={["b"]} />);
    expect(screen.getByTestId("probe")).toHaveTextContent("loading");
    await act(async () => loader.pending.resolve("rows of b"));
    expect(screen.getByTestId("probe")).toHaveTextContent("ready:rows of b");
  });
});
