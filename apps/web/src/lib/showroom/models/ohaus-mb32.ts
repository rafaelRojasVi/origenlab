/**
 * OHAUS MB32 — analizador de humedad por calentamiento de fibra de carbono.
 *
 * Modelado sobre la fotografía VERIFIED del fabricante (`public/products/ohaus/mb32.jpg`,
 * DMX-ID 4295015507), no sobre una descripción:
 *   - cuerpo blanco curvo: la base se ensancha hacia abajo, su borde inferior se
 *     arquea entre las cuatro patas y las esquinas son redondeadas en planta;
 *   - patas rojas moleteadas en las cuatro esquinas;
 *   - tapa alta y redondeada atrás, con una gran cubierta NEGRA del calefactor
 *     arriba: parte delantera lisa con el triángulo amarillo de superficie
 *     caliente y aletas transversales detrás; la cubierta dobla sobre el borde
 *     delantero de la tapa; lengüeta gris al pie, rejillas en el frente;
 *   - consola inclinada delante de la tapa: recuadro gris claro con una línea
 *     roja arriba, el logotipo rojo a la izquierda, la LCD retroiluminada (cifra
 *     grande y una segunda línea en negativo), «MB32» y un botón redondo a la
 *     derecha; en la franja blanca de abajo, los íconos de las teclas y la
 *     PERILLA, bajo la parte derecha de la pantalla;
 *   - riel metálico en la unión de tapa y base, rejilla en el costado derecho y
 *     «OHAUS» en rojo en ese costado.
 *
 * Dimensiones (mm): 211 × 353 × 137 (ancho × fondo × alto), tapa cerrada.
 *   Fuente: Weber Scientific, ficha OHAUS MB32 (weberscientific.com/ohaus-mb32-moisture-analyzers):
 *   «8.3" x 5.4" x 13.9"» = 211 × 137 × 353 mm, sin decir cuál es cuál. Se lee
 *   ancho 211, alto 137 y fondo 353 porque así queda la foto: la LCD (≈ 4″)
 *   ocupa más de un tercio del frente, lo que sólo cuadra con un frente de
 *   ~211 mm. Plato Ø 3,5″ (90 mm), misma ficha. La página oficial
 *   (mx.ohaus.com/es-mx/mb32-6) no se pudo leer desde aquí (certificado TLS).
 *   El ancho de 211 es el máximo, al pie de la base ensanchada.
 *
 * sourceNote — estimado a partir de la foto: proporción tapa/base (base 56 mm
 * hasta la cámara, tapa 67,5 mm más la cubierta y sus aletas; la tapa cubre
 * ~70 % del fondo), consola de 94 mm a ~19°, LCD de 76 × 38 mm, perilla
 * Ø 22 mm, patas Ø 28 × 7 mm,
 * cubierta negra de 128 mm de ancho, el calefactor interior (tres varillas),
 * el ángulo de apertura (100°) y todo lo que la foto no muestra (costado
 * izquierdo, trasera, interior).
 *
 * Ejes: x = ancho, y = alto (0 = suelo), z = fondo; el FRENTE (consola) mira a +z
 * y la bisagra de la tapa está atrás. La cámara general mira desde +z; el giro de
 * presentación (`yaw` negativo) muestra el costado derecho, como la foto.
 *
 * Animación:
 *   `setPower(true)`   la tapa se abre (bisagra trasera) y SE MANTIENE abierta;
 *                      con la tapa abierta, las varillas se ponen al rojo y la
 *                      humedad (%MC) sube en la pantalla hasta el resultado («FIN»)
 *   `setPower(false)`  la tapa se cierra; la pantalla vuelve al peso (0.000 g)
 *   `setKnob(rad)`     gira la perilla; la segunda línea muestra el programa elegido
 *   `setDrying(0…1)`   fija el avance del secado (la historia por scroll); `null` lo devuelve al reloj
 *   `setLidAngle(rad)` fuerza la tapa; `null` la devuelve a su animación
 */
import * as THREE from 'three';
import type { BuiltModel } from '../types';
import { SANS, canvasTexture, disposer } from './kit';

// ── Dimensiones (mm) ──────────────────────────────────────────────────────────
const W = 211; // ancho máximo (pie de la base)
const D = 353; // fondo
const HALF_D = D / 2;
const FEET = 7; // patas
const DECK = 56; // suelo de la cámara = canto superior de la base
const BASE_HW = 98; // medio ancho de la base arriba; abajo se ensancha a W/2
const FLARE = W / 2 / BASE_HW - 1;
const HOOD_HW = 92;
const HOOD_FRONT = 70; // z de la cara delantera de la tapa
const HOOD_BACK = -172;
const HOOD_TOP = 67.5; // sobre DECK; + cubierta 3,5 + aletas 3 + patas 7 = 137
const COVER_HW = 64;
const COVER_T = 3.5; // la cubierta negra sobresale de la tapa
const FIN_H = 3;
const HINGE = new THREE.Vector3(0, DECK + 4, HOOD_BACK + 2);
const LID_OPEN = (100 * Math.PI) / 180;
const PAN_Z = -52; // centro de la cámara y del plato
const PAN_R = 45; // plato Ø 90 mm

// Consola: pendiente de (z, y) = (72, 56) a (160, 26), ~19°.
const SLOPE_REAR = new THREE.Vector2(72, DECK);
const SLOPE_FRONT = new THREE.Vector2(160, 26);

// Panel (mm sobre el panel: x de izquierda a derecha, y de atrás hacia el frente).
const PW = 172;
const PL = 90;
const K = 6; // píxeles por mm del lienzo del panel
const LCD = { x: 36, y: 13, w: 76, h: 38 };
const LK = 8; // píxeles por mm del lienzo de la LCD
const KNOB = { x: 106, y: 72, r: 11 };
const ROUND_BTN = { x: 141, y: 36 };

// Lo que marca el equipo (cifras ilustrativas de pantalla, no especificaciones).
const MC_END = 12.36; // % de humedad final de la muestra ilustrada
const RUN_S = 292; // duración ilustrada del secado, s (04:52)
const SET_TEMP = 105;
const CLOCK_RUN = 12; // s reales que dura el secado sin historia
const METHODS = ['Estándar', 'Rápido'];

// ── Geometría: sólido «loft» de secciones redondeadas ─────────────────────────
type P = [number, number];
interface Section {
  yb: number;
  yt: number;
  hw: number;
}
interface LoftOptions {
  rTop: number;
  rBot: number;
  /** Ensanche relativo al pie de cada sección (0 = paredes rectas). */
  flare?: number;
  steps?: number;
}

/** Puntos de una sección: rectángulo de esquinas redondeadas, en sentido antihorario. */
function ring(sec: Section, o: LoftOptions): P[] {
  const { yb, yt, hw } = sec;
  const h = Math.max(yt - yb, 0.01);
  const rt = Math.min(o.rTop, hw * 0.98, h * 0.65);
  const rb = Math.min(o.rBot, hw * 0.98, h - rt);
  const pts: P[] = [];
  const line = (x0: number, y0: number, x1: number, y1: number, n: number) => {
    for (let i = 0; i < n; i++) pts.push([x0 + ((x1 - x0) * i) / n, y0 + ((y1 - y0) * i) / n]);
  };
  const arc = (cx: number, cy: number, r: number, a0: number, n: number) => {
    for (let i = 0; i < n; i++) {
      const a = a0 + ((Math.PI / 2) * i) / n;
      pts.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]);
    }
  };
  line(hw, yb + rb, hw, yt - rt, 6);
  arc(hw - rt, yt - rt, rt, 0, 8);
  line(hw - rt, yt, -hw + rt, yt, 12);
  arc(-hw + rt, yt - rt, rt, Math.PI / 2, 8);
  line(-hw, yt - rt, -hw, yb + rb, 6);
  arc(-hw + rb, yb + rb, rb, Math.PI, 6);
  line(-hw + rb, yb, hw - rb, yb, 10);
  arc(hw - rb, yb + rb, rb, (3 * Math.PI) / 2, 6);
  const flare = o.flare ?? 0;
  if (flare) for (const p of pts) p[0] *= 1 + flare * (1 - (p[1] - yb) / h) ** 2;
  return pts;
}

/** Sólido cerrado: secciones `at(z)` de z0 a z1 (más densas en los extremos), con tapas planas. */
function loft(z0: number, z1: number, at: (z: number) => Section, o: LoftOptions): THREE.BufferGeometry {
  const steps = o.steps ?? 72;
  const zs: number[] = [];
  for (let k = 0; k <= steps; k++) zs.push(z0 + (z1 - z0) * (0.5 - 0.5 * Math.cos((Math.PI * k) / steps)));
  const rings = zs.map((z) => ring(at(z), o));
  const M = rings[0].length;
  const pos: number[] = [];
  const idx: number[] = [];
  rings.forEach((r, k) => r.forEach(([x, y]) => pos.push(x, y, zs[k])));
  for (let k = 0; k < steps; k++) {
    for (let j = 0; j < M; j++) {
      const a = k * M + j;
      const b = k * M + ((j + 1) % M);
      const c = (k + 1) * M + j;
      const d = (k + 1) * M + ((j + 1) % M);
      idx.push(a, b, c, b, d, c);
    }
  }
  // Tapas: anillos duplicados (arista viva) y un centro.
  const cap = (k: number, front: boolean) => {
    const first = pos.length / 3;
    const sec = at(zs[k]);
    rings[k].forEach(([x, y]) => pos.push(x, y, zs[k]));
    pos.push(0, (sec.yb + sec.yt) / 2, zs[k]);
    const center = first + M;
    for (let j = 0; j < M; j++) {
      const p = first + j;
      const q = first + ((j + 1) % M);
      if (front) idx.push(center, p, q);
      else idx.push(center, q, p);
    }
  };
  cap(0, false);
  cap(steps, true);
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  geo.setIndex(idx);
  geo.computeVertexNormals();
  return geo;
}

/** Medio ancho con las esquinas redondeadas en planta (radio `rb` atrás, `rf` delante). */
function plan(hw: number, z: number, z0: number, z1: number, rb: number, rf: number) {
  const cut = (r: number, d: number) => (d >= r ? 0 : r - Math.sqrt(Math.max(0, r * r - (r - d) ** 2)));
  return hw - cut(rb, z - z0) - cut(rf, z1 - z);
}

// Perfiles laterales.
function baseTop(z: number) {
  if (z < -HALF_D + 10) return DECK - 10 + Math.sqrt(Math.max(0, 100 - (z + HALF_D - 10) ** 2));
  if (z <= SLOPE_REAR.x) return DECK;
  if (z <= SLOPE_FRONT.x)
    return SLOPE_REAR.y + ((SLOPE_FRONT.y - SLOPE_REAR.y) * (z - SLOPE_REAR.x)) / (SLOPE_FRONT.x - SLOPE_REAR.x);
  const t = Math.min(1, (z - SLOPE_FRONT.x) / (HALF_D - SLOPE_FRONT.x));
  return 16 + (SLOPE_FRONT.y - 16) * Math.sqrt(1 - t * t);
}
/** El borde inferior se arquea entre las patas. */
function baseBottom(z: number) {
  return Math.abs(z) >= 140 ? 0 : 8 * (1 + Math.cos((Math.PI * z) / 140));
}
function hoodTop(z: number) {
  const zc = Math.min(z, HOOD_FRONT);
  const R = 16;
  if (zc > HOOD_FRONT - R) return DECK + HOOD_TOP - R + Math.sqrt(Math.max(0, R * R - (zc - (HOOD_FRONT - R)) ** 2));
  if (zc > -80) return DECK + HOOD_TOP;
  const t = Math.min(1, (zc + 80) / (HOOD_BACK + 80));
  return DECK + 8 + (HOOD_TOP - 8) * Math.sqrt(1 - t * t);
}
const coverTop = (z: number) => hoodTop(z) + COVER_T;

// ── Dígitos de siete segmentos ────────────────────────────────────────────────
const SEGMENTS: Record<string, string> = {
  '0': 'abcdef', '1': 'bc', '2': 'abdeg', '3': 'abcdg', '4': 'bcfg',
  '5': 'acdfg', '6': 'acdefg', '7': 'abc', '8': 'abcdefg', '9': 'abcdfg', '-': 'g', ' ': '',
};

function drawSegmentDigit(g: CanvasRenderingContext2D, ch: string, x: number, y: number, w: number, h: number, t: number) {
  const horizontal = (cx: number, cy: number, len: number): P[] => [
    [cx - len / 2, cy], [cx - len / 2 + t / 2, cy - t / 2], [cx + len / 2 - t / 2, cy - t / 2],
    [cx + len / 2, cy], [cx + len / 2 - t / 2, cy + t / 2], [cx - len / 2 + t / 2, cy + t / 2],
  ];
  const vertical = (cx: number, cy: number, len: number): P[] => [
    [cx, cy - len / 2], [cx + t / 2, cy - len / 2 + t / 2], [cx + t / 2, cy + len / 2 - t / 2],
    [cx, cy + len / 2], [cx - t / 2, cy + len / 2 - t / 2], [cx - t / 2, cy - len / 2 + t / 2],
  ];
  const L = w - t * 1.15;
  const M = h / 2 - t * 0.85;
  const parts: Record<string, P[]> = {
    a: horizontal(x + w / 2, y + t / 2, L),
    g: horizontal(x + w / 2, y + h / 2, L),
    d: horizontal(x + w / 2, y + h - t / 2, L),
    f: vertical(x + t / 2, y + h / 4, M),
    b: vertical(x + w - t / 2, y + h / 4, M),
    e: vertical(x + t / 2, y + (3 * h) / 4, M),
    c: vertical(x + w - t / 2, y + (3 * h) / 4, M),
  };
  for (const key of SEGMENTS[ch] ?? '') {
    g.beginPath();
    // Leve inclinación, como la cifra de la foto.
    parts[key].forEach(([px, py], i) => {
      const sx = px + (y + h - py) * 0.08;
      if (i) g.lineTo(sx, py);
      else g.moveTo(sx, py);
    });
    g.closePath();
    g.fill();
  }
}

export interface Mb32Model extends BuiltModel {
  /** Ángulo actual de la tapa en radianes (0 cerrada, ~1,75 abierta). */
  readonly lidAngle: number;
  /** Fuerza el ángulo de la tapa (radianes); `null` la devuelve a su animación. */
  setLidAngle(angle: number | null): void;
  /** Giro de la perilla en radianes; `null` la deja en reposo. */
  setKnob(angle: number | null): void;
  /** Avance del secado 0–1 (0 recién empezado, 1 resultado); `null` lo devuelve al reloj. */
  setDrying(fraction: number | null): void;
}

export function build(): Mb32Model {
  const { keep, dispose } = disposer();
  const root = new THREE.Group();
  const lifted = new THREE.Group(); // todo el equipo, subido lo que miden las patas
  lifted.position.y = FEET;
  root.add(lifted);

  const add = (parent: THREE.Object3D, geo: THREE.BufferGeometry, mat: THREE.Material, x = 0, y = 0, z = 0) => {
    const m = new THREE.Mesh(keep(geo), mat);
    m.position.set(x, y, z);
    parent.add(m);
    return m;
  };

  // ── Materiales ──────────────────────────────────────────────────────────────
  const bodyMat = keep(
    new THREE.MeshPhysicalMaterial({ color: 0xeef0ef, roughness: 0.38, clearcoat: 0.3, clearcoatRoughness: 0.35 }),
  );
  const coverMat = keep(new THREE.MeshStandardMaterial({ color: 0x1c1e20, roughness: 0.55, metalness: 0.1 }));
  const chamberMat = keep(new THREE.MeshStandardMaterial({ color: 0x55595d, roughness: 0.5, metalness: 0.5 }));
  const innerMat = keep(new THREE.MeshStandardMaterial({ color: 0x3a3d40, roughness: 0.6, metalness: 0.3 }));
  const steelMat = keep(
    new THREE.MeshPhysicalMaterial({ color: 0xc4c8cb, metalness: 0.9, roughness: 0.18, side: THREE.DoubleSide }),
  );
  const railMat = keep(new THREE.MeshStandardMaterial({ color: 0xb9bcbd, metalness: 0.85, roughness: 0.25 }));
  const trimMat = keep(new THREE.MeshStandardMaterial({ color: 0xc9ccce, roughness: 0.45 }));
  const footMat = keep(new THREE.MeshStandardMaterial({ color: 0xc42a22, roughness: 0.55, flatShading: true }));
  const slotMat = keep(new THREE.MeshStandardMaterial({ color: 0x6d7276, roughness: 0.8 }));
  const reflectorMat = keep(
    new THREE.MeshStandardMaterial({ color: 0xb9ad8a, metalness: 0.85, roughness: 0.3, emissive: 0xff5a14, emissiveIntensity: 0 }),
  );
  const ceramicMat = keep(new THREE.MeshStandardMaterial({ color: 0xe9dfc9, roughness: 0.8 }));
  const rodMat = keep(
    new THREE.MeshStandardMaterial({ color: 0x2b2522, roughness: 0.5, emissive: 0xff3a08, emissiveIntensity: 0 }),
  );
  const sampleMat = keep(new THREE.MeshStandardMaterial({ color: 0xd8c48c, roughness: 0.9 }));
  const knobMat = keep(new THREE.MeshStandardMaterial({ color: 0xdfe1e2, roughness: 0.35, metalness: 0.25, flatShading: true }));
  const knobTopMat = keep(new THREE.MeshStandardMaterial({ color: 0xe9ebeb, roughness: 0.3, metalness: 0.2 }));
  const markMat = keep(new THREE.MeshBasicMaterial({ color: 0x8a8f93 }));
  const btnRingMat = keep(new THREE.MeshStandardMaterial({ color: 0xb6babd, roughness: 0.4, metalness: 0.3 }));
  const btnCoreMat = keep(new THREE.MeshStandardMaterial({ color: 0x7d8286, roughness: 0.35, metalness: 0.4 }));

  // ── Estado ──────────────────────────────────────────────────────────────────
  let powered = false;
  let lidAngle = 0;
  let lidOverride: number | null = null;
  let knobOverride: number | null = null;
  let dryOverride: number | null = null;
  let readout: number | null = null;
  let dry = 0; // avance del secado con el reloj propio
  let heat = 0;
  let shown = ''; // lo último que se pintó en la LCD

  // ── Base ────────────────────────────────────────────────────────────────────
  const baseAt = (z: number): Section => ({
    yb: baseBottom(z),
    yt: baseTop(z),
    hw: plan(BASE_HW, z, -HALF_D, HALF_D, 18, 24),
  });
  const base = add(lifted, loft(-HALF_D, HALF_D, baseAt, { rTop: 10, rBot: 5, flare: FLARE, steps: 90 }), bodyMat);
  base.castShadow = true;
  base.receiveShadow = true;
  /** Medio ancho exterior de la base a la altura y en z. */
  const baseHalfWidth = (z: number, y: number) => {
    const s = baseAt(z);
    return s.hw * (1 + FLARE * (1 - (y - s.yb) / (s.yt - s.yb)) ** 2);
  };

  // Patas rojas moleteadas en las esquinas.
  const footGeo = keep(new THREE.CylinderGeometry(14, 14.5, FEET + 2, 28));
  const fp = footGeo.attributes.position as THREE.BufferAttribute;
  for (let i = 0; i < fp.count; i++) {
    const r = Math.hypot(fp.getX(i), fp.getZ(i));
    if (r > 5) {
      const k = 1 + 0.035 * Math.cos(Math.atan2(fp.getZ(i), fp.getX(i)) * 14);
      fp.setXYZ(i, fp.getX(i) * k, fp.getY(i), fp.getZ(i) * k);
    }
  }
  footGeo.computeVertexNormals();
  for (const sx of [-1, 1]) {
    for (const sz of [-1, 1]) {
      const foot = new THREE.Mesh(footGeo, footMat);
      foot.position.set(sx * (W / 2 - 13), -FEET / 2 + 1, sz * (HALF_D - 30));
      foot.castShadow = true;
      lifted.add(foot);
    }
  }

  // Suelo oscuro de la cámara, aro contra corrientes, soporte y plato Ø 90 mm.
  add(lifted, new THREE.BoxGeometry(2 * HOOD_HW - 22, 1.2, 186), chamberMat, 0, DECK + 0.6, PAN_Z);
  const shield = add(lifted, new THREE.TorusGeometry(58, 1.8, 8, 72), steelMat, 0, DECK + 2.4, PAN_Z);
  shield.rotation.x = Math.PI / 2;
  const PAN_Y = DECK + 9;
  add(lifted, new THREE.CylinderGeometry(9, 12, 9, 28), steelMat, 0, DECK + 4.5, PAN_Z);
  const panProfile: P[] = [[0, 0], [PAN_R - 5, 0], [PAN_R - 1, 1.5], [PAN_R, 5.2], [PAN_R - 0.9, 5.2], [PAN_R - 2, 2], [PAN_R - 5.4, 0.9], [0, 0.9]];
  add(lifted, new THREE.LatheGeometry(panProfile.map(([r, y]) => new THREE.Vector2(r, y)), 72), steelMat, 0, PAN_Y, PAN_Z);
  const sampleGeo = keep(new THREE.SphereGeometry(24, 28, 14));
  sampleGeo.scale(1, 0.24, 1);
  const sample = new THREE.Mesh(sampleGeo, sampleMat);
  sample.position.set(0, PAN_Y + 1.4, PAN_Z);
  sample.visible = false;
  lifted.add(sample);

  // Riel metálico en la unión de tapa y base, a los dos lados.
  for (const sx of [-1, 1]) add(lifted, new THREE.BoxGeometry(3, 3.5, 224), railMat, sx * (HOOD_HW + 1.5), DECK + 1.6, -51);

  // Rejilla del costado derecho, bajo la consola.
  const slotGeo = keep(new THREE.BoxGeometry(1, 6, 1.8));
  for (let i = 0; i < 9; i++) {
    const z = 92 + i * 5;
    const y = 22 + i * 0.4;
    const slot = new THREE.Mesh(slotGeo, slotMat);
    slot.position.set(baseHalfWidth(z, y) - 0.3, y, z);
    slot.rotation.z = 0.12;
    lifted.add(slot);
  }

  // «OHAUS» en rojo en el costado derecho, leído de adelante hacia atrás.
  const sideTex = canvasTexture(512, 128, (g, w, h) => {
    g.clearRect(0, 0, w, h);
    g.fillStyle = '#d22630';
    g.font = `italic 800 ${h * 0.72}px ${SANS}`;
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText('OHAUS', w / 2, h / 2);
  });
  keep(sideTex.texture);
  const sideMat = keep(new THREE.MeshStandardMaterial({ map: sideTex.texture, transparent: true, roughness: 0.5, depthWrite: false }));
  const sideLogo = add(lifted, new THREE.PlaneGeometry(56, 14), sideMat, baseHalfWidth(-40, 43) + 0.6, 43, -40);
  sideLogo.rotation.y = Math.PI / 2;

  // ── Consola: panel con LCD, botón, íconos y perilla, sobre la pendiente ────
  const slopeDir = new THREE.Vector2().subVectors(SLOPE_REAR, SLOPE_FRONT).normalize();
  const alongW = new THREE.Vector3(0, slopeDir.y, slopeDir.x); // +y del panel: hacia atrás y arriba
  const xAxis = new THREE.Vector3(1, 0, 0);
  const normalW = new THREE.Vector3().crossVectors(xAxis, alongW);
  const panel = new THREE.Group();
  panel.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(xAxis, alongW, normalW));
  const mid = new THREE.Vector3(0, (SLOPE_REAR.y + SLOPE_FRONT.y) / 2, (SLOPE_REAR.x + SLOPE_FRONT.x) / 2);
  mid.addScaledVector(alongW, -1);
  panel.position.copy(mid);
  lifted.add(panel);
  const mm = (x: number, y: number) => new THREE.Vector3(x - PW / 2, PL / 2 - y, 0); // mm del panel → local

  const drawPanel = (g: CanvasRenderingContext2D, w: number, h: number) => {
    g.fillStyle = '#eef0ef';
    g.fillRect(0, 0, w, h);
    // Recuadro gris claro con la línea roja arriba.
    g.fillStyle = '#c8ccce';
    g.beginPath();
    g.roundRect(7 * K, 3 * K, 158 * K, 55 * K, 6 * K);
    g.fill();
    g.fillStyle = '#d22630';
    g.fillRect(30 * K, 7 * K, 130 * K, 0.7 * K);
    // Marco de la LCD.
    g.fillStyle = '#9ea4a8';
    g.beginPath();
    g.roundRect((LCD.x - 2) * K, (LCD.y - 2) * K, (LCD.w + 4) * K, (LCD.h + 4) * K, 1.5 * K);
    g.fill();
    // Logotipo: cuadrado rojo con el óvalo blanco, y el nombre debajo.
    g.fillStyle = '#d22630';
    g.beginPath();
    g.roundRect(15 * K, 19 * K, 12 * K, 12 * K, 1.5 * K);
    g.fill();
    g.strokeStyle = '#ffffff';
    g.lineWidth = 1.3 * K;
    g.beginPath();
    g.ellipse(21 * K, 25 * K, 3.6 * K, 2.4 * K, -0.5, 0, Math.PI * 2);
    g.stroke();
    g.fillStyle = '#d22630';
    g.font = `800 ${2.4 * K}px ${SANS}`;
    g.textAlign = 'center';
    g.textBaseline = 'alphabetic';
    g.fillText('OHAUS', 21 * K, 35.5 * K);
    // «MB32»: MB en negro, 32 en rojo.
    g.font = `italic 800 ${7 * K}px ${SANS}`;
    g.textAlign = 'left';
    g.fillStyle = '#222426';
    g.fillText('MB', 122 * K, 23 * K);
    const mbw = g.measureText('MB').width;
    g.fillStyle = '#d22630';
    g.fillText('32', 122 * K + mbw, 23 * K);
    // Íconos de las teclas en la franja blanca: encendido, detener, flechas, tara.
    g.strokeStyle = '#d22630';
    g.lineWidth = 0.6 * K;
    g.beginPath();
    g.arc(24 * K, 72 * K, 2.6 * K, -Math.PI * 0.3, Math.PI * 1.3);
    g.stroke();
    g.beginPath();
    g.moveTo(24 * K, 68.5 * K);
    g.lineTo(24 * K, 72 * K);
    g.stroke();
    g.fillStyle = '#2a2c2e';
    g.fillRect(43 * K, 70 * K, 4 * K, 4 * K);
    g.beginPath();
    g.moveTo(65 * K, 69.5 * K);
    g.lineTo(61.5 * K, 72 * K);
    g.lineTo(65 * K, 74.5 * K);
    g.closePath();
    g.moveTo(67 * K, 69.5 * K);
    g.lineTo(70.5 * K, 72 * K);
    g.lineTo(67 * K, 74.5 * K);
    g.closePath();
    g.fill();
    g.font = `700 ${4.2 * K}px ${SANS}`;
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText('→T←', 86 * K, 72.3 * K);
  };
  const panelTex = canvasTexture(PW * K, PL * K, drawPanel);
  keep(panelTex.texture);
  const panelMat = keep(new THREE.MeshStandardMaterial({ map: panelTex.texture, roughness: 0.45 }));
  // Placa del panel (2 mm) con el dibujo encima.
  const plate = add(panel, new THREE.BoxGeometry(PW, PL, 2), bodyMat, 0, 0, -0.2);
  plate.receiveShadow = true;
  add(panel, new THREE.PlaneGeometry(PW, PL), panelMat, 0, 0, 0.85);

  // LCD retroiluminada, en su propio lienzo.
  type Screen = { big: string; unit: string; top: string; line: string };
  const IDLE: Screen = { big: '0.000', unit: 'g', top: 'STD', line: 'Coloque la muestra · Tara' };
  let screen: Screen = IDLE;
  const drawLcd = (g: CanvasRenderingContext2D, w: number, h: number) => {
    const grad = g.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, '#eef4f9');
    grad.addColorStop(1, '#d6e3ef');
    g.fillStyle = grad;
    g.fillRect(0, 0, w, h);
    // Fila de indicadores.
    g.fillStyle = '#15191d';
    g.font = `700 ${3.6 * LK}px ${SANS}`;
    g.textBaseline = 'middle';
    g.textAlign = 'left';
    g.fillText(screen.top, 3 * LK, 4.5 * LK);
    g.textAlign = 'right';
    g.fillText(`${SET_TEMP}°C`, w - 3 * LK, 4.5 * LK);
    // Cifra grande: cuatro dígitos con su punto.
    const dh = 19 * LK;
    const dw = 9 * LK;
    const top = 9.5 * LK;
    let cx = 5 * LK;
    for (const ch of screen.big) {
      if (ch === '.') {
        g.fillRect(cx - 0.4 * LK, top + dh - 2 * LK, 2 * LK, 2 * LK);
        cx += 2.6 * LK;
      } else {
        drawSegmentDigit(g, ch, cx, top, dw, dh, 1.9 * LK);
        cx += dw + 2 * LK;
      }
    }
    // Unidad a la derecha de la cifra, sin tocarla.
    g.textAlign = 'left';
    g.textBaseline = 'alphabetic';
    g.font = `800 ${(screen.unit === 'g' ? 9 : 7) * LK}px ${SANS}`;
    g.fillText(screen.unit, cx + 1.5 * LK, top + dh, w - cx - 3 * LK);
    // Segunda línea en negativo.
    g.fillStyle = '#1d2a36';
    g.fillRect(0, h - 9 * LK, w, 9 * LK);
    g.fillStyle = '#e8f0f8';
    g.font = `700 ${4.4 * LK}px "Courier New", monospace`;
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText(screen.line, w / 2, h - 4.5 * LK, w - 4 * LK);
  };
  const lcdTex = canvasTexture(LCD.w * LK, LCD.h * LK, drawLcd);
  keep(lcdTex.texture);
  const lcdMat = keep(
    new THREE.MeshStandardMaterial({
      map: lcdTex.texture,
      roughness: 0.25,
      emissive: 0xffffff,
      emissiveMap: lcdTex.texture,
      emissiveIntensity: 0.55,
    }),
  );
  const lcdPos = mm(LCD.x + LCD.w / 2, LCD.y + LCD.h / 2);
  add(panel, new THREE.PlaneGeometry(LCD.w, LCD.h), lcdMat, lcdPos.x, lcdPos.y, 0.95);

  // Botón redondo a la derecha de la LCD.
  const btnPos = mm(ROUND_BTN.x, ROUND_BTN.y);
  const btnRing = add(panel, new THREE.CylinderGeometry(7, 7.4, 1.6, 40), btnRingMat, btnPos.x, btnPos.y, 1.4);
  btnRing.rotation.x = Math.PI / 2;
  const btnCore = add(panel, new THREE.CylinderGeometry(3.6, 3.6, 2, 32), btnCoreMat, btnPos.x, btnPos.y, 1.8);
  btnCore.rotation.x = Math.PI / 2;

  // Perilla moleteada bajo la parte derecha de la LCD.
  const knob = new THREE.Group();
  const knobPos = mm(KNOB.x, KNOB.y);
  knob.position.set(knobPos.x, knobPos.y, 0.85);
  panel.add(knob);
  const knurlGeo = keep(new THREE.CylinderGeometry(KNOB.r, KNOB.r + 0.4, 9, 60, 1, true));
  const kp = knurlGeo.attributes.position as THREE.BufferAttribute;
  for (let i = 0; i < kp.count; i++) {
    const k = 1 + 0.03 * Math.sign(Math.cos(Math.atan2(kp.getZ(i), kp.getX(i)) * 30));
    kp.setXYZ(i, kp.getX(i) * k, kp.getY(i), kp.getZ(i) * k);
  }
  knurlGeo.computeVertexNormals();
  const knurl = new THREE.Mesh(knurlGeo, knobMat);
  knurl.rotation.x = Math.PI / 2;
  knurl.position.z = 4.5;
  knob.add(knurl);
  const knobTop = add(knob, new THREE.CylinderGeometry(KNOB.r - 0.6, KNOB.r, 1.6, 48), knobTopMat, 0, 0, 9.6);
  knobTop.rotation.x = Math.PI / 2;
  const dimple = add(knob, new THREE.CylinderGeometry(1.4, 1.4, 0.6, 16), markMat, 0, KNOB.r - 3.6, 10.5);
  dimple.rotation.x = Math.PI / 2;

  // ── Tapa: articulada atrás, a la altura del suelo de la cámara ─────────────
  const lid = new THREE.Group();
  lid.position.copy(HINGE);
  lifted.add(lid);
  /** Geometría construida en coordenadas del equipo y llevada al marco de la bisagra. */
  const inLid = (geo: THREE.BufferGeometry) => geo.translate(-HINGE.x, -HINGE.y, -HINGE.z);
  /** Posición en coordenadas del equipo → marco de la bisagra. */
  const at = (x: number, y: number, z: number) => [x - HINGE.x, y - HINGE.y, z - HINGE.z] as const;
  const hoodAt = (z: number): Section => ({
    yb: DECK + 0.3,
    yt: hoodTop(z),
    hw: plan(HOOD_HW, z, HOOD_BACK, HOOD_FRONT, 26, 10),
  });
  const hood = add(lid, inLid(loft(HOOD_BACK, HOOD_FRONT, hoodAt, { rTop: 22, rBot: 3, steps: 72 })), bodyMat);
  hood.castShadow = true;
  // Cubierta negra del calefactor: dobla sobre el borde delantero.
  const coverAt = (z: number): Section => ({
    yb: coverTop(z) - 14,
    yt: coverTop(z),
    hw: plan(COVER_HW, z, -146, HOOD_FRONT + 3, 12, 6),
  });
  const cover = add(lid, inLid(loft(-146, HOOD_FRONT + 3, coverAt, { rTop: 4, rBot: 1, steps: 56 })), coverMat);
  cover.castShadow = true;
  // Aletas transversales en la parte trasera de la cubierta.
  const finGeo = keep(new THREE.BoxGeometry(2 * COVER_HW - 12, FIN_H * 2, 3.2));
  for (let z = -122; z <= -10; z += 8) {
    const fin = new THREE.Mesh(finGeo, coverMat);
    fin.position.set(...at(0, coverTop(z), z));
    lid.add(fin);
  }
  // Triángulo amarillo de superficie caliente, en la parte lisa.
  const warnTex = canvasTexture(256, 256, (g, w, h) => {
    g.clearRect(0, 0, w, h);
    const tri = (inset: number) => {
      g.beginPath();
      g.moveTo(w / 2, inset * 1.15);
      g.lineTo(w - inset, h - inset * 0.85);
      g.lineTo(inset, h - inset * 0.85);
      g.closePath();
    };
    g.fillStyle = '#1a1a1a';
    tri(8);
    g.fill();
    g.fillStyle = '#f7d117';
    tri(26);
    g.fill();
    g.strokeStyle = '#1a1a1a';
    g.lineWidth = 9;
    for (const dx of [-26, 0, 26]) {
      g.beginPath();
      g.moveTo(w / 2 + dx, 196);
      g.bezierCurveTo(w / 2 + dx - 14, 172, w / 2 + dx + 14, 150, w / 2 + dx, 120);
      g.stroke();
    }
    g.lineWidth = 8;
    g.beginPath();
    g.moveTo(w / 2 - 44, 206);
    g.lineTo(w / 2 + 44, 206);
    g.stroke();
  });
  keep(warnTex.texture);
  const warnMat = keep(new THREE.MeshStandardMaterial({ map: warnTex.texture, transparent: true, roughness: 0.4, depthWrite: false }));
  const warn = add(lid, new THREE.PlaneGeometry(26, 26), warnMat, ...at(0, coverTop(34) + 0.3, 34));
  warn.rotation.x = -Math.PI / 2;
  // Rejillas en la cara delantera de la tapa, junto a los bordes.
  const ventTex = canvasTexture(512, 128, (g, w, h) => {
    g.clearRect(0, 0, w, h);
    g.fillStyle = '#b4b8bb';
    for (const x0 of [22, w - 22 - 30]) for (let i = 0; i < 8; i++) g.fillRect(x0, 18 + i * 12, 30, 4);
  });
  keep(ventTex.texture);
  const ventMat = keep(new THREE.MeshStandardMaterial({ map: ventTex.texture, transparent: true, roughness: 0.6, depthWrite: false }));
  add(lid, new THREE.PlaneGeometry(2 * HOOD_HW - 20, 40), ventMat, ...at(0, DECK + 22, HOOD_FRONT + 0.3));
  // Lengüeta gris al pie del frente.
  add(lid, new THREE.BoxGeometry(26, 6, 7), trimMat, ...at(0, DECK + 4, HOOD_FRONT + 2));

  // Interior: recinto oscuro, reflector y tres varillas de fibra de carbono.
  add(lid, new THREE.BoxGeometry(2 * HOOD_HW - 20, 0.8, 180), innerMat, ...at(0, DECK - 0.2, PAN_Z));
  add(lid, new THREE.BoxGeometry(150, 1.6, 120), reflectorMat, ...at(0, DECK - 1.2, PAN_Z));
  const rodGeo = keep(new THREE.CylinderGeometry(3.4, 3.4, 140, 18));
  const clipGeo = keep(new THREE.BoxGeometry(9, 9, 11));
  for (const dz of [-34, 0, 34]) {
    const rod = new THREE.Mesh(rodGeo, rodMat);
    rod.rotation.z = Math.PI / 2;
    rod.position.set(...at(0, DECK - 6.5, PAN_Z + dz));
    lid.add(rod);
    for (const sx of [-1, 1]) {
      const clip = new THREE.Mesh(clipGeo, ceramicMat);
      clip.position.set(...at(sx * 72, DECK - 6, PAN_Z + dz));
      lid.add(clip);
    }
  }

  // ── Anclas y vistas ─────────────────────────────────────────────────────────
  const place = (parent: THREE.Object3D, x: number, y: number, z: number) => {
    const o = new THREE.Object3D();
    o.position.set(x, y, z);
    parent.add(o);
    return o;
  };
  const anchors = {
    pantalla: place(panel, lcdPos.x, lcdPos.y + 6, 4),
    perilla: place(panel, knobPos.x, knobPos.y, 13),
    // En la lengüeta del frente: con la tapa abierta queda arriba y a la vista.
    tapa: place(lid, ...at(0, DECK + 8, HOOD_FRONT + 6)),
    calefactor: place(lid, ...at(0, DECK - 12, PAN_Z)),
    plato: place(lifted, 0, PAN_Y + 12, PAN_Z),
  };
  // Puntos fijos que miran las vistas (la tapa se mueve; la cámara no la sigue).
  const panelFocus = place(panel, (lcdPos.x + knobPos.x) / 2, (lcdPos.y + knobPos.y) / 2 + 4, 0);
  const chamberFocus = place(lifted, 0, DECK + 95, PAN_Z - 10);
  // Centro del calefactor con la tapa abierta.
  const heaterOpen = new THREE.Vector3(0, DECK - 6.5, PAN_Z).sub(HINGE).applyAxisAngle(xAxis, -LID_OPEN).add(HINGE);
  const heaterFocus = place(lifted, heaterOpen.x, heaterOpen.y, heaterOpen.z);

  // ── Pantalla según el estado ────────────────────────────────────────────────
  const clock = (f: number) => {
    const s = Math.round(f * RUN_S);
    return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  };
  const methodOf = (angle: number) => METHODS[((Math.floor(angle / (Math.PI / 3)) % 2) + 2) % 2];
  const refreshScreen = (f: number) => {
    let next: Screen;
    if (powered) {
      const curve = (1 - Math.exp(-4 * f)) / (1 - Math.exp(-4));
      const mc = readout ?? MC_END * curve;
      const done = readout === null && f >= 0.999;
      const temp = Math.round(25 + (SET_TEMP - 25) * Math.min(1, heat * 1.2));
      next = {
        big: Math.min(99.99, Math.max(0, mc)).toFixed(2).padStart(5, ' '),
        unit: '%MC',
        top: done ? 'STD  FIN' : 'STD',
        line: done ? `Resultado  ${clock(1)}` : `${temp}°C   ${clock(f)}`,
      };
    } else if (knobOverride !== null) {
      next = { ...IDLE, top: 'MENÚ', line: `Programa: ${methodOf(knobOverride)}` };
    } else {
      next = IDLE;
    }
    const key = `${next.big}|${next.unit}|${next.top}|${next.line}`;
    if (key !== shown) {
      shown = key;
      screen = next;
      lcdTex.redraw();
    }
  };

  return {
    root,
    anchors,
    yaw: -0.42,
    frameTop: 140,
    views: {
      pantalla: {
        target: panelFocus,
        // La consola es casi horizontal: se mira desde adelante y arriba, a ~45°.
        direction: new THREE.Vector3(0.12, 0.66, 0.74).normalize(),
        distance: 420,
      },
      tapa: {
        target: chamberFocus,
        direction: new THREE.Vector3(-0.32, 0.5, 0.9).normalize(),
        distance: 860,
      },
      calefactor: {
        target: heaterFocus,
        direction: new THREE.Vector3(0.3, 0.05, 1).normalize(),
        distance: 560,
      },
    },

    get lidAngle() {
      return lidAngle;
    },
    setLidAngle(angle: number | null) {
      lidOverride = angle;
      if (angle !== null) {
        lidAngle = angle;
        lid.rotation.x = -angle;
      }
    },
    setKnob(angle: number | null) {
      knobOverride = angle;
    },
    setDrying(fraction: number | null) {
      dryOverride = fraction === null ? null : Math.min(1, Math.max(0, fraction));
    },
    setPower(on: boolean) {
      powered = on;
    },
    setReadout(percent: number | null) {
      readout = percent;
    },

    update(dt: number) {
      // Tapa: abierta mientras el equipo está en marcha.
      if (lidOverride === null) {
        const want = powered ? LID_OPEN : 0;
        lidAngle += (want - lidAngle) * Math.min(1, dt * 3.2);
        if (Math.abs(want - lidAngle) < 0.002) lidAngle = want;
        lid.rotation.x = -lidAngle;
      }

      // Calefactor: sólo al rojo con la tapa abierta y en marcha.
      const heatWant = powered && lidAngle > LID_OPEN * 0.6 ? 1 : 0;
      heat += (heatWant - heat) * Math.min(1, dt * (heatWant ? 2.2 : 1.2));
      if (heat < 0.002) heat = 0;
      rodMat.emissiveIntensity = heat * 1.4;
      rodMat.color.setRGB(0.17 + heat * 0.35, 0.15, 0.13);
      reflectorMat.emissiveIntensity = heat * 0.12;

      // Secado con el reloj propio (sin historia): avanza con el calefactor, se reinicia al apagar.
      if (powered && heat > 0.5) dry = Math.min(1, dry + dt / CLOCK_RUN);
      else if (!powered) dry = 0;
      const f = dryOverride ?? dry;

      // Muestra: visible con la tapa levantada; se aplana al secarse.
      sample.visible = lidAngle > 0.35;
      sample.scale.set(1, 1 - 0.35 * f, 1);

      // Perilla.
      knob.rotation.z = -(knobOverride ?? 0);

      refreshScreen(f);
    },

    dispose,
  };
}
