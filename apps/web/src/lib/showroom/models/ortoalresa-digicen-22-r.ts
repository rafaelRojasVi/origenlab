/**
 * Ortoalresa Digicen 22 R — centrífuga universal refrigerada de sobremesa.
 *
 * Modelada contra la fotografía del equipo real (foto del fabricante entregada
 * por el dueño, tapa abierta, vista de tres cuartos desde la izquierda) y una
 * segunda foto del fabricante de la misma familia (Consul 22, panel idéntico).
 *
 * Fuentes:
 *   ortoalresa.com/centrifugas/digicen-22-r/  (consultado 2026-11-08)
 *     → Dimensiones (a × l × h): 590 × 620 × 320 mm; peso neto 65 kg
 *     → 16 500 RPM / 26 480 ×g; pantalla TFT táctil a color
 *     → Rotores basculantes 4 × 100 ml (o hasta 28 × 15 ml) y angulares;
 *       sistema REI (cambio de rotor sin herramientas); cámara de acero inox.
 *   wp.ortoalresa.com/wp-content/uploads/2021/02/71.jpg (foto del panel: bisel,
 *     botones claros con icono oscuro, ranuras sobre el bisel).
 *
 * Estimaciones (medidas sobre la fotografía, escala por el ancho de 590 mm):
 *   · Cubierta superior (deck) a ~262 mm del suelo; tapa ~40 mm + tirador 14 mm
 *     → ~316 mm cerrada (ficha: 320 ✓). Patas de goma ~18 mm.
 *   · Frente en dos piezas gris claro abombadas en vertical (~9 mm): panel de
 *     rejilla a la izquierda (~35 % del ancho, 8 lamas) y bloque de control a la
 *     derecha, más alto que la cubierta (~22 mm) con orejas en las esquinas; la
 *     derecha (~50 mm más alta) envuelve el costado.
 *   · Bisel del panel ~218 × 206 mm; LCD ~106 × 76 mm; 4 botones Ø ~18 mm.
 *   · Rejilla lateral izquierda densa (ranuras verticales) ~220 × 138 mm.
 *   · Boca de la cámara: junta negra Ø ~364 mm (interior Ø ~304 mm), centrada
 *     ~30 mm a la derecha del centro y ~80 mm detrás; cuba negra ~170 mm de hondo.
 *   · Rotor basculante: cruz oscura, 4 cubetas Ø ~62 mm a r ≈ 70 mm, aro rojo
 *     y cúpula transparente; pomo central rojo. Los códigos de la pantalla
 *     (RT 316, AE 342) se leen borrosos en la foto: son ilustrativos.
 *   · Tapa ~524 × 492 mm, abre ~66°; dos pernos de cierre metálicos en las
 *     esquinas del borde libre, hoyuelo central, amortiguador junto a la bisagra.
 *
 * Comportamiento: tapa abierta en reposo (se ve el rotor). Al «Centrifugar» el
 * rotor gira (las cubetas se abren un poco con la velocidad); al detener, el
 * rotor frena. `setLid` permite a la historia fijar la tapa (abrirla o cerrarla).
 */
import * as THREE from 'three';
import type { BuiltModel } from '../types';
import { SANS, canvasTexture, lathe, disposer } from './kit';

// ── dimensiones (mm) ─────────────────────────────────────────────────────────
const W = 590;
const HW = W / 2;
const Z_BACK = -310;
const Z_BODY_FRONT = 236; // frente del cuerpo oscuro, tras la fascia
const H_FOOT = 18;
const DECK = 262; // cubierta superior

// cámara
const ZC = -80;
const CX = 30; // la cámara (y la tapa) va algo a la derecha del centro, como en la foto
const GASKET_R = 182;
const BOWL_R = 152;
const BOWL_D = 172;

// rotor
const PIVOT_Y = DECK - 22;
const PIVOT_R = 70;
const BUCKET_R = 31;
const DOME_R = 31;
const SWING_MAX = 0.3; // apertura de las cubetas a plena marcha (rad)
const SPIN = 7; // rad/s a plena marcha (visible, no la real)

// tapa
const LID_W = 524;
const LID_D = 492;
const LID_H = 40;
const HINGE_Z = -298;
const LID_ANGLE = THREE.MathUtils.degToRad(66);

// frente abombado (z del frente según la altura)
const fRight = (y: number) => 306 + 9 * Math.sin(Math.PI * THREE.MathUtils.clamp((y - 22) / 262, 0, 1));
const fLeft = (y: number) => 298 + 9 * Math.sin(Math.PI * THREE.MathUtils.clamp((y - 22) / 236, 0, 1));

// colores
const C_BODY = 0x323438;
const C_FASCIA = 0xbfbdb7;
const C_LID = 0xbebebb;

export interface DigicenModel extends BuiltModel {
  /** Fija la apertura de la tapa (0 cerrada … 1 abierta); `null` vuelve a su animación. */
  setLid(open: number | null): void;
}

/** Rectángulo redondeado en el plano de una forma, radio por esquina (v0 abajo). */
function rr(x0: number, x1: number, v0: number, v1: number, r: [number, number, number, number]) {
  const [a, b, c, d] = r;
  const s = new THREE.Shape();
  s.moveTo(x0 + a, v0);
  s.lineTo(x1 - b, v0);
  s.quadraticCurveTo(x1, v0, x1, v0 + b);
  s.lineTo(x1, v1 - c);
  s.quadraticCurveTo(x1, v1, x1 - c, v1);
  s.lineTo(x0 + d, v1);
  s.quadraticCurveTo(x0, v1, x0, v1 - d);
  s.lineTo(x0, v0 + a);
  s.quadraticCurveTo(x0, v0, x0 + a, v0);
  return s;
}

/**
 * Losa en planta: contorno (x, z) con radios [delante-izq, delante-der,
 * detrás-der, detrás-izq], de y0 a y0 + h, cantos redondeados `b`.
 */
function plan(
  x0: number, x1: number, z0: number, z1: number, r: [number, number, number, number],
  y0: number, h: number, b: number, hole?: { x: number; z: number; r: number },
) {
  const s = rr(x0 + b, x1 - b, -z1 + b, -z0 - b, r);
  if (hole) {
    const p = new THREE.Path();
    p.absarc(hole.x, -hole.z, hole.r, 0, Math.PI * 2, true);
    s.holes.push(p);
  }
  const g = new THREE.ExtrudeGeometry(s, {
    depth: Math.max(h - 2 * b, 0.1), bevelEnabled: b > 0, bevelSize: b, bevelThickness: b,
    bevelSegments: 4, curveSegments: hole ? 96 : 12,
  });
  g.rotateX(-Math.PI / 2);
  g.translate(0, y0 + b, 0);
  return g;
}

/** Pieza de frente: perfil lateral (detrás plano, delante según `front`) extruido en x. */
function profile(x0: number, x1: number, zBack: number, y0: number, y1: number, front: (y: number) => number, b: number) {
  const s = new THREE.Shape();
  const N = 16;
  s.moveTo(-(zBack + b), y0 + b);
  for (let i = 0; i <= N; i++) {
    const y = y0 + b + ((y1 - y0 - 2 * b) * i) / N;
    s.lineTo(-(front(y) - b), y);
  }
  s.lineTo(-(zBack + b), y1 - b);
  s.lineTo(-(zBack + b), y0 + b);
  const g = new THREE.ExtrudeGeometry(s, {
    depth: x1 - x0 - 2 * b, bevelEnabled: true, bevelSize: b, bevelThickness: b, bevelSegments: 4, curveSegments: 8,
  });
  g.rotateY(Math.PI / 2);
  g.translate(x0 + b, 0, 0);
  return g;
}

/**
 * Parte los triángulos hasta que ningún lado pase de `max` mm, para que una
 * pieza plana se pueda curvar con `onFront` (la tapa de un extruido sólo tiene
 * vértices en el contorno). Sólo posiciones: para piezas sin textura.
 */
function fine(src: THREE.BufferGeometry, max = 10) {
  const g = src.index ? src.toNonIndexed() : src;
  const p = g.attributes.position;
  const out: number[] = [];
  const a = new THREE.Vector3();
  const b = new THREE.Vector3();
  const c = new THREE.Vector3();
  const split = (a: THREE.Vector3, b: THREE.Vector3, c: THREE.Vector3, depth: number) => {
    const ab = a.distanceTo(b);
    const bc = b.distanceTo(c);
    const ca = c.distanceTo(a);
    const m = Math.max(ab, bc, ca);
    if (m <= max || depth > 10) { out.push(a.x, a.y, a.z, b.x, b.y, b.z, c.x, c.y, c.z); return; }
    if (m === ab) { const d = a.clone().lerp(b, 0.5); split(a, d, c, depth + 1); split(d, b, c, depth + 1); }
    else if (m === bc) { const d = b.clone().lerp(c, 0.5); split(a, b, d, depth + 1); split(a, d, c, depth + 1); }
    else { const d = c.clone().lerp(a, 0.5); split(a, b, d, depth + 1); split(d, b, c, depth + 1); }
  };
  for (let i = 0; i < p.count; i += 3) {
    split(a.fromBufferAttribute(p, i).clone(), b.fromBufferAttribute(p, i + 1).clone(), c.fromBufferAttribute(p, i + 2).clone(), 0);
  }
  if (g !== src) g.dispose();
  src.dispose();
  const res = new THREE.BufferGeometry();
  res.setAttribute('position', new THREE.Float32BufferAttribute(out, 3));
  return res;
}

/** Lleva una pieza plana (en z = 0, ya situada en x, y) sobre el frente abombado. */
function onFront<T extends THREE.BufferGeometry>(g: T, front: (y: number) => number, dz: number): T {
  const p = g.attributes.position;
  for (let i = 0; i < p.count; i++) p.setZ(i, p.getZ(i) + front(p.getY(i)) + dz);
  p.needsUpdate = true;
  g.computeVertexNormals();
  return g;
}

/** Lente (ranura o lama) de ancho w y alto h, centrada en el origen. */
function lens(w: number, h: number) {
  const s = new THREE.Shape();
  s.moveTo(-w / 2, 0);
  s.quadraticCurveTo(0, h, w / 2, 0);
  s.quadraticCurveTo(0, -h, -w / 2, 0);
  return s;
}

export function build(): DigicenModel {
  const { keep, dispose } = disposer();
  const root = new THREE.Group();
  const add = (parent: THREE.Object3D, geo: THREE.BufferGeometry, mat: THREE.Material, x = 0, y = 0, z = 0) => {
    const m = new THREE.Mesh(keep(geo), mat);
    m.position.set(x, y, z);
    m.castShadow = true;
    m.receiveShadow = true;
    parent.add(m);
    return m;
  };

  // ── materiales ─────────────────────────────────────────────────────────────
  const bodyMat = keep(new THREE.MeshPhysicalMaterial({ color: C_BODY, roughness: 0.62, metalness: 0.08, clearcoat: 0.12, clearcoatRoughness: 0.7 }));
  const fasciaMat = keep(new THREE.MeshPhysicalMaterial({ color: C_FASCIA, roughness: 0.46, metalness: 0.02, clearcoat: 0.3, clearcoatRoughness: 0.5 }));
  const lidMat = keep(new THREE.MeshPhysicalMaterial({ color: C_LID, roughness: 0.42, metalness: 0.03, clearcoat: 0.35, clearcoatRoughness: 0.45 }));
  const lidInnerMat = keep(new THREE.MeshPhysicalMaterial({ color: 0xbcbdbc, roughness: 0.55, metalness: 0.03, side: THREE.DoubleSide }));
  const slotMat = keep(new THREE.MeshStandardMaterial({ color: 0x1b1c1f, roughness: 0.9, side: THREE.DoubleSide }));
  const rubberMat = keep(new THREE.MeshStandardMaterial({ color: 0x121315, roughness: 0.92, side: THREE.DoubleSide }));
  const footMat = keep(new THREE.MeshStandardMaterial({ color: 0x34363a, roughness: 0.85 }));
  const bowlMat = keep(new THREE.MeshPhysicalMaterial({ color: 0x18191b, roughness: 0.5, metalness: 0.2, side: THREE.DoubleSide }));
  const carrierMat = keep(new THREE.MeshPhysicalMaterial({ color: 0x2a2b2f, roughness: 0.4, metalness: 0.55 }));
  // en el fondo de la cuba negra el aluminio de las cubetas se ve oscuro
  const alumMat = keep(new THREE.MeshPhysicalMaterial({ color: 0x8e9398, roughness: 0.38, metalness: 0.85 }));
  const chromeMat = keep(new THREE.MeshPhysicalMaterial({ color: 0xd8dde2, roughness: 0.16, metalness: 1 }));
  const redMat = keep(new THREE.MeshPhysicalMaterial({ color: 0xcf1b22, roughness: 0.32, metalness: 0.05, clearcoat: 0.6, clearcoatRoughness: 0.3 }));
  const capMat = keep(new THREE.MeshPhysicalMaterial({ color: 0xa2a6aa, roughness: 0.5, metalness: 0.05 }));
  const domeMat = keep(new THREE.MeshPhysicalMaterial({
    color: 0xeef6fb, roughness: 0.04, clearcoat: 1, clearcoatRoughness: 0.03, envMapIntensity: 2,
    transparent: true, opacity: 0.4, side: THREE.DoubleSide, depthWrite: false,
  }));
  const bezelMat = keep(new THREE.MeshPhysicalMaterial({ color: 0x46484d, roughness: 0.4, metalness: 0.05, clearcoat: 0.5, clearcoatRoughness: 0.3 }));
  const lineMat = keep(new THREE.MeshStandardMaterial({ color: 0xb9bbbd, roughness: 0.5 }));
  const lcdFrameMat = keep(new THREE.MeshPhysicalMaterial({ color: 0x141518, roughness: 0.2, clearcoat: 1 }));
  const hingeMat = keep(new THREE.MeshStandardMaterial({ color: 0x1a1b1e, roughness: 0.5, metalness: 0.2 }));

  // ── cuerpo oscuro: un bloque con la boca de la cámara ──────────────────────
  const body = new THREE.Group();
  root.add(body);
  add(body, plan(-HW, HW, Z_BACK, Z_BODY_FRONT, [6, 6, 16, 16], H_FOOT, DECK - H_FOOT, 6, { x: CX, z: ZC, r: GASKET_R }), bodyMat);

  // patas de goma
  const footGeo = keep(new THREE.CylinderGeometry(24, 28, H_FOOT, 28));
  for (const [fx, fz] of [[-236, 262], [236, 262], [-236, -262], [236, -262]] as const) {
    const f = new THREE.Mesh(footGeo, footMat);
    f.position.set(fx, H_FOOT / 2, fz);
    body.add(f);
  }

  // ── cámara: cuba negra y junta gruesa ──────────────────────────────────────
  const bowl = add(body, lathe([
    [BOWL_R, 0], [BOWL_R, -BOWL_D + 26], [BOWL_R - 8, -BOWL_D + 8], [BOWL_R - 26, -BOWL_D], [0, -BOWL_D],
  ], 96), bowlMat, CX, DECK, ZC);
  bowl.castShadow = false;
  add(body, lathe([
    [BOWL_R - 1, -18], [BOWL_R - 1, -2], [BOWL_R + 2, 2.5], [BOWL_R + 9, 5], [GASKET_R - 9, 5], [GASKET_R - 2, 2.5], [GASKET_R + 1, -2], [GASKET_R + 1, -10],
  ], 120), rubberMat, CX, DECK, ZC);
  // tapa del eje en el fondo
  add(body, new THREE.CylinderGeometry(40, 46, 18, 40), carrierMat, CX, DECK - BOWL_D + 9, ZC);

  // ── frente gris claro ──────────────────────────────────────────────────────
  // panel izquierdo (rejilla), abombado en vertical, envuelve la esquina
  add(body, profile(-HW, -94, 228, 22, 258, fLeft, 9), fasciaMat);
  // bloque de control, más alto que la cubierta
  add(body, profile(-91, HW, 228, 22, 284, fRight, 10), fasciaMat);
  // oreja izquierda del bloque y oreja derecha (más alta, envuelve el costado)
  add(body, profile(-91, -50, 228, 200, 292, fRight, 8), fasciaMat);
  add(body, profile(254, HW + 1, 224, 160, 302, (y) => fRight(Math.min(y, 284)), 16), fasciaMat);

  // lamas del panel izquierdo: 8 ranuras con labio superior
  const LOUVRE_X = -176;
  for (let i = 0; i < 8; i++) {
    const y = 52 + i * 17.5;
    const w = 150 - Math.abs(i - 3.5) * 1.5;
    const slot = new THREE.ShapeGeometry(lens(w, 7), 16);
    slot.translate(LOUVRE_X, y, 0);
    add(body, onFront(slot, fLeft, 0.6), slotMat);
    const lip = new THREE.ExtrudeGeometry(lens(w + 8, 6), { depth: 1.5, bevelEnabled: true, bevelSize: 1.5, bevelThickness: 1.5, bevelSegments: 2, curveSegments: 16 });
    lip.translate(LOUVRE_X, y + 4.5, -1);
    add(body, onFront(lip, fLeft, 0), fasciaMat);
  }

  // dos ranuras sobre el bisel
  for (const [y, w] of [[264, 54], [272, 46]] as const) {
    const slot = new THREE.ShapeGeometry(lens(w, 4.5), 16);
    slot.translate(97, y, 0);
    add(body, onFront(slot, fRight, 0.5), slotMat);
  }

  // rejilla lateral izquierda: ranuras verticales densas (refrigeración)
  const vent = canvasTexture(512, 320, (g, w, h) => {
    g.clearRect(0, 0, w, h);
    g.fillStyle = '#000';
    const cols = 30;
    const rows = 13;
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const x = 8 + (c * (w - 16)) / cols;
        const y = 6 + (r * (h - 12)) / rows;
        g.beginPath();
        g.roundRect(x + 4, y + 2, 6, (h - 12) / rows - 6, 3);
        g.fill();
      }
    }
  });
  keep(vent.texture);
  const ventPlane = new THREE.Mesh(
    keep(new THREE.PlaneGeometry(220, 138)),
    keep(new THREE.MeshStandardMaterial({ color: 0x0c0c0e, map: vent.texture, transparent: true, roughness: 0.9, depthWrite: false })),
  );
  ventPlane.rotation.y = -Math.PI / 2;
  ventPlane.position.set(-HW - 0.6, 124, 4);
  body.add(ventPlane);

  // ── panel de control ───────────────────────────────────────────────────────
  const PX = 97; // centro del bisel
  const PY = 153;
  const flat = (shape: THREE.Shape, x: number, y: number, depth = 0) => {
    const g = depth > 0
      ? new THREE.ExtrudeGeometry(shape, { depth, bevelEnabled: false, curveSegments: 10 })
      : new THREE.ShapeGeometry(shape, 10);
    g.translate(x, y, 0);
    return fine(g);
  };
  // filete claro del bisel y bisel oscuro
  add(body, onFront(flat(rr(-112, 112, -106, 106, [13, 13, 13, 13]), PX, PY, 1.2), fRight, -0.4), lineMat);
  add(body, onFront(flat(rr(-109, 109, -103, 103, [12, 12, 12, 12]), PX, PY, 2), fRight, -0.2), bezelMat);
  // línea fina interior del bisel
  const inner = rr(-104, 104, -98, 98, [10, 10, 10, 10]);
  inner.holes.push(rr(-103, 103, -97, 97, [9, 9, 9, 9]));
  add(body, onFront(flat(inner, PX, PY), fRight, 1.9), lineMat);

  // pantalla TFT (lienzo) con su marco negro
  const LCD_X = PX - 4;
  const LCD_Y = 181;
  const LCD_W = 106;
  const LCD_H = 76;
  add(body, onFront(flat(rr(-58, 58, -43, 43, [3, 3, 3, 3]), LCD_X, LCD_Y, 1), fRight, 1.9), lcdFrameMat);

  let powered = false;
  let screenOn = false;
  let rpm = 0;
  let temp = 22;
  let runTime = 0;
  let lastDraw = -1;
  const DISP_RPM = 4000; // ilustrativo (máx. 16 500)
  const DISP_TEMP = 4;
  const RUN_S = 10 * 60;

  const screen = canvasTexture(1024, 736, (g, w, h) => {
    const u = w / 512;
    if (!screenOn) {
      g.fillStyle = '#0b0d10';
      g.fillRect(0, 0, w, h);
      return;
    }
    // fondo claro con retícula tenue
    g.fillStyle = '#e6ecee';
    g.fillRect(0, 0, w, h);
    g.strokeStyle = 'rgba(120,140,150,0.12)';
    g.lineWidth = u;
    for (let x = 0; x < w; x += 16 * u) { g.beginPath(); g.moveTo(x, 40 * u); g.lineTo(x, h); g.stroke(); }
    // barra superior: tres iconos a la derecha (el último rojo) y la temperatura
    g.fillStyle = '#d3d9dc';
    g.fillRect(0, 0, w, 38 * u);
    ['#eef1f2', '#eef1f2', '#d3352b'].forEach((c, i) => {
      const x = w - (146 - i * 46) * u;
      g.fillStyle = '#6f7479';
      g.beginPath(); g.roundRect(x, 5 * u, 40 * u, 28 * u, 4 * u); g.fill();
      g.fillStyle = c;
      g.beginPath(); g.arc(x + 20 * u, 19 * u, 8 * u, 0, Math.PI * 2); g.fill();
    });
    g.fillStyle = '#2f5d8a';
    g.font = `700 ${18 * u}px ${SANS}`;
    g.textBaseline = 'middle';
    g.textAlign = 'left';
    g.fillText(`${temp.toFixed(1)} °C`, 12 * u, 20 * u);

    const left = Math.max(0, RUN_S - runTime);
    const mm = Math.floor(left / 60);
    const ss = Math.floor(left % 60);
    const rcf = Math.round(1.118e-6 * 150 * rpm * rpm);
    const leftCol: [string, string][] = [
      ['Prog:', '000'], ['Rotor:', 'RT 316'], ['RPM:', `${Math.round(rpm / 10) * 10}`], ['Accel:', '9'],
      ['Time:', `${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}`],
    ];
    const rightCol: [string, string][] = [['Adapt:', 'AE 342'], ['RCF:', `${rcf} xg`], ['Decel:', '9'], ['Temp:', `${DISP_TEMP} °C`]];
    const row = (label: string, value: string, x: number, y: number, boxW: number) => {
      g.fillStyle = '#3d4a57';
      g.font = `500 ${19 * u}px ${SANS}`;
      g.textAlign = 'left';
      g.fillText(label, x, y);
      g.fillStyle = '#ffffff';
      g.strokeStyle = '#b8c2c8';
      g.lineWidth = 1.5 * u;
      g.beginPath(); g.roundRect(x + 64 * u, y - 15 * u, boxW, 30 * u, 3 * u); g.fill(); g.stroke();
      g.fillStyle = '#111518';
      g.font = `700 ${19 * u}px ${SANS}`;
      g.fillText(value, x + 71 * u, y + 1 * u);
    };
    leftCol.forEach(([l, v], i) => row(l, v, 14 * u, (66 + i * 50) * u, 112 * u));
    rightCol.forEach(([l, v], i) => row(l, v, 262 * u, (66 + i * 50) * u, 120 * u));
    // barra roja de estado
    g.fillStyle = '#d8281f';
    g.fillRect(0, h - 40 * u, w, 40 * u);
    g.fillStyle = '#ffffff';
    g.font = `700 ${17 * u}px ${SANS}`;
    g.textAlign = 'left';
    g.fillText(rpm > 30 ? 'Centrifugando' : 'Abierta', 12 * u, h - 20 * u);
    g.textAlign = 'right';
    g.fillText('Work>>Edit', w - 12 * u, h - 20 * u);
    // avance
    g.fillStyle = 'rgba(255,255,255,0.35)';
    g.fillRect(160 * u, h - 24 * u, 180 * u, 8 * u);
    g.fillStyle = '#ffffff';
    g.fillRect(160 * u, h - 24 * u, 180 * u * Math.min(1, runTime / RUN_S), 8 * u);
  });
  keep(screen.texture);
  const screenMat = keep(new THREE.MeshStandardMaterial({
    map: screen.texture, emissive: 0xffffff, emissiveMap: screen.texture, emissiveIntensity: 0, roughness: 0.35,
  }));
  const lcdGeo = new THREE.PlaneGeometry(LCD_W, LCD_H, 1, 8);
  lcdGeo.translate(LCD_X, LCD_Y, 0);
  add(body, onFront(lcdGeo, fRight, 3.1), screenMat);

  // cuatro botones claros con icono oscuro
  const ICONS: ((g: CanvasRenderingContext2D, s: number) => void)[] = [
    (g, s) => { // marcha / paro
      g.beginPath(); g.moveTo(s * 0.3, s * 0.3); g.lineTo(s * 0.7, s * 0.7); g.moveTo(s * 0.7, s * 0.3); g.lineTo(s * 0.3, s * 0.7); g.stroke();
      g.beginPath(); g.moveTo(s * 0.5, s * 0.22); g.lineTo(s * 0.62, s * 0.38); g.lineTo(s * 0.38, s * 0.38); g.fill();
    },
    (g, s) => { // abrir tapa
      g.beginPath(); g.roundRect(s * 0.28, s * 0.45, s * 0.44, s * 0.26, s * 0.03); g.fill();
      g.beginPath(); g.moveTo(s * 0.3, s * 0.42); g.lineTo(s * 0.68, s * 0.3); g.stroke();
    },
    (g, s) => { g.fillRect(s * 0.33, s * 0.33, s * 0.34, s * 0.34); }, // paro
    (g, s) => { // inicio
      g.beginPath(); g.moveTo(s * 0.5, s * 0.25); g.lineTo(s * 0.76, s * 0.5); g.lineTo(s * 0.68, s * 0.5); g.lineTo(s * 0.68, s * 0.74);
      g.lineTo(s * 0.32, s * 0.74); g.lineTo(s * 0.32, s * 0.5); g.lineTo(s * 0.24, s * 0.5); g.closePath(); g.fill();
    },
  ];
  const btnGeo = keep(new THREE.CylinderGeometry(9, 9.5, 3, 32));
  const btnFaceGeo = keep(new THREE.CircleGeometry(9, 32));
  ICONS.forEach((icon, i) => {
    const tex = canvasTexture(128, 128, (g, s) => {
      g.fillStyle = '#e7e7e5';
      g.fillRect(0, 0, s, s);
      g.fillStyle = '#26282c';
      g.strokeStyle = '#26282c';
      g.lineWidth = s * 0.07;
      g.lineCap = 'round';
      icon(g, s);
    });
    keep(tex.texture);
    const x = [60, 90, 119, 148][i];
    const y = 121;
    const z = fRight(y) + 2.2;
    const btn = new THREE.Mesh(btnGeo, capMat);
    btn.rotation.x = Math.PI / 2;
    btn.position.set(x, y, z + 1.5);
    body.add(btn);
    const face = new THREE.Mesh(btnFaceGeo, keep(new THREE.MeshStandardMaterial({ map: tex.texture, roughness: 0.45 })));
    face.position.set(x, y, z + 3.1);
    body.add(face);
  });

  // rótulos: «digicen 22 R» en su placa gris y «orto alresa» en claro
  const label = canvasTexture(512, 112, (g, w, h) => {
    g.clearRect(0, 0, w, h);
    g.fillStyle = '#8c8e92';
    g.beginPath(); g.roundRect(0, 0, w, h, 18); g.fill();
    g.fillStyle = '#f4f4f2';
    g.font = `600 64px ${SANS}`;
    g.textBaseline = 'middle';
    g.fillText('digicen 22 R', 26, h / 2 + 3);
  });
  keep(label.texture);
  const labelGeo = new THREE.PlaneGeometry(52, 12.5, 1, 2);
  labelGeo.translate(40, 71, 0);
  add(body, onFront(labelGeo, fRight, 2.4), keep(new THREE.MeshStandardMaterial({ map: label.texture, transparent: true, roughness: 0.5 })));
  const logo = canvasTexture(512, 256, (g, w, h) => {
    g.clearRect(0, 0, w, h);
    g.fillStyle = '#f2f2f0';
    g.textAlign = 'right';
    g.textBaseline = 'alphabetic';
    g.font = `700 78px ${SANS}`;
    g.fillText('orto', w - 20, h * 0.42);
    g.font = `400 112px ${SANS}`;
    g.fillText('alresa', w - 16, h * 0.86);
  });
  keep(logo.texture);
  const logoGeo = new THREE.PlaneGeometry(52, 26, 1, 2);
  logoGeo.translate(172, 102, 0);
  add(body, onFront(logoGeo, fRight, 2.4), keep(new THREE.MeshStandardMaterial({ map: logo.texture, transparent: true, roughness: 0.5 })));

  // ── rotor basculante ───────────────────────────────────────────────────────
  const rotor = new THREE.Group();
  rotor.name = 'rotor';
  rotor.position.set(CX, PIVOT_Y, ZC);
  rotor.rotation.y = Math.PI / 4;
  body.add(rotor);

  add(rotor, new THREE.CylinderGeometry(34, 36, 18, 40), carrierMat, 0, -4, 0);
  add(rotor, new THREE.CylinderGeometry(27, 27, 3, 40), chromeMat, 0, 6.5, 0);
  // pomo central rojo (carrete): base, cuello y sombrero
  add(rotor, lathe([
    [0, 8], [20, 8], [21, 10], [21, 19], [18, 21], [8.5, 22], [8, 25], [8, 40], [9.5, 42], [20, 43], [22, 45], [22, 54], [20, 56], [0, 56],
  ], 48), redMat);

  // placa en cruz bajo los aros rojos: tapa los cuerpos de las cubetas, como en la foto
  const plate = new THREE.Shape();
  for (let k = 0; k <= 96; k++) {
    const th = (k / 96) * Math.PI * 2;
    const rad = 60 + 50 * Math.pow(Math.cos(2 * th), 2);
    if (k === 0) plate.moveTo(rad * Math.cos(th), rad * Math.sin(th));
    else plate.lineTo(rad * Math.cos(th), rad * Math.sin(th));
  }
  for (let k = 0; k < 4; k++) {
    const h = new THREE.Path();
    h.absarc(PIVOT_R * Math.cos((k * Math.PI) / 2), PIVOT_R * Math.sin((k * Math.PI) / 2), BUCKET_R + 3, 0, Math.PI * 2, true);
    plate.holes.push(h);
  }
  const plateGeo = new THREE.ExtrudeGeometry(plate, { depth: 8, bevelEnabled: true, bevelSize: 2, bevelThickness: 2, bevelSegments: 2, curveSegments: 32 });
  plateGeo.rotateX(-Math.PI / 2);
  add(rotor, plateGeo, carrierMat, 0, -4, 0);

  const armGeo = keep(new THREE.BoxGeometry(30, 14, PIVOT_R - 30));
  const prongGeo = keep(new THREE.BoxGeometry(10, 26, 50));
  const bridgeGeo = keep(new THREE.BoxGeometry(2 * (BUCKET_R + 12), 14, 10));
  const pinGeo = keep(new THREE.CylinderGeometry(4, 4, 8, 14));
  const bucketGeo = keep(lathe([[0, -90], [18, -90], [25, -85], [BUCKET_R - 1, -74], [BUCKET_R, -60], [BUCKET_R, 11], [0, 11]], 40));
  const ringGeo = keep(lathe([[BUCKET_R - 2, 10], [BUCKET_R + 1.5, 10], [BUCKET_R + 2, 12], [BUCKET_R + 2, 17], [BUCKET_R + 1.5, 18], [BUCKET_R - 2, 18]], 40));
  const bandGeo = keep(new THREE.CylinderGeometry(BUCKET_R + 0.8, BUCKET_R + 0.8, 3, 40, 1, true));
  const domeGeo = keep(new THREE.SphereGeometry(DOME_R, 36, 16, 0, Math.PI * 2, 0, Math.PI / 2));
  const capGeo = keep(new THREE.CylinderGeometry(21, 21, 20, 28));
  const capTopGeo = keep(new THREE.CylinderGeometry(17, 21, 4, 28));
  // la cúpula transparente: un tramo recto y la media esfera
  const domeWallGeo = keep(new THREE.CylinderGeometry(DOME_R, DOME_R, 9, 40, 1, true));
  const buckets: THREE.Group[] = [];
  for (let i = 0; i < 4; i++) {
    const arm = new THREE.Group();
    arm.rotation.y = (i * Math.PI) / 2;
    rotor.add(arm);
    const a = new THREE.Mesh(armGeo, carrierMat);
    a.position.set(0, -2, 15 + (PIVOT_R - 30) / 2);
    arm.add(a);
    const br = new THREE.Mesh(bridgeGeo, carrierMat);
    br.position.set(0, -4, PIVOT_R - 30);
    arm.add(br);
    for (const sx of [-1, 1]) {
      const p = new THREE.Mesh(prongGeo, carrierMat);
      p.position.set(sx * (BUCKET_R + 7), -4, PIVOT_R - 4);
      arm.add(p);
      const pin = new THREE.Mesh(pinGeo, chromeMat);
      pin.rotation.z = Math.PI / 2;
      pin.position.set(sx * (BUCKET_R + 1), 0, PIVOT_R);
      arm.add(pin);
    }
    const bucket = new THREE.Group();
    bucket.position.set(0, 0, PIVOT_R);
    arm.add(bucket);
    buckets.push(bucket);
    bucket.add(new THREE.Mesh(bucketGeo, alumMat));
    bucket.add(new THREE.Mesh(ringGeo, redMat));
    const band = new THREE.Mesh(bandGeo, rubberMat);
    band.position.y = 19.5;
    bucket.add(band);
    const cap = new THREE.Mesh(capGeo, capMat);
    cap.position.y = 19 + 11;
    bucket.add(cap);
    const capTop = new THREE.Mesh(capTopGeo, capMat);
    capTop.position.y = 19 + 23;
    bucket.add(capTop);
    const dome = new THREE.Mesh(domeGeo, domeMat);
    dome.position.y = 28;
    dome.renderOrder = 3;
    bucket.add(dome);
    const wall = new THREE.Mesh(domeWallGeo, domeMat);
    wall.position.y = 23.5;
    wall.renderOrder = 3;
    bucket.add(wall);
  }

  // ── tapa ───────────────────────────────────────────────────────────────────
  const lid = new THREE.Group();
  lid.position.set(CX, DECK, HINGE_Z);
  root.add(lid);
  const LHW = LID_W / 2;
  add(lid, plan(-LHW, LHW, 0, LID_D, [30, 30, 14, 14], 0, LID_H, 11), lidMat);
  // tirador: labio redondeado elevado en el borde libre
  add(lid, plan(-176, 176, LID_D - 50, LID_D + 8, [22, 22, 10, 10], 24, 30, 10), lidMat);

  // cara interior (mira al visitante con la tapa abierta)
  const lidFace = (shape: THREE.Shape, mat: THREE.Material, y: number) => {
    const g = new THREE.ShapeGeometry(shape, 10);
    g.rotateX(Math.PI / 2);
    return add(lid, g, mat, 0, y, 0);
  };
  lidFace(rr(-LHW + 16, LHW - 16, 18, LID_D - 18, [20, 20, 10, 10]), lidInnerMat, -0.4);
  const groove = rr(-LHW + 40, LHW - 40, 40, LID_D - 46, [12, 12, 12, 12]);
  groove.holes.push(rr(-LHW + 43, LHW - 43, 43, LID_D - 49, [10, 10, 10, 10]));
  lidFace(groove, slotMat, -0.7);
  // hoyuelo central con su tornillo
  add(lid, new THREE.CylinderGeometry(13, 10, 2, 32), slotMat, 0, -0.6, LID_D * 0.52);
  add(lid, new THREE.CylinderGeometry(5, 5, 2.2, 20), chromeMat, 0, -1, LID_D * 0.52);
  // tornillos del borde
  const screwGeo = keep(new THREE.CylinderGeometry(3, 3, 1.6, 14));
  const screws: [number, number][] = [];
  for (const x of [-180, -60, 60, 180]) screws.push([x, LID_D - 30]);
  for (const z of [110, 250, 390]) screws.push([-LHW + 28, z], [LHW - 28, z]);
  for (const [x, z] of screws) {
    const s = new THREE.Mesh(screwGeo, slotMat);
    s.position.set(x, -0.9, z);
    lid.add(s);
  }
  // pernos de cierre metálicos en las esquinas del borde libre
  const boltGeo = keep(new THREE.CylinderGeometry(5, 5, 24, 18));
  const collarGeo = keep(new THREE.CylinderGeometry(9, 9, 4, 20));
  for (const x of [-LHW + 52, LHW - 52]) {
    const c = new THREE.Mesh(collarGeo, chromeMat);
    c.position.set(x, -2, LID_D - 46);
    lid.add(c);
    const b = new THREE.Mesh(boltGeo, chromeMat);
    b.position.set(x, -14, LID_D - 46);
    lid.add(b);
  }
  // bisagras negras: hoja en la tapa y bloque en la cubierta
  const leafGeo = keep(new THREE.BoxGeometry(46, 12, 34));
  const blockGeo = keep(new THREE.BoxGeometry(50, 22, 30));
  const barrelGeo = keep(new THREE.CylinderGeometry(6, 6, 54, 16).rotateZ(Math.PI / 2));
  for (const x of [-LHW + 46, LHW - 46]) {
    const leaf = new THREE.Mesh(leafGeo, hingeMat);
    leaf.position.set(x, -2, 14);
    lid.add(leaf);
    const block = new THREE.Mesh(blockGeo, hingeMat);
    block.position.set(CX + x, DECK + 8, HINGE_Z - 4);
    body.add(block);
    const barrel = new THREE.Mesh(barrelGeo, hingeMat);
    barrel.position.set(CX + x, DECK + 14, HINGE_Z + 2);
    body.add(barrel);
  }
  // amortiguador de gas junto a la bisagra derecha: cuerpo + vástago
  const strutX = LHW - 92;
  const strutA = new THREE.Vector3(CX + strutX, DECK + 6, HINGE_Z + 34);
  const strutBLocal = new THREE.Vector3(strutX, -4, 112);
  add(body, new THREE.BoxGeometry(18, 14, 22), hingeMat, CX + strutX, DECK + 5, HINGE_Z + 34);
  add(lid, new THREE.BoxGeometry(18, 8, 18), hingeMat, strutX, -4, 112);
  const strutBodyGeo = keep(new THREE.CylinderGeometry(7, 7, 1, 16));
  strutBodyGeo.translate(0, 0.5, 0);
  const strutRodGeo = keep(new THREE.CylinderGeometry(3, 3, 1, 12));
  strutRodGeo.translate(0, 0.5, 0);
  const strutBody = new THREE.Mesh(strutBodyGeo, hingeMat);
  const strutRod = new THREE.Mesh(strutRodGeo, chromeMat);
  body.add(strutBody, strutRod);
  const up = new THREE.Vector3(0, 1, 0);
  const tmpB = new THREE.Vector3();
  const tmpD = new THREE.Vector3();
  const placeStrut = () => {
    lid.updateMatrix();
    tmpB.copy(strutBLocal).applyMatrix4(lid.matrix);
    tmpD.subVectors(tmpB, strutA);
    const len = Math.max(tmpD.length(), 1);
    tmpD.normalize();
    strutBody.position.copy(strutA);
    strutBody.quaternion.setFromUnitVectors(up, tmpD);
    strutBody.scale.set(1, len * 0.55, 1);
    strutRod.position.copy(strutA);
    strutRod.quaternion.copy(strutBody.quaternion);
    strutRod.scale.set(1, len, 1);
  };

  // La escena encuadra la vista general con lo que mide el modelo al montarlo:
  // se construye con la tapa abierta para que quepa al abrirse; el primer
  // `update` la deja cerrada (reposo).
  lid.rotation.x = -LID_ANGLE;
  placeStrut();

  // ── anclas ─────────────────────────────────────────────────────────────────
  const screenAnchor = new THREE.Object3D();
  screenAnchor.position.set(LCD_X, LCD_Y, fRight(LCD_Y) + 4);
  body.add(screenAnchor);
  const rotorAnchor = new THREE.Object3D();
  rotorAnchor.position.set(CX, DECK + 20, ZC);
  body.add(rotorAnchor);
  const lidAnchor = new THREE.Object3D();
  lidAnchor.position.set(-LHW * 0.45, -3, LID_D * 0.7);
  lid.add(lidAnchor);
  const bodyAnchor = new THREE.Object3D();
  bodyAnchor.position.set(-HW - 4, 124, 4);
  body.add(bodyAnchor);

  // ── estado ─────────────────────────────────────────────────────────────────
  let lidOpen = 1; // en reposo la tapa está abierta: el rotor es lo que el dueño quiere mostrar
  let lidOverride: number | null = null;
  let swing = 0;
  const ease = (x: number) => x * x * (3 - 2 * x);

  return {
    root,
    anchors: { screen: screenAnchor, rotor: rotorAnchor, lid: lidAnchor, body: bodyAnchor },
    views: {
      pantalla: { target: screenAnchor, direction: new THREE.Vector3(0.1, 0.12, 1), distance: 420 },
      rotor: { target: rotorAnchor, direction: new THREE.Vector3(-0.25, 1.15, 0.85), distance: 760 },
      lateral: { target: bodyAnchor, direction: new THREE.Vector3(-1, 0.32, 0.42), distance: 900 },
    },
    // La vista general encuadra el equipo con la tapa abierta (reposo), punta incluida (~710 mm).
    frameTop: 740,
    frameMargin: 1.3,
    yaw: 0.56,

    setPower(on: boolean) {
      powered = on;
      if (on) { screenOn = true; runTime = 0; }
    },

    setLid(open: number | null) {
      lidOverride = open === null ? null : THREE.MathUtils.clamp(open, 0, 1);
    },

    update(dt: number, t: number, level: number) {
      // tapa: abierta en reposo y en marcha (muestra el rotor); la historia puede fijarla
      const want = lidOverride ?? 1;
      lidOpen += (want - lidOpen) * Math.min(1, dt * 2.4);
      if (Math.abs(want - lidOpen) < 1e-3) lidOpen = want;
      lid.rotation.x = -LID_ANGLE * ease(lidOpen);
      placeStrut();

      // rotor: gira con el nivel; las cubetas se abren un poco con la velocidad
      rotor.rotation.y += level * SPIN * dt;
      swing += (SWING_MAX * level * level - swing) * Math.min(1, dt * 3);
      for (const b of buckets) b.rotation.x = -swing;

      // pantalla
      if (powered || level > 0.01) screenOn = true;
      else screenOn = false;
      if (screenOn) {
        if (powered) runTime += dt;
        rpm += (DISP_RPM * level - rpm) * Math.min(1, dt * 4);
        temp += ((powered ? DISP_TEMP : 22) - temp) * Math.min(1, dt * 0.08);
        if (t - lastDraw > 1 / 12 || lastDraw < 0) { lastDraw = t; screen.redraw(); }
      } else if (lastDraw >= 0) {
        lastDraw = -1;
        rpm = 0;
        screen.redraw();
      }
      screenMat.emissiveIntensity = screenOn ? 0.5 : 0;
    },

    dispose,
  };
}
