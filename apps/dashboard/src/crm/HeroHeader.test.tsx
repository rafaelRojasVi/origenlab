import "@testing-library/jest-dom";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HeroHeader } from "./HeroHeader";

const NOW = new Date("2026-10-09T18:47:00Z"); // 15:47 in Santiago

describe("HeroHeader", () => {
  it("greets the profile by first name with the date, time and today's counts", () => {
    render(<HeroHeader name="Tatiana Vivanco" counts={{ decide: 3, followUps: 4 }} driveCasosUrl={null} now={NOW} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Hola, Tatiana");
    expect(screen.getByText(/Viernes 9 de octubre/)).toBeInTheDocument();
    expect(screen.getByText("15:47")).toBeInTheDocument();
    expect(screen.getByText(/3 por decidir · 4 seguimientos/)).toBeInTheDocument();
  });

  it("links Gmail, Drive casos and the website, in new tabs, as the shared account", () => {
    render(<HeroHeader name="Tatiana" counts={null} driveCasosUrl="https://drive.google.com/drive/folders/abc" now={NOW} />);
    const nav = within(screen.getByRole("navigation", { name: "Accesos" }));
    expect(nav.getByRole("link", { name: /Gmail/ })).toHaveAttribute("href", "https://mail.google.com/mail/?authuser=contacto%40origenlab.cl");
    expect(nav.getByRole("link", { name: /Drive casos/ })).toHaveAttribute("href", "https://drive.google.com/drive/folders/abc?authuser=contacto%40origenlab.cl");
    expect(nav.getByRole("link", { name: /Sitio web/ })).toHaveAttribute("href", "https://origenlab.cl/");
    for (const a of nav.getAllByRole("link")) expect(a).toHaveAttribute("target", "_blank");
  });

  it("falls back to the Drive home when the casos folder is not configured", () => {
    render(<HeroHeader name="Tatiana" counts={null} driveCasosUrl={null} now={NOW} />);
    expect(screen.getByRole("link", { name: /Drive casos/ }))
      .toHaveAttribute("href", "https://drive.google.com/drive/my-drive?authuser=contacto%40origenlab.cl");
  });
});
