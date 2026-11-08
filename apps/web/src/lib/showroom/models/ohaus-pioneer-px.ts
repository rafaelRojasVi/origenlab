/**
 * OHAUS Pioneer PX — balanza analítica (PX124 / PX224) con protector de vidrio.
 *
 * Modelado sobre FOTOGRAFÍAS REALES, no sobre una descripción:
 *   - foto del fabricante en el repo: `public/products/ohaus/pioneer-px.jpg`
 *     (tres cuartos por la izquierda, pantalla encendida «0.0000 g / Place sample»);
 *   - foto PX224 de LabFriend (distribuidor OHAUS), mismo ángulo a 1000 px:
 *     labfriendcoredataprod.blob.core.windows.net/labfriendcore-data-prod/images/
 *     6705045_analytical-balance-pioneer-px224-…-incal-model.jpeg
 *     (labfriend.com.au/ohaus-analytical-balance-pioneer-px224-220g-00001g-309x321x209mm-incal-model);
 *   - hoja de datos OHAUS «Pioneer Analytical and Precision Balances» 80775269
 *     (2018-04-27), vía ei.uk/wp-content/uploads/ei/product/documents/Ohaus_Pioneer_PX_Datasheet.pdf:
 *     p. 1 (foto de la analítica), p. 2 (tabla: plato Ø 90 en PX124/PX224), p. 3
 *     (primer plano frontal de la pantalla y el teclado) y p. 4 (planos acotados).
 *   Copias en ~/data/origenlab-v2-migration/insights-20261107/study/web/ref/
 *   ohaus-pioneer-px-{maker,labfriend-px224,datasheet-p1,datasheet-display,datasheet-dimensions}.*
 *   (us.ohaus.com / mx.ohaus.com fallan por certificado TLS desde aquí).
 *
 * Cotas de la hoja de datos (p. 4), en mm:
 *   conjunto 209 (ancho) × 321 (fondo) × 309 (alto); base sola 98 de alto con el plato;
 *   protector 172 de ancho exterior × 142 de fondo exterior (131 interior), vidrio
 *   235 de alto; la puerta lateral abierta asoma 138 por detrás (430 en total);
 *   plato Ø 90.
 *
 * sourceNote — ESTIMADOS medidos sobre los planos y las fotos (no son cotas):
 *   frente del protector a 107 del frente de la base (plano lateral, a escala);
 *   cubierta donde se apoya el protector a 80; canto delantero de la consola a 40;
 *   consola inclinada ~22° desde z = 62 hasta el frente; tapa del protector 12 de
 *   alto con barras de 10; varillas de esquina Ø 6; patas Ø 36 × 11 (plano frontal);
 *   arco de la base entre patas 14–18; panel gris 170 × 78; LCD 104 × 34; franja de
 *   teclas 100 × 11; burbuja Ø 9; aletas de agarre de 60 de alto (la izquierda delante,
 *   como la foto; la derecha atrás, porque la foto no la muestra delante); asa
 *   semicircular de la puerta superior 74 × 10; recorrido de las puertas; masa de la
 *   muestra 2,4853 g (sólo ilustrativa); costado derecho y trasera (no salen en las fotos).
 *
 * Ejes: x = ancho, y = alto (0 = suelo), z = fondo; el FRENTE (consola) mira a +z.
 * El giro de presentación (`yaw` positivo) muestra el costado izquierdo con el
 * rótulo «OHAUS», como la foto.
 *
 * Animación (`update`, nivel 0–1): al arrancar, la puerta lateral izquierda y la
 * superior se deslizan hacia atrás, la navecilla con la muestra baja al plato, las
 * puertas se cierran y la lectura se asienta (indicador de estabilidad «*»).
 * `setPower(true)` enciende la retroiluminación de la LCD.
 */
import * as THREE from 'three';
import type { BuiltModel } from '../types';
import { SANS, canvasTexture, lathe, labGlass, disposer } from './kit';

// ── Dimensiones (mm) ──────────────────────────────────────────────────────────
const W = 209; // ancho de la base (hoja de datos)
const D = 321; // fondo de la base (hoja de datos)
const H = 309; // alto total (hoja de datos)
const HW = W / 2;
const HD = D / 2;
const FOOT_H = 11; // est. (plano frontal)
const FOOT_R = 18; // est.
const DECK = 80; // est.: cubierta donde se apoya el protector
const NOSE = 40; // est.: alto del canto delantero
const SLOPE_Z = 62; // est.: donde la consola empieza a bajar
const SLOPE_K = (DECK - NOSE) / (HD - SLOPE_Z);
const SLOPE_ANGLE = Math.atan(SLOPE_K);

const SH_W = 172; // ancho exterior del protector (hoja de datos)
const SH_D = 142; // fondo exterior del protector (hoja de datos)
const SH_FRONT = HD - 107; // est. (plano lateral)
const SH_BACK = SH_FRONT - SH_D;
const SH_CZ = (SH_FRONT + SH_BACK) / 2;
const CAP_H = 12; // est.
const CAP_B = 10; // est.
const ROD_R = 3; // est.
const PAN_R = 45; // Ø 90 (hoja de datos)

const PANEL = { w: 170, h: 78 }; // est.
const LCD = { x: 33, y: 13, w: 104, h: 34 }; // est., mm del panel (origen arriba a la izquierda)
const SAMPLE_G = 2.4853; // ilustrativa

// ── Perfil de la base ─────────────────────────────────────────────────────────
const smin = (a: number, b: number, r: number) => {
  const h = Math.max(r - Math.abs(a - b), 0) / r;
  return Math.min(a, b) - (h * h * r) / 4;
};
const smooth = (e0: number, e1: number, x: number) => {
  const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
  return t * t * (3 - 2 * t);
};
/** Alto de la carcasa blanca a la profundidad `z`. */
const topAt = (z: number) => smin(DECK, DECK - (z - SLOPE_Z) * SLOPE_K, 14);
/** Arco de la base entre las patas: 0 sobre una pata, 1 en el centro del vano. */
const arch = (x: number, z: number) => Math.max(1 - smooth(70, 118, Math.abs(z)), 1 - smooth(46, 74, Math.abs(x)));

/**
 * Sólido barrido a lo largo de z: en cada z una sección de rectángulo redondeado
 * (medio ancho, arriba, abajo); los extremos se redondean con radio `endR` y
 * `lift` levanta el borde inferior (el arco entre las patas).
 */
function loft(
  section: (z: number) => { half: number; top: number; bot: number },
  opts: { z0: number; z1: number; endR: number; rTop: number; rBot: number; lift: (x: number, z: number) => number },
) {
  const { z0, z1, endR, rTop, rBot, lift } = opts;
  const zs: number[] = [];
  const N = 70;
  for (let i = 0; i <= N; i++) {
    const t = i / N;
    const u = 0.5 - 0.5 * Math.cos(Math.PI * t); // más secciones cerca de los extremos
    zs.push(z0 + (z1 - z0) * (0.35 * t + 0.65 * u));
  }
  const corner = (cx: number, cy: number, r: number, a0: number, a1: number, n: number, out: [number, number][]) => {
    for (let i = 0; i <= n; i++) {
      const a = a0 + ((a1 - a0) * i) / n;
      out.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]);
    }
  };
  const line = (x0: number, y0: number, x1: number, y1: number, n: number, out: [number, number][]) => {
    for (let i = 0; i <= n; i++) out.push([x0 + ((x1 - x0) * i) / n, y0 + ((y1 - y0) * i) / n]);
  };
  const ring = (half: number, top: number, bot: number) => {
    const rt = Math.max(0.5, Math.min(rTop, half - 1, (top - bot) / 2 - 0.5));
    const rb = Math.max(0.5, Math.min(rBot, half - 1, (top - bot) / 2 - 0.5));
    const pts: [number, number][] = [];
    const segs: (() => void)[] = [
      () => line(-half + rb, bot, half - rb, bot, 16, pts),
      () => corner(half - rb, bot + rb, rb, -Math.PI / 2, 0, 5, pts),
      () => line(half, bot + rb, half, top - rt, 6, pts),
      () => corner(half - rt, top - rt, rt, 0, Math.PI / 2, 8, pts),
      () => line(half - rt, top, -half + rt, top, 12, pts),
      () => corner(-half + rt, top - rt, rt, Math.PI / 2, Math.PI, 8, pts),
      () => line(-half, top - rt, -half, bot + rb, 6, pts),
      () => corner(-half + rb, bot + rb, rb, Math.PI, (3 * Math.PI) / 2, 5, pts),
    ];
    for (const s of segs) {
      s();
      pts.pop(); // el último punto de cada tramo es el primero del siguiente
    }
    return { pts, edge: bot + rb + 0.01 };
  };
  const pos: number[] = [];
  let M = 0;
  for (const z of zs) {
    const d = Math.min(z - z0, z1 - z);
    const inset = d < endR ? endR - Math.sqrt(Math.max(0, endR * endR - (endR - d) * (endR - d))) : 0;
    const s = section(z);
    const { pts, edge } = ring(s.half - inset, s.top - inset, s.bot + inset * 0.45);
    M = pts.length;
    for (const [x, y] of pts) pos.push(x, y < edge ? y + lift(x, z) : y, z);
  }
  const idx: number[] = [];
  for (let i = 0; i < zs.length - 1; i++) {
    for (let j = 0; j < M; j++) {
      const a = i * M + j;
      const b = i * M + ((j + 1) % M);
      const c = (i + 1) * M + j;
      const e = (i + 1) * M + ((j + 1) % M);
      idx.push(a, b, c, b, e, c);
    }
  }
  // Tapas: abanico desde el centro de la primera y la última sección.
  for (const [ring0, back] of [[0, true], [zs.length - 1, false]] as const) {
    let cx = 0;
    let cy = 0;
    for (let j = 0; j < M; j++) {
      cx += pos[(ring0 * M + j) * 3];
      cy += pos[(ring0 * M + j) * 3 + 1];
    }
    const center = pos.length / 3;
    pos.push(cx / M, cy / M, zs[ring0]);
    for (let j = 0; j < M; j++) {
      const a = ring0 * M + j;
      const b = ring0 * M + ((j + 1) % M);
      if (back) idx.push(center, a, b);
      else idx.push(center, b, a);
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  geo.setIndex(idx);
  geo.computeVertexNormals();
  return geo;
}

// ── Dígitos de siete segmentos ────────────────────────────────────────────────
type P = [number, number];
const SEGMENTS: Record<string, string> = {
  '0': 'abcdef', '1': 'bc', '2': 'abdeg', '3': 'abcdg', '4': 'bcfg',
  '5': 'acdfg', '6': 'acdefg', '7': 'abc', '8': 'abcdefg', '9': 'abcdfg', '-': 'g', ' ': '',
};
function segDigit(g: CanvasRenderingContext2D, ch: string, x: number, y: number, w: number, h: number, t: number) {
  const hor = (cx: number, cy: number, len: number): P[] => [
    [cx - len / 2, cy], [cx - len / 2 + t / 2, cy - t / 2], [cx + len / 2 - t / 2, cy - t / 2],
    [cx + len / 2, cy], [cx + len / 2 - t / 2, cy + t / 2], [cx - len / 2 + t / 2, cy + t / 2],
  ];
  const ver = (cx: number, cy: number, len: number): P[] => [
    [cx, cy - len / 2], [cx + t / 2, cy - len / 2 + t / 2], [cx + t / 2, cy + len / 2 - t / 2],
    [cx, cy + len / 2], [cx - t / 2, cy + len / 2 - t / 2], [cx - t / 2, cy - len / 2 + t / 2],
  ];
  const L = w - t * 1.2;
  const M = h / 2 - t * 0.9;
  const parts: Record<string, P[]> = {
    a: hor(x + w / 2, y + t / 2, L), g: hor(x + w / 2, y + h / 2, L), d: hor(x + w / 2, y + h - t / 2, L),
    f: ver(x + t / 2, y + h / 4, M), b: ver(x + w - t / 2, y + h / 4, M),
    e: ver(x + t / 2, y + (3 * h) / 4, M), c: ver(x + w - t / 2, y + (3 * h) / 4, M),
  };
  for (const key of SEGMENTS[ch] ?? '') {
    g.beginPath();
    parts[key].forEach(([px, py], i) => {
      const sx = px + (y + h - py) * 0.06; // leve inclinación, como la LCD real
      if (i) g.lineTo(sx, py);
      else g.moveTo(sx, py);
    });
    g.closePath();
    g.fill();
  }
}

/**
 * Texto en un lienzo escalado a milímetros: se dibuja a escala de píxel (un
 * cuerpo de 3 px escalado ×6 sale con el espaciado roto).
 */
function label(g: CanvasRenderingContext2D, k: number, s: string, x: number, y: number, size: number, weight: number) {
  g.save();
  g.scale(1 / k, 1 / k);
  g.font = `${weight} ${size * k}px ${SANS}`;
  g.fillText(s, x * k, y * k);
  g.restore();
}
const measure = (g: CanvasRenderingContext2D, k: number, s: string, size: number, weight: number) => {
  g.save();
  g.font = `${weight} ${size * k}px ${SANS}`;
  const w = g.measureText(s).width / k;
  g.restore();
  return w;
};

export function build(): BuiltModel {
  const { keep, dispose } = disposer();
  const root = new THREE.Group();
  const add = (parent: THREE.Object3D, geo: THREE.BufferGeometry, mat: THREE.Material, x = 0, y = 0, z = 0) => {
    const m = new THREE.Mesh(keep(geo), mat);
    m.position.set(x, y, z);
    parent.add(m);
    return m;
  };

  // ── Materiales ──────────────────────────────────────────────────────────────
  const whiteMat = keep(new THREE.MeshPhysicalMaterial({ color: 0xe9e9e6, roughness: 0.4, clearcoat: 0.3, clearcoatRoughness: 0.35 }));
  const lowerMat = keep(new THREE.MeshPhysicalMaterial({ color: 0xe4e6e5, roughness: 0.5, clearcoat: 0.15 }));
  const capMat = keep(new THREE.MeshPhysicalMaterial({ color: 0xf5f5f2, roughness: 0.38, clearcoat: 0.25 }));
  const chromeMat = keep(new THREE.MeshStandardMaterial({ color: 0xa9b0b5, metalness: 1, roughness: 0.22 }));
  const steelMat = keep(new THREE.MeshStandardMaterial({ color: 0xd0d4d6, metalness: 0.9, roughness: 0.22 }));
  const floorMat = keep(new THREE.MeshStandardMaterial({ color: 0xc4c8ca, metalness: 0.75, roughness: 0.35 }));
  const darkMat = keep(new THREE.MeshStandardMaterial({ color: 0x3a3e40, metalness: 0.4, roughness: 0.5 }));
  const redMat = keep(new THREE.MeshStandardMaterial({ color: 0xa00b16, roughness: 0.48 }));
  const stripeMat = keep(new THREE.MeshBasicMaterial({ color: 0xc0101c, toneMapped: false }));
  const glassMat = keep(labGlass());
  glassMat.color.set(0xe6f4f0);
  glassMat.opacity = 0.16;
  const edgeMat = keep(new THREE.MeshStandardMaterial({ color: 0x7fcfb4, roughness: 0.2, transparent: true, opacity: 0.85 }));
  const boatMat = keep(new THREE.MeshStandardMaterial({ color: 0xf7f7f2, roughness: 0.6, transparent: true, opacity: 0, side: THREE.DoubleSide }));
  const sampleMat = keep(new THREE.MeshStandardMaterial({ color: 0xe7dcc0, roughness: 0.9, transparent: true, opacity: 0 }));

  // ── Base: carcasa blanca sobre la carcasa inferior gris (metal fundido) ─────
  const housing = add(
    root,
    loft((z) => ({ half: HW, top: topAt(z), bot: 22 }), { z0: -HD, z1: HD, endR: 12, rTop: 13, rBot: 3, lift: (x, z) => 14 * arch(x, z) }),
    whiteMat,
  );
  housing.castShadow = true;
  housing.receiveShadow = true;
  const lower = add(
    root,
    loft((z) => ({ half: HW - 4 + 6.5 * smooth(96, 132, Math.abs(z)), top: 34, bot: FOOT_H }), { z0: -HD + 3, z1: HD - 3, endR: 8, rTop: 2, rBot: 3, lift: (x, z) => 18 * arch(x, z) }),
    lowerMat,
  );
  lower.castShadow = true;

  // ── Patas rojas moleteadas en las cuatro esquinas ─────────────────────────
  const footGeo = keep(new THREE.CylinderGeometry(FOOT_R, FOOT_R + 0.6, FOOT_H + 4, 72, 1));
  {
    const p = footGeo.attributes.position as THREE.BufferAttribute;
    for (let i = 0; i < p.count; i++) {
      if (Math.hypot(p.getX(i), p.getZ(i)) < FOOT_R - 0.5) continue;
      const k = 1 + 0.025 * Math.sign(Math.cos(Math.atan2(p.getZ(i), p.getX(i)) * 36));
      p.setXYZ(i, p.getX(i) * k, p.getY(i), p.getZ(i) * k);
    }
    footGeo.computeVertexNormals();
  }
  const FX = HW - 17;
  const FZ = HD - 22;
  for (const sx of [-1, 1]) {
    for (const sz of [-1, 1]) {
      const f = new THREE.Mesh(footGeo, redMat);
      f.position.set(sx * FX, (FOOT_H + 4) / 2, sz * FZ);
      f.castShadow = true;
      root.add(f);
    }
  }

  // ── Consola: panel gris con LCD, teclas, logotipo y burbuja de nivel ───────
  // Plano de la pendiente: normal hacia arriba y adelante; +y del panel = hacia atrás.
  const PANEL_Z = (SLOPE_Z + 8 + HD - 10) / 2; // est.
  const consoleGrp = new THREE.Group();
  consoleGrp.position.set(0, topAt(PANEL_Z) + 0.6, PANEL_Z);
  consoleGrp.rotation.x = -(Math.PI / 2 - SLOPE_ANGLE);
  root.add(consoleGrp);
  const K = 6; // px por mm
  const mmX = (x: number) => x - PANEL.w / 2;
  const mmY = (y: number) => PANEL.h / 2 - y;
  const BUB = { x: 155, y: 23 };

  const panelTex = canvasTexture(PANEL.w * K, PANEL.h * K, (g, w, h) => {
    g.clearRect(0, 0, w, h);
    g.save();
    g.scale(K, K);
    // Recuadro gris claro, con un filo algo más oscuro.
    g.fillStyle = '#b5bbba';
    g.strokeStyle = '#868d8b';
    g.lineWidth = 0.5;
    g.beginPath();
    g.roundRect(0.4, 0.4, PANEL.w - 0.8, PANEL.h - 0.8, 7);
    g.fill();
    g.stroke();
    // Marco de la ventana de la LCD.
    g.fillStyle = '#a2a9a7';
    g.beginPath();
    g.roundRect(LCD.x - 1.2, LCD.y - 1.2, LCD.w + 2.4, LCD.h + 2.4, 1.5);
    g.fill();
    // «PIONEER™» en versalitas, arriba a la derecha de la ventana.
    g.fillStyle = '#2a2d2e';
    g.textAlign = 'right';
    g.textBaseline = 'alphabetic';
    const pw = measure(g, K, 'IONEER', 4.4, 800);
    label(g, K, 'IONEER', LCD.x + LCD.w - 2, LCD.y - 3, 4.4, 800);
    label(g, K, 'P', LCD.x + LCD.w - 2 - pw, LCD.y - 3, 5.6, 800);
    g.textAlign = 'left';
    label(g, K, '™', LCD.x + LCD.w - 1.8, LCD.y - 6.2, 2, 600);
    // Logotipo OHAUS: cuadro rojo con la elipse blanca y el nombre debajo.
    const lx = 16;
    const ly = 26;
    g.fillStyle = '#b00d1a';
    g.fillRect(lx - 6, ly - 5.5, 12, 10);
    g.save();
    g.translate(lx, ly - 0.5);
    g.rotate(-0.75);
    g.strokeStyle = '#b5bbba';
    g.lineWidth = 1.5;
    g.beginPath();
    g.ellipse(0, 0, 6.2, 3, 0, 0, Math.PI * 2);
    g.stroke();
    g.restore();
    g.fillStyle = '#b00d1a';
    g.textAlign = 'center';
    label(g, K, 'OHAUS', lx, ly + 8.2, 2.9, 900);
    // Burbuja de nivel: hueco redondo a la derecha.
    const rg = g.createRadialGradient(BUB.x - 1, BUB.y - 1, 0.5, BUB.x, BUB.y, 4.8);
    rg.addColorStop(0, '#f4f6f5');
    rg.addColorStop(0.55, '#b9bebd');
    rg.addColorStop(1, '#7d8483');
    g.fillStyle = rg;
    g.beginPath();
    g.arc(BUB.x, BUB.y, 4.5, 0, Math.PI * 2);
    g.fill();
    // Franja de cuatro teclas de membrana, con su segunda función debajo.
    const kx = 40;
    const ky = 55;
    const kw = 100;
    const kh = 11;
    g.fillStyle = '#aab0ae';
    g.strokeStyle = '#7c8381';
    g.lineWidth = 0.4;
    g.beginPath();
    g.roundRect(kx, ky, kw, kh, 1.4);
    g.fill();
    g.stroke();
    const keys: [string, string, string][] = [
      ['Zero', '', 'Yes'],
      ['Print', 'Unit', 'No'],
      ['Function', 'Mode', 'Back'],
      ['Tare', 'Menu-Cal', 'Exit'],
    ];
    keys.forEach(([a, b, sub], i) => {
      const cx = kx + (kw / 4) * (i + 0.5);
      if (i) {
        g.strokeStyle = '#8f9694';
        g.lineWidth = 0.4;
        g.beginPath();
        g.moveTo(kx + (kw / 4) * i, ky + 1.5);
        g.lineTo(kx + (kw / 4) * i, ky + kh - 1.5);
        g.stroke();
      }
      g.fillStyle = '#1f2324';
      g.textAlign = 'center';
      g.textBaseline = 'middle';
      if (b) {
        label(g, K, a, cx, ky + 3.6, 2.9, 700);
        label(g, K, b, cx, ky + 7.4, 2.9, 700);
      } else {
        label(g, K, a, cx - 2, ky + kh / 2, 2.9, 700);
        // Ícono de encendido rojo junto a «Zero».
        g.strokeStyle = '#b00d1a';
        g.lineWidth = 0.5;
        g.beginPath();
        g.arc(cx + 5.2, ky + kh / 2 + 0.2, 1.4, -Math.PI / 2 + 0.6, Math.PI * 1.5 - 0.6);
        g.moveTo(cx + 5.2, ky + kh / 2 - 1.6);
        g.lineTo(cx + 5.2, ky + kh / 2 + 0.1);
        g.stroke();
      }
      g.fillStyle = '#2f3436';
      label(g, K, sub, cx, ky + kh + 3.2, 2.2, 600);
    });
    g.restore();
  });
  keep(panelTex.texture);
  const panelMat = keep(new THREE.MeshStandardMaterial({ map: panelTex.texture, transparent: true, roughness: 0.55, envMapIntensity: 0.5 }));
  add(consoleGrp, new THREE.PlaneGeometry(PANEL.w, PANEL.h), panelMat);
  // Anillo en relieve alrededor del hueco de la burbuja.
  add(consoleGrp, new THREE.TorusGeometry(4.7, 0.55, 10, 40), whiteMat, mmX(BUB.x), mmY(BUB.y), 0.3);

  // LCD retroiluminada: plano propio para que sólo ella brille.
  let screenOn = false;
  let shown = 0;
  let stable = false;
  const LK = 8;
  const lcdTex = canvasTexture(LCD.w * LK, LCD.h * LK, (g) => {
    g.save();
    g.scale(LK, LK);
    const bg = g.createLinearGradient(0, 0, 0, LCD.h);
    bg.addColorStop(0, screenOn ? '#fbfdfc' : '#c4cac7');
    bg.addColorStop(1, screenOn ? '#eef3f1' : '#b6bdba');
    g.fillStyle = bg;
    g.fillRect(0, 0, LCD.w, LCD.h);
    // Líneas rojas impresas en el cristal, arriba y abajo (se ven siempre).
    g.fillStyle = '#b00d1a';
    g.fillRect(0, 2.2, LCD.w, 1.3);
    g.fillRect(0, LCD.h - 3.6, LCD.w, 1.3);
    if (screenOn) {
      g.fillStyle = '#101413';
      // Indicadores a la izquierda: estabilidad «*» y cero «→0←».
      g.textAlign = 'left';
      g.textBaseline = 'middle';
      if (stable) label(g, LK, '*', 3.5, 10.5, 5, 800);
      if (shown < 0.00005) label(g, LK, '→0←', 2.6, 16.5, 2.8, 700);
      // Cifra principal en siete segmentos, alineada a la derecha delante de la «g».
      const txt = shown.toFixed(4);
      const dw = 8.6;
      const dh = 14.5;
      const top = 6.6;
      const gap = 1.6;
      let width = 0;
      for (const ch of txt) width += ch === '.' ? 2.6 : dw + gap;
      let cx = LCD.w - 13 - width;
      for (const ch of txt) {
        if (ch === '.') {
          g.fillRect(cx + 0.1, top + dh - 1.7, 1.7, 1.7);
          cx += 2.6;
        } else {
          segDigit(g, ch, cx, top, dw, dh, 2.3);
          cx += dw + gap;
        }
      }
      g.textBaseline = 'alphabetic';
      label(g, LK, 'g', LCD.w - 9, top + dh, 5.2, 700);
      // Segunda línea de guía.
      if (shown < 0.00005) label(g, LK, 'Place sample', 10, LCD.h - 6, 3.6, 600);
    }
    g.restore();
  });
  keep(lcdTex.texture);
  const lcdMat = keep(
    // Sin luz de escena: el lienzo ya es la LCD apagada (gris) o retroiluminada (blanca).
    new THREE.MeshBasicMaterial({ map: lcdTex.texture, toneMapped: false }),
  );
  add(consoleGrp, new THREE.PlaneGeometry(LCD.w, LCD.h), lcdMat, mmX(LCD.x + LCD.w / 2), mmY(LCD.y + LCD.h / 2), 0.25);

  // ── Rótulo «OHAUS» en el costado izquierdo ──────────────────────────────────
  const sideTex = canvasTexture(512, 112, (g, w, h) => {
    g.clearRect(0, 0, w, h);
    g.fillStyle = '#b00d1a';
    g.font = `900 ${h * 0.86}px ${SANS}`;
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText('OHAUS', w / 2, h * 0.54, w * 0.98);
  });
  keep(sideTex.texture);
  const sideMat = keep(new THREE.MeshStandardMaterial({ map: sideTex.texture, transparent: true, roughness: 0.45 }));
  const sideLogo = add(root, new THREE.PlaneGeometry(46, 10), sideMat, -HW - 0.3, 47, 92);
  sideLogo.rotation.y = -Math.PI / 2;

  // ── Interior del protector: piso metálico, subplato y plato ────────────────
  add(root, new THREE.BoxGeometry(SH_W - 14, 1.2, SH_D - 14), floorMat, 0, DECK + 0.3, SH_CZ);
  add(root, new THREE.CylinderGeometry(30, 32, 9, 48), darkMat, 0, DECK + 5, SH_CZ);
  const PAN_Y = DECK + 10;
  const pan = add(
    root,
    lathe([[0, 0], [PAN_R - 4, 0], [PAN_R - 0.5, 1], [PAN_R, 3.5], [PAN_R - 0.6, 5.2], [PAN_R - 2.4, 4.2], [PAN_R - 3.5, 2.6], [0, 2.6]]),
    steelMat,
    0,
    PAN_Y,
    SH_CZ,
  );
  pan.castShadow = true;
  pan.receiveShadow = true;
  const PAN_TOP = PAN_Y + 2.6;

  // ── Protector: zócalo, varillas cromadas, tapa blanca, vidrios con canto verde ─
  const SH_TOP = H - CAP_H;
  const glassH = SH_TOP - DECK - 4;
  const glassY = DECK + 4 + glassH / 2;
  for (const sz of [-1, 1]) add(root, new THREE.BoxGeometry(SH_W, 4, 6), capMat, 0, DECK + 2, SH_CZ + sz * (SH_D / 2 - 3));
  for (const sx of [-1, 1]) add(root, new THREE.BoxGeometry(6, 4, SH_D - 12), capMat, sx * (SH_W / 2 - 3), DECK + 2, SH_CZ);
  const rodGeo = keep(new THREE.CylinderGeometry(ROD_R, ROD_R, SH_TOP - DECK, 20));
  for (const sx of [-1, 1]) {
    for (const z of [SH_FRONT - 6, SH_BACK + 6]) {
      const r = new THREE.Mesh(rodGeo, chromeMat);
      r.position.set(sx * (SH_W / 2 - 6), (DECK + SH_TOP) / 2, z);
      r.castShadow = true;
      root.add(r);
    }
  }
  // Tapa: marco blanco de cuatro barras.
  const capY = SH_TOP + CAP_H / 2;
  for (const sz of [-1, 1]) add(root, new THREE.BoxGeometry(SH_W, CAP_H, CAP_B), capMat, 0, capY, SH_CZ + sz * (SH_D / 2 - CAP_B / 2)).castShadow = true;
  for (const sx of [-1, 1]) add(root, new THREE.BoxGeometry(CAP_B, CAP_H, SH_D - 2 * CAP_B), capMat, sx * (SH_W / 2 - CAP_B / 2), capY, SH_CZ).castShadow = true;
  // Línea roja en el canto interior delantero de la tapa.
  add(root, new THREE.BoxGeometry(SH_W - 2 * CAP_B, 0.6, 1.6), stripeMat, 0, H + 0.05, SH_FRONT - CAP_B + 0.8);

  const glassMesh = (geo: THREE.BufferGeometry) => {
    const m = new THREE.Mesh(keep(geo), glassMat);
    m.renderOrder = 2;
    return m;
  };
  const edgeGeo = keep(new THREE.BoxGeometry(2.6, glassH, 3));
  // Vidrio delantero fijo y trasero.
  for (const z of [SH_FRONT - 2, SH_BACK + 2]) {
    const m = glassMesh(new THREE.BoxGeometry(SH_W - 14, glassH, 2.5));
    m.position.set(0, glassY, z);
    root.add(m);
  }
  for (const sx of [-1, 1]) {
    const e = new THREE.Mesh(edgeGeo, edgeMat);
    e.position.set(sx * (SH_W / 2 - 8), glassY, SH_FRONT - 2);
    root.add(e);
  }
  // Puertas laterales deslizantes (hacia atrás), con su aleta blanca de agarre.
  const finShape = new THREE.Shape();
  finShape.moveTo(0, 0);
  finShape.lineTo(9, 0);
  finShape.quadraticCurveTo(9, 30, 2, 60);
  finShape.quadraticCurveTo(0.5, 62, 0, 58);
  finShape.lineTo(0, 0);
  const finGeo = keep(new THREE.ExtrudeGeometry(finShape, { depth: 5, bevelEnabled: true, bevelSize: 1.2, bevelThickness: 1.2, bevelSegments: 3, curveSegments: 16 }));
  finGeo.translate(0, 0, -2.5);
  const doorLen = SH_D - 10;
  const doors: THREE.Group[] = [];
  for (const sx of [-1, 1]) {
    const door = new THREE.Group();
    door.position.set(sx * (SH_W / 2 - 2), 0, SH_CZ);
    root.add(door);
    const m = glassMesh(new THREE.BoxGeometry(doorLen, glassH, 2.5));
    m.rotation.y = Math.PI / 2;
    m.position.set(0, glassY, 0);
    door.add(m);
    const e = new THREE.Mesh(edgeGeo, edgeMat);
    e.position.set(0, glassY, doorLen / 2 - 1);
    door.add(e);
    // Aleta vertical por fuera, junto al canto delantero, en la mitad baja.
    const fin = new THREE.Mesh(finGeo, capMat);
    fin.rotation.y = sx < 0 ? Math.PI : 0;
    // Izquierda: junto al canto delantero (foto); derecha: la foto no la muestra
    // delante, se pone atrás (est.), con un botón de tope en el canto delantero.
    fin.position.set(sx * 1.5, DECK + 14, sx < 0 ? doorLen / 2 - 14 : -doorLen / 2 + 8);
    fin.castShadow = true;
    door.add(fin);
    if (sx > 0) add(door, new THREE.SphereGeometry(2.6, 16, 12), chromeMat, 3, DECK + glassH * 0.6, doorLen / 2 - 4);
    doors.push(door);
  }
  const leftDoor = doors[0];
  // Puerta superior deslizante con su asa semicircular.
  const topDoor = new THREE.Group();
  topDoor.position.set(0, H - 2, SH_CZ);
  root.add(topDoor);
  topDoor.add(glassMesh(new THREE.BoxGeometry(SH_W - 2 * CAP_B + 4, 2.5, SH_D - 2 * CAP_B + 4)));
  {
    const domeGeo = keep(new THREE.CylinderGeometry(37, 37, 3, 48, 1, false, -Math.PI / 2, Math.PI));
    domeGeo.rotateX(-Math.PI / 2);
    domeGeo.scale(1, 0.27, 1);
    const dome = new THREE.Mesh(domeGeo, capMat);
    dome.position.set(0, 1.2, SH_D / 2 - CAP_B - 26);
    topDoor.add(dome);
  }

  // ── Navecilla de pesaje con la muestra (aparece con el nivel) ──────────────
  const boat = new THREE.Group();
  boat.position.set(0, PAN_TOP, SH_CZ);
  boat.visible = false;
  root.add(boat);
  const boatGeo = new THREE.CylinderGeometry(19, 14, 7, 4, 1, true);
  boatGeo.rotateY(Math.PI / 4);
  boatGeo.scale(1, 1, 0.75);
  add(boat, boatGeo, boatMat, 0, 3.5, 0).renderOrder = 1;
  add(boat, new THREE.PlaneGeometry(20, 15).rotateX(-Math.PI / 2), boatMat, 0, 0.2, 0).renderOrder = 1;
  const heapGeo = new THREE.SphereGeometry(7, 20, 12);
  heapGeo.scale(1, 0.4, 0.85);
  add(boat, heapGeo, sampleMat, 0, 1, 0).renderOrder = 1;

  // ── Anclas ──────────────────────────────────────────────────────────────────
  const displayAnchor = new THREE.Object3D();
  displayAnchor.position.set(mmX(LCD.x + LCD.w / 2), mmY(LCD.y + LCD.h / 2), 6);
  consoleGrp.add(displayAnchor);
  const panAnchor = new THREE.Object3D();
  panAnchor.position.set(0, PAN_TOP + 6, SH_CZ);
  root.add(panAnchor);
  const shieldAnchor = new THREE.Object3D();
  shieldAnchor.position.set(-SH_W / 2 - 4, DECK + glassH * 0.62, SH_CZ + 20);
  root.add(shieldAnchor);
  const footAnchor = new THREE.Object3D();
  footAnchor.position.set(-FX, FOOT_H / 2, FZ + FOOT_R);
  root.add(footAnchor);

  // Normal de la consola en el marco del modelo.
  const consoleNormal = new THREE.Vector3(0, Math.cos(SLOPE_ANGLE), Math.sin(SLOPE_ANGLE));

  // ── Estado ──────────────────────────────────────────────────────────────────
  let powered = false;
  let doorOpen = 0;
  let boatAlpha = 0;
  let reading = 0;
  let lastDraw = -1;
  let drawn = '';
  const DOOR_TRAVEL = 112; // est.: la hoja abierta asoma ~138 por detrás (plano)
  const TOP_TRAVEL = 70; // est.

  return {
    root,
    anchors: { display: displayAnchor, pan: panAnchor, shield: shieldAnchor, foot: footAnchor },
    views: {
      pantalla: { target: displayAnchor, direction: consoleNormal.clone().add(new THREE.Vector3(0, 0.08, 0)), distance: 250 },
      plato: { target: panAnchor, direction: new THREE.Vector3(-0.35, 0.95, 0.75), distance: 420 },
      protector: { target: shieldAnchor, direction: new THREE.Vector3(-0.8, 0.28, 0.72), distance: 520 },
    },
    frameTop: H + 10,
    frameMargin: 1.6,
    yaw: 0.68,

    setPower(on: boolean) {
      powered = on;
    },

    update(dt: number, t: number, level: number) {
      // Puertas: se abren al arrancar, entra la muestra, se cierran.
      const wantDoor = level > 0.12 && level < 0.78 ? 1 : 0;
      doorOpen += (wantDoor - doorOpen) * Math.min(1, dt * 3);
      leftDoor.position.z = SH_CZ - doorOpen * DOOR_TRAVEL;
      topDoor.position.z = SH_CZ - doorOpen * TOP_TRAVEL;

      const wantBoat = level > 0.3 ? 1 : 0;
      boatAlpha += (wantBoat - boatAlpha) * Math.min(1, dt * 2.4);
      boat.visible = boatAlpha > 0.02;
      boatMat.opacity = boatAlpha;
      sampleMat.opacity = boatAlpha;
      boat.position.y = PAN_TOP + (1 - boatAlpha) * 40;

      // Pantalla: la lectura sube con la muestra y se asienta.
      screenOn = powered || level > 0.02;
      const want = boatAlpha > 0.9 ? SAMPLE_G : 0;
      reading += (want - reading) * Math.min(1, dt * 4);
      if (Math.abs(want - reading) < 0.00004) reading = want;
      // Estable: sin navecilla, o con ella ya apoyada y la cifra quieta.
      // Con una puerta abierta la corriente de aire no deja asentarse la cifra.
      stable = Math.abs(want - reading) < 0.0002 && (boatAlpha < 0.02 || boatAlpha > 0.98) && doorOpen < 0.05;
      shown = Math.max(0, stable ? want : reading + Math.sin(t * 37) * (doorOpen > 0.05 ? 0.0021 : 0.0003));

      const key = `${screenOn}|${shown.toFixed(4)}|${stable}`;
      if (key !== drawn && t - lastDraw > 1 / 15) {
        drawn = key;
        lastDraw = t;
        lcdTex.redraw();
      }
    },

    dispose,
  };
}
