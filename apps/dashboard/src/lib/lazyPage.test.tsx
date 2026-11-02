import "@testing-library/jest-dom";
import { Component, Suspense, type ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { lazyPage } from "./lazyPage";

class Boundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? <p>falló</p> : this.props.children;
  }
}

function mount(load: () => Promise<() => ReactNode>) {
  const Page = lazyPage(load);
  render(
    <Boundary>
      <Suspense fallback={<p>cargando</p>}>
        <Page />
      </Suspense>
    </Boundary>,
  );
}

describe("lazyPage", () => {
  const reload = vi.fn();

  beforeEach(() => {
    sessionStorage.clear();
    reload.mockReset();
    vi.stubGlobal("location", { ...window.location, reload });
    vi.spyOn(console, "error").mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows the page once its chunk arrives, and clears the reload flag", async () => {
    sessionStorage.setItem("crm.chunk.reloaded", "1");
    mount(() => Promise.resolve(() => <p>página</p>));
    expect(await screen.findByText("página")).toBeInTheDocument();
    expect(sessionStorage.getItem("crm.chunk.reloaded")).toBeNull();
    expect(reload).not.toHaveBeenCalled();
  });

  it("reloads once when a chunk from an older release is gone", async () => {
    mount(() => Promise.reject(new TypeError("Failed to fetch dynamically imported module")));
    expect(await screen.findByText("falló")).toBeInTheDocument();
    expect(reload).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem("crm.chunk.reloaded")).toBe("1");
  });

  it("does not reload again when the reload did not help", async () => {
    sessionStorage.setItem("crm.chunk.reloaded", "1");
    mount(() => Promise.reject(new TypeError("Failed to fetch dynamically imported module")));
    expect(await screen.findByText("falló")).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
  });
});
