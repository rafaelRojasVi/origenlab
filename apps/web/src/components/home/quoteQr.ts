/**
 * Codificador QR sin dependencias, sólo en el build (lo usa `QuoteShowcase.astro`
 * para dibujar el QR de WhatsApp de la cotización de ejemplo como SVG estático:
 * no llega JavaScript al navegador). Es una copia del encoder del Cotizador
 * (`apps/dashboard/src/lib/qr.ts`, rama feat/quote-pdf-whatsapp-qr, probado allí
 * contra el ejemplo del estándar): modo byte, corrección M, versiones 1-6
 * (hasta 106 bytes). Sigue la estructura de ISO/IEC 18004 y las convenciones
 * de la implementación de referencia de Project Nayuki.
 */
export type QrMatrix = boolean[][];

/** Per version 1..6 at level M: [total codewords, EC codewords per block, blocks]. */
const M_TABLE: ReadonlyArray<readonly [number, number, number]> = [
  [26, 10, 1], [44, 16, 1], [70, 26, 1], [100, 18, 2], [134, 24, 2], [172, 16, 4],
];
const MAX_VERSION = M_TABLE.length;

const EXP = new Uint8Array(512);
const LOG = new Uint8Array(256);
for (let i = 0, x = 1; i < 255; i++) {
  EXP[i] = x;
  LOG[x] = i;
  x = x << 1 ^ (x & 128 ? 0x11d : 0);
}
for (let i = 255; i < 512; i++) EXP[i] = EXP[i - 255];
const gmul = (a: number, b: number) => (a && b ? EXP[LOG[a] + LOG[b]] : 0);

/** Reed-Solomon remainder of `data` x^n divided by prod (x - a^i), i < n. */
export function rsRemainder(data: number[], n: number): number[] {
  let gen = [1];
  for (let i = 0; i < n; i++) {
    const next = new Array<number>(gen.length + 1).fill(0);
    gen.forEach((c, j) => {
      next[j] ^= c;
      next[j + 1] ^= gmul(c, EXP[i]);
    });
    gen = next;
  }
  const rem = new Array<number>(n).fill(0);
  for (const byte of data) {
    const f = byte ^ rem.shift()!;
    rem.push(0);
    for (let i = 0; i < n; i++) rem[i] ^= gmul(gen[i + 1], f);
  }
  return rem;
}

/** Data bits for byte mode: 0100, 8-bit count, bytes, terminator, padding 0xEC/0x11. */
function dataCodewords(bytes: Uint8Array, capacity: number): number[] {
  const bits: number[] = [];
  const push = (v: number, len: number) => {
    for (let i = len - 1; i >= 0; i--) bits.push((v >>> i) & 1);
  };
  push(0b0100, 4);
  push(bytes.length, 8);
  bytes.forEach((b) => push(b, 8));
  push(0, Math.min(4, capacity * 8 - bits.length));
  while (bits.length % 8) bits.push(0);
  const out: number[] = [];
  for (let i = 0; i < bits.length; i += 8) out.push(parseInt(bits.slice(i, i + 8).join(''), 2));
  for (let pad = 0xec; out.length < capacity; pad ^= 0xec ^ 0x11) out.push(pad);
  return out;
}

const MASKS: Array<(r: number, c: number) => boolean> = [
  (r, c) => (r + c) % 2 === 0,
  (r) => r % 2 === 0,
  (_, c) => c % 3 === 0,
  (r, c) => (r + c) % 3 === 0,
  (r, c) => (Math.floor(r / 2) + Math.floor(c / 3)) % 2 === 0,
  (r, c) => ((r * c) % 2) + ((r * c) % 3) === 0,
  (r, c) => (((r * c) % 2) + ((r * c) % 3)) % 2 === 0,
  (r, c) => (((r + c) % 2) + ((r * c) % 3)) % 2 === 0,
];

/** The 15 format bits for level M (00) and `mask`: BCH(15,5) with generator 0x537, XOR 0x5412. */
export function formatBitsM(mask: number): number {
  let rem = mask;
  for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
  return ((mask << 10) | rem) ^ 0x5412;
}

function penalty(m: QrMatrix): number {
  const n = m.length;
  let score = 0;
  let dark = 0;
  const line = (get: (i: number) => boolean) => {
    let s = '';
    let run = 1;
    for (let i = 0; i < n; i++) {
      s += get(i) ? '1' : '0';
      if (i > 0 && get(i) === get(i - 1)) run++;
      else run = 1;
      if (run === 5) score += 3;
      else if (run > 5) score += 1;
    }
    // Rule 3: a finder-like 1:1:3:1:1 run with four light modules on either side; outside the
    // symbol counts as light (ISO/IEC 18004, as in Nayuki), so patterns touching an edge count too.
    s = `0000${s}0000`;
    score += 40 * ((s.match(/(?=00001011101|10111010000)/g) ?? []).length);
  };
  for (let i = 0; i < n; i++) {
    line((j) => m[i][j]);
    line((j) => m[j][i]);
    for (let j = 0; j < n; j++) {
      if (m[i][j]) dark++;
      if (i < n - 1 && j < n - 1 && m[i][j] === m[i][j + 1] && m[i][j] === m[i + 1][j] && m[i][j] === m[i + 1][j + 1]) score += 3;
    }
  }
  const k = Math.ceil(Math.abs(dark * 20 - n * n * 10) / (n * n)) - 1;
  return score + 10 * Math.max(0, k);
}

/**
 * Encode `text` (UTF-8) as a QR symbol; `true` is a dark module. No quiet zone included.
 * `mask` (0-7) forces a mask pattern, for tests against a reference encoder; default: the best by penalty.
 */
export function encodeQr(text: string, mask?: number): QrMatrix {
  if (mask !== undefined && !(Number.isInteger(mask) && mask >= 0 && mask < 8)) throw new RangeError(`QR: mask ${mask} is not 0-7`);
  const bytes = new TextEncoder().encode(text);
  const version = M_TABLE.findIndex(([total, ec, blocks]) => bytes.length <= total - ec * blocks - 2) + 1;
  if (version === 0) throw new RangeError(`QR: ${bytes.length} bytes do not fit version ${MAX_VERSION} at level M`);
  const [total, ecLen, blocks] = M_TABLE[version - 1];
  const dataLen = total - ecLen * blocks;

  // Codewords: blocks are all the same size at these versions; interleave data, then EC.
  const data = dataCodewords(bytes, dataLen);
  const per = dataLen / blocks;
  const dataBlocks = Array.from({ length: blocks }, (_, b) => data.slice(b * per, (b + 1) * per));
  const ecBlocks = dataBlocks.map((d) => rsRemainder(d, ecLen));
  const words: number[] = [];
  for (let i = 0; i < per; i++) dataBlocks.forEach((d) => words.push(d[i]));
  for (let i = 0; i < ecLen; i++) ecBlocks.forEach((e) => words.push(e[i]));
  const bits = words.flatMap((w) => Array.from({ length: 8 }, (_, i) => ((w >>> (7 - i)) & 1) === 1));

  const size = 4 * version + 17;
  const grid: QrMatrix = Array.from({ length: size }, () => new Array<boolean>(size).fill(false));
  const fixed: boolean[][] = Array.from({ length: size }, () => new Array<boolean>(size).fill(false));
  const set = (x: number, y: number, v: boolean) => {
    grid[y][x] = v;
    fixed[y][x] = true;
  };
  const drawFormat = (mask: number) => {
    const f = formatBitsM(mask);
    const bit = (i: number) => ((f >>> i) & 1) === 1;
    for (let i = 0; i <= 5; i++) set(8, i, bit(i));
    set(8, 7, bit(6));
    set(8, 8, bit(7));
    set(7, 8, bit(8));
    for (let i = 9; i < 15; i++) set(14 - i, 8, bit(i));
    for (let i = 0; i < 8; i++) set(size - 1 - i, 8, bit(i));
    for (let i = 8; i < 15; i++) set(8, size - 15 + i, bit(i));
    set(8, size - 8, true); // the always-dark module
  };

  for (let i = 0; i < size; i++) {
    set(6, i, i % 2 === 0);
    set(i, 6, i % 2 === 0);
  }
  for (const [cx, cy] of [[3, 3], [size - 4, 3], [3, size - 4]]) {
    for (let dy = -4; dy <= 4; dy++) {
      for (let dx = -4; dx <= 4; dx++) {
        const d = Math.max(Math.abs(dx), Math.abs(dy));
        if (cx + dx >= 0 && cx + dx < size && cy + dy >= 0 && cy + dy < size) set(cx + dx, cy + dy, d !== 2 && d !== 4);
      }
    }
  }
  if (version >= 2) {
    for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) set(size - 7 + dx, size - 7 + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
  }
  drawFormat(0); // reserves the format areas

  let k = 0;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5;
    for (let v = 0; v < size; v++) {
      for (let j = 0; j < 2; j++) {
        const x = right - j;
        const y = ((right + 1) & 2) === 0 ? size - 1 - v : v;
        if (!fixed[y][x]) grid[y][x] = k < bits.length ? bits[k++] : false;
      }
    }
  }

  let best: QrMatrix | null = null;
  let bestScore = Infinity;
  for (const m of mask === undefined ? [0, 1, 2, 3, 4, 5, 6, 7] : [mask]) {
    for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) if (!fixed[y][x] && MASKS[m](y, x)) grid[y][x] = !grid[y][x];
    drawFormat(m);
    const score = penalty(grid);
    if (score < bestScore) {
      bestScore = score;
      best = grid.map((row) => row.slice());
    }
    for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) if (!fixed[y][x] && MASKS[m](y, x)) grid[y][x] = !grid[y][x]; // undo
  }
  return best!;
}

/**
 * Una fila por `<path>` (y = fila del símbolo), con 4 módulos de zona de
 * silencio: la animación de la portada dibuja el código fila a fila.
 */
export function qrRows(text: string): { side: number; rows: string[] } {
  const m = encodeQr(text);
  const quiet = 4;
  const rows = m.map((row, y) => {
    let d = '';
    for (let x = 0; x < row.length; x++) {
      if (!row[x]) continue;
      let len = 1;
      while (row[x + len]) len++;
      d += `M${x + quiet} ${y + quiet}h${len}v1h-${len}z`;
      x += len - 1;
    }
    return d;
  });
  return { side: m.length + quiet * 2, rows };
}
