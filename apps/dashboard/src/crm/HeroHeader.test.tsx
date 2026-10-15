import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HeroHeader, countLine } from "./HeroHeader";

// Every value below is invented; the repository is public.
const NOW = new Date("2026-10-09T18:47:00Z"); // 15:47 in Santiago

describe("HeroHeader", () => {
  it("greets the profile by first name with the date, time and today's counts", () => {
    render(<HeroHeader name="Tatiana Vivanco" counts={{ paraTi: 4, hechas: 2 }} now={NOW} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Hola, Tatiana");
    expect(screen.getByText(/Viernes 9 de octubre/)).toBeInTheDocument();
    expect(screen.getByText("15:47")).toBeInTheDocument();
    expect(screen.getByText(/4 para ti · 2 hechas/)).toBeInTheDocument();
  });

  it("leaves out «hechas» when zero, and says «todo al día» when both are zero", () => {
    expect(countLine({ paraTi: 5, hechas: 0 })).toBe("5 para ti");
    expect(countLine({ paraTi: 0, hechas: 3 })).toBe("3 hechas");
    expect(countLine({ paraTi: 0, hechas: 0 })).toBe("todo al día");
  });

  it("does not repeat the top bar's Gmail, Drive and website links", () => {
    render(<HeroHeader name="Tatiana" counts={null} now={NOW} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
