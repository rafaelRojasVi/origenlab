import "@testing-library/jest-dom";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { encodeQr, fitsQr, formatBitsM, QrSvg, rsRemainder } from "./qr";

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

/**
 * A test-side QR reader, written from the standard and sharing no code with the encoder except
 * `formatBitsM`: reads the mask from the format information, unmasks, walks the zigzag over the
 * non-function modules, de-interleaves the blocks, checks every block's error-correction codewords
 * with `rsRemainder`, and returns the byte-mode payload.
 */
const EC_M = [[26, 10, 1], [44, 16, 1], [70, 26, 1], [100, 18, 2], [134, 24, 2], [172, 16, 4]]; // total, EC per block, blocks

function readQr(m: boolean[][]): string {
  const n = m.length;
  const version = (n - 17) / 4;
  const [total, ecLen, blocks] = EC_M[version - 1];
  const bit = (b: boolean) => (b ? 1 : 0);
  const word = [0, 1, 2, 3, 4, 5].map((i) => m[i][8]).concat(m[7][8], m[8][8], m[8][7], [5, 4, 3, 2, 1, 0].map((i) => m[8][i])).reduce((a, b, i) => a | (bit(b) << i), 0);
  const mask = [0, 1, 2, 3, 4, 5, 6, 7].find((k) => formatBitsM(k) === word);
  if (mask === undefined) throw new Error("bad format information");
  const masked = [
    (r: number, c: number) => (r + c) % 2 === 0,
    (r: number) => r % 2 === 0,
    (_: number, c: number) => c % 3 === 0,
    (r: number, c: number) => (r + c) % 3 === 0,
    (r: number, c: number) => (Math.floor(r / 2) + Math.floor(c / 3)) % 2 === 0,
    (r: number, c: number) => ((r * c) % 2) + ((r * c) % 3) === 0,
    (r: number, c: number) => (((r * c) % 2) + ((r * c) % 3)) % 2 === 0,
    (r: number, c: number) => (((r + c) % 2) + ((r * c) % 3)) % 2 === 0,
  ][mask];
  const isFunction = (r: number, c: number) =>
    (r < 9 && c < 9) || (r < 9 && c >= n - 8) || (r >= n - 8 && c < 9) || r === 6 || c === 6 || (version >= 2 && Math.abs(r - (n - 7)) <= 2 && Math.abs(c - (n - 7)) <= 2);
  // Zigzag: pairs of columns from the right, upward then downward, skipping column 6.
  const bits: number[] = [];
  let up = true;
  for (let right = n - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5;
    for (let i = 0; i < n; i++) {
      const r = up ? n - 1 - i : i;
      for (const c of [right, right - 1]) if (!isFunction(r, c)) bits.push(bit(m[r][c]) ^ bit(masked(r, c)));
    }
    up = !up;
  }
  const words: number[] = [];
  for (let i = 0; i + 8 <= total * 8; i += 8) words.push(bits.slice(i, i + 8).reduce((a, b) => (a << 1) | b, 0));
  const per = (total - ecLen * blocks) / blocks;
  const dataBlocks = Array.from({ length: blocks }, (_, b) => Array.from({ length: per }, (_, i) => words[i * blocks + b]));
  const ecBlocks = Array.from({ length: blocks }, (_, b) => Array.from({ length: ecLen }, (_, i) => words[per * blocks + i * blocks + b]));
  dataBlocks.forEach((d, b) => expect(ecBlocks[b]).toEqual(rsRemainder(d, ecLen)));
  const data = dataBlocks.flat();
  expect(data[0] >> 4).toBe(0b0100); // byte mode
  const len = ((data[0] & 15) << 4) | (data[1] >> 4);
  const bytes = Array.from({ length: len }, (_, i) => ((data[1 + i] & 15) << 4) | (data[2 + i] >> 4));
  return new TextDecoder().decode(new Uint8Array(bytes));
}

describe("Reed-Solomon", () => {
  it("matches the standard's HELLO WORLD example (version 1-M)", () => {
    const data = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17];
    expect(rsRemainder(data, 10)).toEqual([196, 35, 39, 119, 235, 215, 231, 226, 93, 23]);
  });
});

describe("a symbol read back", () => {
  const url = "https://wa.me/56912345678?text=Hola%20OrigenLab%2C%20cotizaci%C3%B3n%20CN01234A-26.";
  const payloads: Array<[number, string]> = [
    [1, "HELLO WORLD"],
    [2, "https://wa.me/56912345678"],
    [3, "ñandú ✓ ".repeat(2) + "x".repeat(10)], // 34 bytes
    [4, url.slice(0, 50)],
    [5, url], // the shipped link: 83 bytes
    [6, url + "x".repeat(15)], // 98 bytes
  ];
  it.each(payloads)("version %i: the payload comes back and every block's error correction checks out", (version, text) => {
    const m = encodeQr(text);
    expect(m.length).toBe(4 * version + 17);
    expect(readQr(m)).toBe(text);
  });
  it("reads the largest payload of each version, and the shipped link", () => {
    for (const [i, [total, ec, blocks]] of EC_M.entries()) {
      const text = "z".repeat(total - ec * blocks - 2);
      expect(encodeQr(text).length).toBe(4 * (i + 1) + 17);
      expect(readQr(encodeQr(text))).toBe(text);
    }
    expect(readQr(encodeQr(url))).toBe(url);
  });
  it("fitsQr says what encodeQr will do", () => {
    expect(fitsQr(url)).toBe(true);
    expect(fitsQr("a".repeat(107))).toBe(false);
  });
});

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

  it("renders nothing, and warns once in dev, when the text cannot be encoded safely", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const { container } = render(<QrSvg text={"a".repeat(200)} />);
    expect(container).toBeEmptyDOMElement();
    expect(warn).toHaveBeenCalledTimes(1);
    warn.mockRestore();
  });
});
