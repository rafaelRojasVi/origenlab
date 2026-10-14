import "@testing-library/jest-dom";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProductImage } from "./ProductImage";
import * as urls from "./imageUrls";

describe("ProductImage", () => {
  beforeEach(() => urls.resetImageUrls());
  afterEach(() => vi.restoreAllMocks());

  it("shows the placeholder when the product has no image, without asking for one", () => {
    const spy = vi.spyOn(urls, "requestImageUrl");
    render(<ProductImage imageId={null} kind="equipment" alt="Sonda" size="thumb" />);
    expect(screen.getByTestId("image-placeholder")).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
  });

  it("renders the signed URL", async () => {
    vi.spyOn(urls, "requestImageUrl").mockResolvedValue("https://s.test/a");
    render(<ProductImage imageId="a" kind="equipment" alt="Sonda" size="large" />);
    expect(await screen.findByRole("img", { name: "Sonda" })).toHaveAttribute("src", "https://s.test/a");
  });

  it("re-signs once after a load error, then gives up to the placeholder", async () => {
    const request = vi.spyOn(urls, "requestImageUrl")
      .mockResolvedValueOnce("https://s.test/old")
      .mockResolvedValueOnce("https://s.test/new");
    const invalidate = vi.spyOn(urls, "invalidateImageUrl");
    render(<ProductImage imageId="a" kind="equipment" alt="Sonda" size="large" />);
    fireEvent.error(await screen.findByRole("img", { name: "Sonda" }));
    await waitFor(() => expect(screen.getByRole("img", { name: "Sonda" })).toHaveAttribute("src", "https://s.test/new"));
    expect(invalidate).toHaveBeenCalledWith("a");
    fireEvent.error(screen.getByRole("img", { name: "Sonda" }));
    expect(await screen.findByTestId("image-placeholder")).toBeInTheDocument();
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("a thumbnail asks only once it scrolls into view", async () => {
    let callback: IntersectionObserverCallback = () => undefined;
    vi.stubGlobal("IntersectionObserver", class {
      constructor(cb: IntersectionObserverCallback) { callback = cb; }
      observe() {}
      disconnect() {}
      unobserve() {}
    });
    const request = vi.spyOn(urls, "requestImageUrl").mockResolvedValue("https://s.test/a");
    render(<ProductImage imageId="a" kind="equipment" alt="Sonda" size="thumb" />);
    expect(request).not.toHaveBeenCalled();
    await act(async () => callback([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver));
    expect(await screen.findByRole("img", { name: "Sonda" })).toHaveAttribute("loading", "lazy");
    vi.unstubAllGlobals();
  });
});
