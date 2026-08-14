import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OrigenLabStaticLogo } from "./OrigenLabStaticLogo";

describe("OrigenLabStaticLogo", () => {
  it("renders static mark without canvas", () => {
    const { container } = render(<OrigenLabStaticLogo />);
    expect(screen.getByTestId("origenlab-logo-static")).toBeTruthy();
    expect(screen.getByText("OrigenLab")).toBeTruthy();
    expect(screen.getByText("Panel operador")).toBeTruthy();
    expect(container.querySelector("canvas")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
  });
});
