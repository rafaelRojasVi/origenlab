import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HeroHeader, countLine } from "./HeroHeader";

const NOW = new Date("2026-10-09T18:47:00Z"); // 15:47 in Santiago

describe("HeroHeader", () => {
  it("greets the profile by first name with the date, time and today's counts", () => {
    render(<HeroHeader name="Tatiana Vivanco" counts={{ replies: 2, decide: 3, followUps: 4 }} now={NOW} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Hola, Tatiana");
    expect(screen.getByText(/Viernes 9 de octubre/)).toBeInTheDocument();
    expect(screen.getByText("15:47")).toBeInTheDocument();
    expect(screen.getByText(/2 por responder · 4 seguimientos · 3 por decidir/)).toBeInTheDocument();
  });

  it("leaves out what is zero and says when everything is done", () => {
    expect(countLine({ replies: 0, decide: 3, followUps: 0 })).toBe("3 por decidir");
    expect(countLine({ replies: 0, decide: 0, followUps: 1 })).toBe("1 seguimiento");
    expect(countLine({ replies: 0, decide: 0, followUps: 0 })).toBe("todo al día");
  });

  it("does not repeat the top bar's Gmail, Drive and website links", () => {
    render(<HeroHeader name="Tatiana" counts={null} now={NOW} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
