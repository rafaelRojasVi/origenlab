import "@testing-library/jest-dom";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { encodeQr, formatBitsM, QrSvg } from "./qr";

afterEach(cleanup);

const sizeOf = (text: string) => encodeQr(text).length;

/** Finder pattern at (row, col): 7x7 ring, 3x3 core, light ring between. */
function expectFinder(m: boolean[][], r0: number, c0: number) {
  for (let r = 0; r < 7; r++) {
    for (let c = 0; c < 7; c++) {
      const edge = r === 0 || r === 6 || c === 0 || c === 6;
      const core = r >= 2 && r <= 4 && c >= 2 && c <= 4;
      expect(m[r0 + r][c0 + c]).toBe(edge || core);
    }
  }
}

describe("encodeQr", () => {
  it("picks the smallest version for level M in byte mode (21, 25, 29, 33, 37, 41 modules)", () => {
    expect(sizeOf("a".repeat(14))).toBe(21); // version 1 holds 14 bytes
    expect(sizeOf("a".repeat(15))).toBe(25);
    expect(sizeOf("a".repeat(26))).toBe(25);
    expect(sizeOf("a".repeat(27))).toBe(29);
    expect(sizeOf("a".repeat(42))).toBe(29);
    expect(sizeOf("a".repeat(43))).toBe(33);
    expect(sizeOf("a".repeat(62))).toBe(33);
    expect(sizeOf("a".repeat(63))).toBe(37);
    expect(sizeOf("a".repeat(84))).toBe(37);
    expect(sizeOf("a".repeat(85))).toBe(41);
    expect(sizeOf("a".repeat(106))).toBe(41);
  });

  it("counts UTF-8 bytes, and refuses what does not fit version 6", () => {
    expect(sizeOf("ñ".repeat(7))).toBe(21); // 14 bytes
    expect(() => encodeQr("a".repeat(107))).toThrow(RangeError);
  });

  it("has the three finder patterns, timing patterns and the dark module", () => {
    for (const text of ["hello", "https://wa.me/56912345678?text=Hola"]) {
      const m = encodeQr(text);
      const n = m.length;
      expectFinder(m, 0, 0);
      expectFinder(m, 0, n - 7);
      expectFinder(m, n - 7, 0);
      for (let i = 8; i < n - 8; i++) {
        expect(m[6][i]).toBe(i % 2 === 0);
        expect(m[i][6]).toBe(i % 2 === 0);
      }
      expect(m[n - 8][8]).toBe(true);
      // Light separators around the finders.
      for (let i = 0; i < 8; i++) {
        expect(m[7][i]).toBe(false);
        expect(m[i][7]).toBe(false);
      }
    }
  });

  it("has the alignment pattern from version 2 on, none in version 1", () => {
    const v2 = encodeQr("a".repeat(15));
    const c = v2.length - 7; // centre (18, 18)
    expect(v2[c][c]).toBe(true);
    expect(v2[c - 1][c - 1]).toBe(false);
    expect(v2[c - 2][c - 2]).toBe(true);
    expect(v2[c][c + 2]).toBe(true);
  });

  it("writes the format information for level M twice, with the mask it chose", () => {
    // Known values: level M, mask 0 -> 101010000010010; level M, mask 5 -> 100000011001110.
    expect(formatBitsM(0)).toBe(0b101010000010010);
    expect(formatBitsM(5)).toBe(0b100000011001110);
    for (const text of ["hello", "https://wa.me/56912345678?text=Hola%20OrigenLab"]) {
      const m = encodeQr(text);
      const n = m.length;
      // First copy (around the top-left finder), read as the standard orders it.
      const first = [0, 1, 2, 3, 4, 5].map((i) => m[i][8]).concat(m[7][8], m[8][8], m[8][7], [5, 4, 3, 2, 1, 0].map((i) => m[8][i]));
      // Second copy: bits 0-7 up the right column of the bottom-left, 8-14 along the right of the top-right finder.
      const second = [0, 1, 2, 3, 4, 5, 6, 7].map((i) => m[8][n - 1 - i]).concat([8, 9, 10, 11, 12, 13, 14].map((i) => m[n - 15 + i][8]));
      const value = (bits: boolean[]) => bits.reduce((a, b, i) => a | ((b ? 1 : 0) << i), 0);
      const word = value(first);
      expect(value(second)).toBe(word);
      expect([0, 1, 2, 3, 4, 5, 6, 7].map(formatBitsM)).toContain(word);
    }
  });

  it("is deterministic", () => {
    expect(encodeQr("https://wa.me/56912345678")).toEqual(encodeQr("https://wa.me/56912345678"));
  });
});

describe("QrSvg", () => {
  it("renders an accessible inline SVG with a 4-module quiet zone", () => {
    const { container } = render(<QrSvg text="hello" />);
    const svg = screen.getByRole("img", { name: "Código QR para escribir por WhatsApp" });
    expect(svg.tagName.toLowerCase()).toBe("svg");
    expect(svg).toHaveAttribute("width", "22mm");
    expect(svg).toHaveAttribute("viewBox", "0 0 29 29"); // 21 + 2 x 4
    expect(container.querySelector("rect")).toHaveAttribute("fill", "#ffffff");
    const d = container.querySelector("path")!.getAttribute("d")!;
    expect(d.startsWith("M4 4h7")).toBe(true); // the top-left finder starts at the quiet zone's edge
  });

  it("renders nothing when the text cannot be encoded safely", () => {
    const { container } = render(<QrSvg text={"a".repeat(200)} />);
    expect(container).toBeEmptyDOMElement();
  });
});
