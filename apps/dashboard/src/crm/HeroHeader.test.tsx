import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HeroHeader, countLine } from "./HeroHeader";

// Every value below is invented; the repository is public.
const NOW = new Date("2026-10-09T18:47:00Z"); // 15:47 in Santiago

describe("HeroHeader", () => {
  it("greets the profile by first name with the date, time and today's counts", () => {
    render(<HeroHeader name="Tatiana Vivanco" counts={{ cotizar: 6, responder: 4, seguimientos: 8, hechas: 2 }} now={NOW} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Hola, Tatiana");
    expect(screen.getByText(/Viernes 9 de octubre/)).toBeInTheDocument();
    expect(screen.getByText("15:47")).toBeInTheDocument();
    expect(screen.getByText(/6 por cotizar · 4 por responder · 8 seguimientos · 2 hechas/)).toBeInTheDocument();
  });

  it("omits zero segments and says «todo al día» when all are zero", () => {
    expect(countLine({ cotizar: 3, responder: 0, seguimientos: 2, hechas: 0 })).toBe("3 por cotizar · 2 seguimientos");
    expect(countLine({ cotizar: 0, responder: 2, seguimientos: 0, hechas: 1 })).toBe("2 por responder · 1 hecha");
    expect(countLine({ cotizar: 0, responder: 0, seguimientos: 0, hechas: 0 })).toBe("todo al día");
    expect(countLine({ cotizar: 1, responder: 1, seguimientos: 1, hechas: 1 })).toBe("1 por cotizar · 1 por responder · 1 seguimiento · 1 hecha");
  });

  it("does not repeat the top bar's Gmail, Drive and website links", () => {
    render(<HeroHeader name="Tatiana" counts={null} now={NOW} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
