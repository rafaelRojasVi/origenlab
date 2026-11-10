/**
 * Adam Equipment Highland HCB 123 — balanza portátil de precisión (120 g ×
 * 0,001 g) con protector de corrientes azul. Es la variante de la fotografía
 * VERIFIED del sitio (`public/products/adam-equipment/highland.png`, página 1
 * de la ficha HCB): pantalla «0.000 g», protector cilíndrico con tapa
 * perforada (el HCB 6001, con plato de 145 mm, no lleva protector).
 *
 * sourceNote — medidas oficiales (ficha HCB-DS-A4-EN, Adam Equipment):
 *   • Dimensiones totales (An × Fo × Al): 174 × 252 × 80 mm (sin el protector)
 *   • Plato Ø 120 mm
 *   • Protector de corrientes Ø 132 × 90 mm
 *   • Pantalla LCD retroiluminada con dígitos de 18 mm
 * Leído de la fotografía (estimado †):
 *   • Planta en lágrima: nariz delantera estrecha y redondeada (140 mm †),
 *     círculo alrededor del protector (Ø 154 †) y atrás un bloque bajo de lado
 *     a lado (los 174 mm oficiales) que hace de asa: sus lóbulos asoman a los
 *     lados (72 mm de fondo †), con patas azules estriadas debajo †
 *   • Cubierta blanca delgada sobre una base azul de 15 mm †; baja hacia una
 *     nariz plana con el teclado (42 → 32 mm, ~5° †). Los lóbulos del asa
 *     quedan apenas sobre la cubierta (54 mm †): la fotografía no muestra
 *     ningún punto del cuerpo a 80 mm, así que manda la fotografía y la cifra
 *     oficial de alto no se alcanza (quizá incluye el plato o el asiento del
 *     protector; sin confirmar)
 *   • Centro del plato 26 mm detrás del centro del cuerpo †
 *   • Marco de pantalla 82 × 44 mm con ventana LCD 60 × 30 mm †, logo
 *     «Highland™» arriba a la izquierda y franjas azules a los lados
 *   • Teclado de membrana 86 × 20 mm †: encendido aparte; Smpl, Mode, % y
 *     Print sobre un óvalo azul; tara amarilla abajo a la izquierda y arriba a
 *     la derecha; logo ADAM entre pantalla y teclado
 *   • Nivel de burbuja delante del protector y placa de modelo a su derecha †
 *   • Protector: banda inferior y borde superior más opacos †; tapa hundida
 *     10 mm † con orificio central Ø 24 † y catorce orificios en anillo †
 *
 * Ejes: x = ancho, y = alto (0 = suelo), z = fondo; el FRENTE (teclado) mira a
 * +z y el asa queda atrás (z = −126).
 *
 * Animación: arranca encendida («0.000 g», como la fotografía y el póster) y
 * la pantalla queda encendida siempre. `setPower(true)` es «Pesar» en la sala:
 * pone la muestra (igual que `root.userData.sample = 1`); `setPower(false)` es
 * «Retirar» y la saca. `root.userData.sample = 1` coloca una muestra: la tapa del protector se
 * levanta, el platillo baja al plato, la tapa cierra y la lectura sube y se
 * asienta con una oscilación amortiguada; `0` la retira y vuelve a cero.
 */
import * as THREE from 'three';
import { mergeVertices } from 'three/examples/jsm/utils/BufferGeometryUtils.js';
import type { BuiltModel } from '../types';
import { SANS, canvasTexture, disposer, lathe } from './kit';

/* ── medidas (mm) ─────────────────────────────────────────────────────────── */
const HALF_W = 87; // 174 / 2, oficial
const FRONT = 126; // 252 / 2, oficial
const LOBE_TOP = 54; // lóbulos del asa: apenas sobre la cubierta, como en la fotografía †
const ZC = -26; // centro del plato y del protector †
const RING = 77; // radio de la cubierta alrededor del protector †
const NOSE_Z = 30; // donde empieza la nariz delantera †
const NOSE_W = 70; // medio ancho de la nariz al empezar †
const HUMP_Z = -90; // centro del bloque trasero del asa †
const HUMP_B = 36; // medio fondo del bloque trasero †
const HUMP_IN = 74; // radio de su cara cóncava, alrededor del protector †
const BASE_H = 15; // base azul †
const DECK = 42; // cubierta plana alrededor del protector †
const NOSE = 32; // alto de la cubierta en el borde delantero †
const SLOPE_Z = 20; // donde empieza a bajar la cubierta †
const SLOPE = Math.atan((DECK - NOSE) / (FRONT - SLOPE_Z));
const PAN_R = 60; // Ø 120 oficial
const SHIELD_R = 66; // Ø 132 oficial
const SHIELD_H = 90; // oficial
const LID_DROP = 10; // tapa hundida †
const SAMPLE_G = 42.618; // lo que marca la muestra ilustrada (no es una especificación)
const CAPACITY_G = 120; // HCB 123, para la barra de capacidad

type P = [number, number];

const smooth = (t: number) => {
  const x = Math.min(1, Math.max(0, t));
  return x * x * (3 - 2 * x);
};

/** Superelipse: medio ancho `a` a la distancia `d` del centro, de semieje `b`. */
const superellipse = (a: number, d: number, b: number, p: number) =>
  a * Math.pow(Math.max(0, 1 - Math.pow(Math.min(1, Math.abs(d) / b), p)), 1 / p);

/** Lágrima de la cubierta: nariz delantera estrecha y redondeada, círculo alrededor del protector. */
function halfWidth(z: number) {
  if (z >= NOSE_Z) return superellipse(NOSE_W, z - NOSE_Z, FRONT - NOSE_Z, 2.6);
  if (z >= ZC) return RING + (NOSE_W - RING) * smooth((z - ZC) / (NOSE_Z - ZC));
  const d = z - ZC;
  return Math.sqrt(Math.max(0, RING * RING - d * d));
}

/** Bloque trasero del asa: de lado a lado (174 mm), con los extremos redondeados. */
const humpWidth = (z: number) => superellipse(HALF_W, z - HUMP_Z, HUMP_B, 3);

/** Contorno del asa: el bloque trasero menos el círculo del protector (cara cóncava hacia el plato). */
function humpOutline(): P[] {
  // Se recorre la superelipse desde su punto delantero central (dentro del
  // círculo): lo que queda fuera es un solo tramo, del lóbulo izquierdo por
  // detrás hasta el derecho; se cierra con el arco del círculo por detrás.
  const outer: P[] = [];
  const n = 360;
  for (let i = 0; i < n; i++) {
    const t = Math.PI / 2 + (i / n) * Math.PI * 2;
    const c = Math.cos(t);
    const s = Math.sin(t);
    const x = HALF_W * Math.sign(c) * Math.pow(Math.abs(c), 2 / 3);
    const z = HUMP_Z + HUMP_B * Math.sign(s) * Math.pow(Math.abs(s), 2 / 3);
    if (Math.hypot(x, z - ZC) > HUMP_IN) outer.push([x, z]);
  }
  const angle = ([x, z]: P) => Math.atan2(z - ZC, x);
  const from = angle(outer[outer.length - 1]); // lóbulo derecho
  const to = angle(outer[0]); // lóbulo izquierdo
  const inner: P[] = [];
  for (let i = 1; i < 48; i++) {
    const a = from + ((to - from) * i) / 48;
    inner.push([HUMP_IN * Math.cos(a), ZC + HUMP_IN * Math.sin(a)]);
  }
  return [...outer, ...inner];
}

/** Contorno cerrado (x, z) muestreado con más puntos en los extremos. */
function outline(width: (z: number) => number, zFront: number, zBack: number, n = 72): P[] {
  const mid = (zFront + zBack) / 2;
  const half = (zFront - zBack) / 2;
  const right: P[] = [];
  for (let i = 0; i <= n; i++) {
    const z = mid + half * Math.cos((Math.PI * i) / n);
    right.push([width(z), z]);
  }
  const left = right
    .slice(1, -1)
    .reverse()
    .map(([x, z]) => [-x, z] as P);
  return [...right, ...left];
}

/**
 * Extruye un contorno de planta hasta `height` con bordes redondeados de radio
 * `r` (sin crecer: `bevelOffset = −r`), de y = 0 a y = height, con normales
 * suaves.
 */
function extrudePlan(points: P[], height: number, r: number, segments = 5) {
  const shape = new THREE.Shape(points.map(([x, z]) => new THREE.Vector2(x, -z)));
  const raw = new THREE.ExtrudeGeometry(shape, {
    depth: Math.max(height - 2 * r, 0.01),
    bevelEnabled: true,
    bevelSize: r,
    bevelThickness: r,
    bevelOffset: -r,
    bevelSegments: segments,
    curveSegments: 4,
  });
  raw.rotateX(-Math.PI / 2);
  raw.translate(0, r, 0);
  raw.deleteAttribute('uv');
  raw.deleteAttribute('normal');
  const merged = mergeVertices(raw, 0.01);
  raw.dispose();
  merged.computeVertexNormals();
  return merged;
}

/** Alto de la cubierta en `z`. */
const deckAt = (z: number) => (z <= SLOPE_Z ? DECK : DECK - (DECK - NOSE) * ((z - SLOPE_Z) / (FRONT - SLOPE_Z)));

/* ── pantalla de segmentos ────────────────────────────────────────────────── */
const SEGMENTS: Record<string, string> = {
  '0': 'abcdef',
  '1': 'bc',
  '2': 'abged',
  '3': 'abgcd',
  '4': 'fgbc',
  '5': 'afgcd',
  '6': 'afgedc',
  '7': 'abc',
  '8': 'abcdefg',
  '9': 'abcdfg',
  '-': 'g',
};

/** Un dígito de siete segmentos con su esquina superior izquierda en (x, y), de alto `h`, inclinado. */
function digit(g: CanvasRenderingContext2D, ch: string, x: number, y: number, h: number) {
  const w = h * 0.52;
  const t = h * 0.14;
  const slant = h * 0.09;
  const on = SEGMENTS[ch] ?? '';
  const sx = (yy: number) => slant * (1 - (yy - y) / h);
  const poly = (pts: P[]) => {
    g.beginPath();
    pts.forEach(([px, py], i) => (i ? g.lineTo(px + sx(py), py) : g.moveTo(px + sx(py), py)));
    g.closePath();
    g.fill();
  };
  const hseg = (yy: number) =>
    poly([
      [x + t * 0.25, yy],
      [x + t * 0.75, yy - t / 2],
      [x + w - t * 0.75, yy - t / 2],
      [x + w - t * 0.25, yy],
      [x + w - t * 0.75, yy + t / 2],
      [x + t * 0.75, yy + t / 2],
    ]);
  const vseg = (xx: number, y0: number, y1: number) =>
    poly([
      [xx, y0 + t * 0.25],
      [xx + t / 2, y0 + t * 0.75],
      [xx + t / 2, y1 - t * 0.75],
      [xx, y1 - t * 0.25],
      [xx - t / 2, y1 - t * 0.75],
      [xx - t / 2, y0 + t * 0.75],
    ]);
  const m = y + h / 2;
  if (on.includes('a')) hseg(y);
  if (on.includes('b')) vseg(x + w, y, m);
  if (on.includes('c')) vseg(x + w, m, y + h);
  if (on.includes('d')) hseg(y + h);
  if (on.includes('e')) vseg(x, m, y + h);
  if (on.includes('f')) vseg(x, y, m);
  if (on.includes('g')) hseg(m);
}

export function build(): BuiltModel {
  const { keep, dispose } = disposer();
  const root = new THREE.Group();
  root.userData.sample = 0;

  /* ── materiales ─────────────────────────────────────────────────────────── */
  const DS = THREE.DoubleSide;
  const white = keep(new THREE.MeshPhysicalMaterial({ color: 0xf3f4f6, roughness: 0.42, clearcoat: 0.35, clearcoatRoughness: 0.4, side: DS }));
  const blue = keep(new THREE.MeshPhysicalMaterial({ color: 0x1d5fb4, roughness: 0.38, clearcoat: 0.4, clearcoatRoughness: 0.3 }));
  const rubber = keep(new THREE.MeshStandardMaterial({ color: 0x1a4f9a, roughness: 0.8, side: DS }));
  const steel = keep(new THREE.MeshStandardMaterial({ color: 0xc9ced4, metalness: 0.85, roughness: 0.28, side: DS }));
  const bezel = keep(new THREE.MeshStandardMaterial({ color: 0xe6eaee, roughness: 0.5 }));
  const translucent = (color: number, opacity: number) =>
    keep(
      new THREE.MeshPhysicalMaterial({
        color,
        roughness: 0.08,
        clearcoat: 1,
        clearcoatRoughness: 0.05,
        transparent: true,
        opacity,
        side: DS,
        depthWrite: false,
        envMapIntensity: 1.6,
      }),
    );
  const shieldClear = translucent(0x0f72d8, 0.58);
  const shieldBand = translucent(0x0753b8, 0.9);
  const lidMat = translucent(0x1584e6, 0.68);

  /* ── base azul, cubierta blanca y asa trasera ───────────────────────────── */
  const plan = outline(halfWidth, FRONT, ZC - RING);
  const footprint = outline((z) => Math.max(halfWidth(z), humpWidth(z)), FRONT, HUMP_Z - HUMP_B, 96);
  const base = new THREE.Mesh(keep(extrudePlan(footprint.map(([x, z]) => [x * 1.012, z * 1.006 + 0.4] as P), BASE_H, 4)), blue);
  base.position.y = 1.5;
  root.add(base);

  const shellGeo = keep(extrudePlan(plan, DECK - 12, 8, 6));
  shellGeo.translate(0, 12, 0);
  {
    // La cubierta baja hacia el frente: sólo se mueve la mitad superior.
    const pos = shellGeo.getAttribute('position');
    for (let i = 0; i < pos.count; i++) {
      const y = pos.getY(i);
      if (y > (DECK + 12) / 2) pos.setY(i, y + deckAt(pos.getZ(i)) - DECK);
    }
    shellGeo.computeVertexNormals();
  }
  root.add(new THREE.Mesh(shellGeo, white));

  // Asa: bloque trasero bajo y redondeado, de lado a lado, con la
  // cara cóncava hacia el protector; sus extremos son los lóbulos que se ven a
  // los lados, detrás del plato.
  const humpGeo = keep(extrudePlan(humpOutline(), LOBE_TOP - 12, 14, 6));
  humpGeo.translate(0, 12, 0);
  root.add(new THREE.Mesh(humpGeo, white));

  // Patas traseras de nivelación: discos azules estriados bajo los lóbulos.
  const ribs: P[] = [[0, 0]];
  for (let i = 0; i <= 6; i++) ribs.push([i % 2 === 0 ? 17 : 18.6, 0.8 + i * 1.7]);
  ribs.push([15, 12], [0, 12]);
  const footGeo = keep(lathe(ribs, 40));
  for (const side of [-1, 1]) {
    const foot = new THREE.Mesh(footGeo, rubber);
    foot.position.set(side * 70, 0, -100);
    root.add(foot);
  }

  // Patas.
  const padGeo = keep(new THREE.CylinderGeometry(6, 6.5, 2, 20));
  for (const [x, z] of [
    [-40, 96],
    [40, 96],
    [-50, -20],
    [50, -20],
  ] as P[]) {
    const pad = new THREE.Mesh(padGeo, rubber);
    pad.position.set(x, 1, z);
    root.add(pad);
  }

  /* ── plato y asiento del protector ──────────────────────────────────────── */
  const seat = new THREE.Mesh(
    keep(
      lathe(
        [
          [63, 0],
          [71, 0],
          [72, 1.6],
          [70.5, 3],
          [64, 3],
          [63, 2],
        ],
        96,
      ),
    ),
    white,
  );
  seat.position.set(0, DECK - 0.5, ZC);
  root.add(seat);

  const pan = new THREE.Mesh(
    keep(
      lathe(
        [
          [0, 0],
          [8, 0],
          [8, 3.5],
          [PAN_R - 1, 3.5],
          [PAN_R, 4.5],
          [PAN_R, 6.5],
          [PAN_R - 1.5, 7],
          [0, 7],
        ],
        96,
      ),
    ),
    steel,
  );
  pan.position.set(0, DECK, ZC);
  root.add(pan);
  const PAN_TOP = DECK + 7;

  /* ── protector de corrientes con tapa perforada ─────────────────────────── */
  const shield = new THREE.Group();
  shield.position.set(0, DECK + 1, ZC);
  root.add(shield);
  const band = new THREE.Mesh(
    keep(
      lathe(
        [
          [65.5, 0],
          [68.5, 0],
          [68.5, 13],
          [67, 15],
          [65.5, 15],
          [65.5, 0],
        ],
        96,
      ),
    ),
    shieldBand,
  );
  band.renderOrder = 3;
  shield.add(band);
  const wall = new THREE.Mesh(keep(new THREE.CylinderGeometry(SHIELD_R, SHIELD_R, SHIELD_H - 19, 96, 1, true)), shieldClear);
  wall.position.y = 15 + (SHIELD_H - 19) / 2;
  wall.renderOrder = 3;
  shield.add(wall);
  const rim = new THREE.Mesh(
    keep(
      lathe(
        [
          [65.5, SHIELD_H - 4],
          [67.5, SHIELD_H - 4],
          [67.5, SHIELD_H],
          [65.5, SHIELD_H],
          [65.5, SHIELD_H - 4],
        ],
        96,
      ),
    ),
    shieldBand,
  );
  rim.renderOrder = 3;
  shield.add(rim);

  // Tapa: cono corto que baja desde el borde y disco con orificios.
  const lid = new THREE.Group();
  lid.position.y = SHIELD_H;
  shield.add(lid);
  const lidCone = new THREE.Mesh(
    keep(
      lathe(
        [
          [65.5, 0],
          [62, -LID_DROP + 1],
          [61, -LID_DROP],
        ],
        96,
      ),
    ),
    lidMat,
  );
  lidCone.renderOrder = 4;
  lid.add(lidCone);
  const disk = new THREE.Shape();
  disk.absarc(0, 0, 61.2, 0, Math.PI * 2, false);
  const hole = new THREE.Path();
  hole.absarc(0, 0, 12, 0, Math.PI * 2, true);
  disk.holes.push(hole);
  for (let i = 0; i < 14; i++) {
    const a = (i / 14) * Math.PI * 2;
    const h = new THREE.Path();
    h.absellipse(Math.cos(a) * 37, Math.sin(a) * 37, 3.6, 2.2, 0, Math.PI * 2, true, a);
    disk.holes.push(h);
  }
  // Brillo en el borde de cada orificio: en la fotografía se leen como gotas claras.
  const holeRimGeo = keep(new THREE.RingGeometry(1, 1.45, 28));
  holeRimGeo.rotateX(-Math.PI / 2);
  const holeRimMat = keep(new THREE.MeshBasicMaterial({ color: 0xe3f4ff, transparent: true, opacity: 0.85, depthWrite: false }));
  for (let i = 0; i < 14; i++) {
    const a = (i / 14) * Math.PI * 2;
    const rimMesh = new THREE.Mesh(holeRimGeo, holeRimMat);
    // El disco se tumba con rotateX(−π/2): la y del plano pasa a −z.
    rimMesh.position.set(Math.cos(a) * 37, -LID_DROP + 0.3, -Math.sin(a) * 37);
    rimMesh.rotation.y = a;
    rimMesh.scale.set(3.6, 1, 2.2);
    rimMesh.renderOrder = 5;
    lid.add(rimMesh);
  }
  const lidDiskGeo = keep(new THREE.ShapeGeometry(disk, 24));
  lidDiskGeo.rotateX(-Math.PI / 2);
  const lidDisk = new THREE.Mesh(lidDiskGeo, lidMat);
  lidDisk.position.y = -LID_DROP;
  lidDisk.renderOrder = 4;
  lid.add(lidDisk);
  const collar = new THREE.Mesh(
    keep(
      lathe(
        [
          [12, -LID_DROP],
          [12, -LID_DROP + 3.5],
          [14.5, -LID_DROP + 3.5],
          [15.5, -LID_DROP],
        ],
        64,
      ),
    ),
    shieldBand,
  );
  collar.renderOrder = 4;
  lid.add(collar);

  /* ── muestra: platillo de pesaje con polvo ──────────────────────────────── */
  const sample = new THREE.Group();
  sample.visible = false;
  root.add(sample);
  const dishMat = keep(new THREE.MeshStandardMaterial({ color: 0xfafafa, roughness: 0.35, side: DS }));
  const powderMat = keep(new THREE.MeshStandardMaterial({ color: 0xd9a54a, roughness: 0.95, side: DS }));
  sample.add(
    new THREE.Mesh(
      keep(
        lathe(
          [
            [0, 0],
            [17, 0],
            [24, 8],
            [23, 8.4],
            [16, 1],
            [0, 1],
          ],
          48,
        ),
      ),
      dishMat,
    ),
  );
  sample.add(
    new THREE.Mesh(
      keep(
        lathe(
          [
            [15.5, 1],
            [13, 2.5],
            [7, 5.5],
            [0, 6.5],
          ],
          48,
        ),
      ),
      powderMat,
    ),
  );

  /* ── consola inclinada: nivel, placa, pantalla y teclado ────────────────── */
  // `fascia` sigue la pendiente: x = ancho, z = distancia por la pendiente
  // desde SLOPE_Z (hacia el frente), y = normal de la cubierta.
  const fascia = new THREE.Group();
  fascia.position.set(0, DECK, SLOPE_Z);
  fascia.rotation.x = SLOPE;
  root.add(fascia);

  // Nivel de burbuja y placa de modelo.
  const bubbleRing = new THREE.Mesh(
    keep(
      lathe(
        [
          [0, 0],
          [7.5, 0],
          [7.5, 2.2],
          [6.5, 3],
          [5, 3],
          [5, 2],
          [0, 2],
        ],
        40,
      ),
    ),
    white,
  );
  bubbleRing.position.set(-8, 0, 24);
  fascia.add(bubbleRing);
  const bubbleDot = new THREE.Mesh(keep(new THREE.RingGeometry(1.6, 2.4, 32)), keep(new THREE.MeshBasicMaterial({ color: 0xc8303a })));
  bubbleDot.rotation.x = -Math.PI / 2;
  bubbleDot.position.set(-8, 2.1, 24);
  fascia.add(bubbleDot);
  const plate = new THREE.Mesh(keep(new THREE.BoxGeometry(28, 0.8, 10)), keep(new THREE.MeshStandardMaterial({ color: 0xf8f9fa, roughness: 0.3 })));
  plate.position.set(28, 0.4, 20);
  fascia.add(plate);

  // Marco de la pantalla (logo, franjas).
  const FW = 82;
  const FD = 44;
  const FZ = 56; // centro, por la pendiente
  const frameTex = keep(
    canvasTexture(820, 440, (g, w, h) => {
      g.fillStyle = '#e8ecef';
      g.fillRect(0, 0, w, h);
      g.strokeStyle = '#cfd5da';
      g.lineWidth = 8;
      g.strokeRect(4, 4, w - 8, h - 8);
      g.fillStyle = '#1f6fd0';
      g.font = `italic 700 46px ${SANS}`;
      g.textBaseline = 'alphabetic';
      g.fillText('Highland', 70, 76);
      g.font = `600 22px ${SANS}`;
      g.fillText('TM', 278, 50);
      g.beginPath();
      g.arc(42, 64, 9, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = '#2f8be6';
      g.fillRect(0, 336, 150, 12);
      g.fillRect(0, 366, 150, 12);
      g.fillRect(w - 80, 120, 80, 12);
      g.fillRect(w - 80, 150, 80, 12);
    }).texture,
  );
  const frame = new THREE.Mesh(keep(new THREE.BoxGeometry(FW, 1.4, FD)), [
    bezel,
    bezel,
    keep(new THREE.MeshStandardMaterial({ map: frameTex, roughness: 0.45 })),
    bezel,
    bezel,
    bezel,
  ]);
  frame.position.set(0, 0.7, FZ);
  fascia.add(frame);

  // Ventana LCD 70 × 30 mm (10 px por mm).
  const LW = 60;
  const LD = 30;
  // Encendida desde el principio: el póster y la portada muestran «0.000 g» como la fotografía.
  const screenOn = true;
  let readValue = 0;
  let stable = true;
  const lcd = canvasTexture(600, 300, (g, w, h) => {
    const lit = screenOn;
    const grad = g.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, lit ? '#3fa9ff' : '#8c9aa5');
    grad.addColorStop(1, lit ? '#1f7fe8' : '#76838d');
    g.fillStyle = grad;
    g.fillRect(0, 0, w, h);
    if (!lit) return;
    const ink = '#050c26';
    g.fillStyle = ink;
    g.strokeStyle = ink;
    // Dígitos de 18 mm, alineados a la derecha antes de la unidad.
    const DH = 150;
    const DY = 32;
    const PITCH = 90;
    const text = readValue.toFixed(3);
    const count = text.replace('.', '').length;
    let x = 505 - count * PITCH + (PITCH - DH * 0.5);
    for (const ch of text) {
      if (ch === '.') {
        g.fillRect(x - 22, DY + DH - 12, 16, 16);
        continue;
      }
      digit(g, ch, x, DY, DH);
      x += PITCH;
    }
    g.font = `500 72px ${SANS}`;
    g.textAlign = 'left';
    g.textBaseline = 'alphabetic';
    g.fillText('g', 525, 150);
    // Marcas de cero y de estable a la izquierda.
    g.font = `600 30px ${SANS}`;
    g.fillText('0', 18, 70);
    g.lineWidth = 5;
    g.beginPath();
    g.moveTo(42, 60);
    g.lineTo(54, 60);
    g.stroke();
    if (stable) {
      g.beginPath();
      g.moveTo(18, 156);
      g.lineTo(30, 156);
      g.arc(30, 168, 12, -Math.PI / 2, Math.PI / 2, false);
      g.lineTo(18, 180);
      g.stroke();
    }
    // Barra de capacidad: marcas y relleno según lo que hay en el plato.
    g.lineWidth = 4;
    for (let i = 0; i <= 20; i++) {
      const bx = 60 + i * 24;
      g.beginPath();
      g.moveTo(bx, 266);
      g.lineTo(bx, i % 5 === 0 ? 292 : 282);
      g.stroke();
    }
    const fill = Math.min(1, Math.max(0, readValue / CAPACITY_G));
    if (fill > 0.002) g.fillRect(60, 252, 480 * fill, 9);
  });
  keep(lcd.texture);
  const lcdMat = keep(
    new THREE.MeshStandardMaterial({ map: lcd.texture, emissive: 0xffffff, emissiveMap: lcd.texture, emissiveIntensity: 0, roughness: 0.25 }),
  );
  const lcdGeo = keep(new THREE.PlaneGeometry(LW, LD));
  lcdGeo.rotateX(-Math.PI / 2);
  const lcdMesh = new THREE.Mesh(lcdGeo, lcdMat);
  lcdMesh.position.set(4, 1.5, FZ + 4);
  fascia.add(lcdMesh);

  // Panel del teclado: tara amarilla, óvalo azul, logo ADAM.
  const KW = 86;
  const KD = 20;
  const KZ = 87;
  const KEYS: { label: string; at: P }[] = [
    { label: 'power', at: [-25, 3] },
    { label: 'Smpl', at: [-12.5, 3.5] },
    { label: 'Mode', at: [-4, -4] },
    { label: 'Print', at: [6.5, 4.5] },
    { label: '%', at: [15, -3.5] },
  ];
  const PXMM = 10;
  const keypadTex = keep(
    canvasTexture(KW * PXMM, KD * PXMM, (g, w, h) => {
      const mx = (x: number) => (x + KW / 2) * PXMM;
      const mz = (z: number) => (z + KD / 2) * PXMM;
      g.fillStyle = '#eceff1';
      g.fillRect(0, 0, w, h);
      g.fillStyle = '#f2d64b';
      // Tara abajo a la izquierda y arriba a la derecha, en media luna.
      g.beginPath();
      g.moveTo(0, h * 0.42);
      g.quadraticCurveTo(w * 0.17, h * 0.55, w * 0.33, h);
      g.lineTo(0, h);
      g.closePath();
      g.fill();
      g.beginPath();
      g.moveTo(w, h * 0.58);
      g.quadraticCurveTo(w * 0.83, h * 0.45, w * 0.67, 0);
      g.lineTo(w, 0);
      g.closePath();
      g.fill();
      g.fillStyle = '#5a5a3a';
      g.font = `600 30px ${SANS}`;
      g.textAlign = 'center';
      g.textBaseline = 'middle';
      g.fillText('>0/T<', w * 0.09, h * 0.84);
      g.fillText('>0/T<', w * 0.91, h * 0.16);
      // Óvalo azul que une las cuatro teclas de función.
      g.strokeStyle = '#2f8be6';
      g.lineWidth = 7;
      g.beginPath();
      g.ellipse(mx(1), mz(0), 14 * PXMM, 6 * PXMM, -0.05, 0, Math.PI * 2);
      g.stroke();
      // Logo ADAM.
      g.fillStyle = '#1f6fd0';
      g.font = `800 34px ${SANS}`;
      g.textAlign = 'left';
      g.fillText('ADAM', mx(24), mz(-6.8));
      g.fillRect(mx(18.5), mz(-8.6), 44, 5);
      g.fillRect(mx(18.5), mz(-7), 44, 5);
      g.fillRect(mx(18.5), mz(-5.4), 44, 5);
    }).texture,
  );
  const keypad = new THREE.Mesh(keep(new THREE.BoxGeometry(KW, 1.2, KD)), [
    bezel,
    bezel,
    keep(new THREE.MeshStandardMaterial({ map: keypadTex, roughness: 0.5 })),
    bezel,
    bezel,
    bezel,
  ]);
  keypad.position.set(0, 0.6, KZ);
  fascia.add(keypad);

  // Teclas redondas blancas con su rótulo.
  const keyGeo = keep(new THREE.CylinderGeometry(4.2, 4.6, 2.4, 32));
  const keySide = keep(new THREE.MeshStandardMaterial({ color: 0xfafbfc, roughness: 0.4 }));
  for (const { label, at } of KEYS) {
    const tex = keep(
      canvasTexture(128, 128, (g, w, h) => {
        g.fillStyle = '#fafbfc';
        g.fillRect(0, 0, w, h);
        g.strokeStyle = '#9aa3ab';
        g.lineWidth = 5;
        g.beginPath();
        g.arc(w / 2, h / 2, w / 2 - 4, 0, Math.PI * 2);
        g.stroke();
        g.fillStyle = '#222';
        g.strokeStyle = '#222';
        if (label === 'power') {
          g.lineWidth = 7;
          g.beginPath();
          g.arc(w / 2, h / 2 + 4, 24, -Math.PI / 2 + 0.6, (3 * Math.PI) / 2 - 0.6);
          g.stroke();
          g.beginPath();
          g.moveTo(w / 2, h / 2 - 26);
          g.lineTo(w / 2, h / 2 + 2);
          g.stroke();
          return;
        }
        g.textAlign = 'center';
        g.textBaseline = 'middle';
        g.font = `700 ${label.length > 2 ? 30 : 46}px ${SANS}`;
        g.fillText(label, w / 2, h / 2 + 2);
      }).texture,
    );
    const top = keep(new THREE.MeshStandardMaterial({ map: tex, roughness: 0.4 }));
    const k = new THREE.Mesh(keyGeo, [keySide, top, keySide]);
    k.position.set(at[0], 2.4, KZ + at[1]);
    // El mapa de la tapa de un cilindro corre por z: girado, el rótulo se lee desde el frente.
    k.rotation.y = Math.PI / 2;
    fascia.add(k);
  }

  /* ── anclas y vistas ────────────────────────────────────────────────────── */
  const anchor = (parent: THREE.Object3D, x: number, y: number, z: number) => {
    const o = new THREE.Object3D();
    o.position.set(x, y, z);
    parent.add(o);
    return o;
  };
  const screenAnchor = anchor(fascia, 4, 3, FZ + 4 - LD / 2 + 3);
  const keysAnchor = anchor(fascia, -6, 3, KZ);
  const consoleAnchor = anchor(fascia, 0, 0, 62);
  const panAnchor = anchor(root, 34, PAN_TOP + 2, ZC + 30);
  const shieldAnchor = anchor(root, -46, DECK + SHIELD_H - 6, ZC + 32);
  const handleAnchor = anchor(root, -70, LOBE_TOP + 1, -96);
  const shieldMid = anchor(root, 0, DECK + 30, ZC + 20);
  const wholeMid = anchor(root, 0, 64, -4);

  /* ── animación ──────────────────────────────────────────────────────────── */
  let sampleT = 0; // 0 sin muestra … 1 muestra en el plato y tapa cerrada
  let landedFor = -1; // segundos desde que la muestra tocó el plato
  let shown = '';

  const SAMPLE_UP = DECK + SHIELD_H + 60;
  const lidBump = (s: number) => Math.sin(Math.PI * smooth((s - 0.05) / 0.85));

  const redraw = () => {
    const state = screenOn ? `${readValue.toFixed(3)}|${stable}` : 'off';
    if (state === shown) return;
    shown = state;
    lcd.redraw();
  };
  redraw();

  return {
    root,
    anchors: {
      screen: screenAnchor,
      keys: keysAnchor,
      pan: panAnchor,
      shield: shieldAnchor,
      handle: handleAnchor,
    },
    views: {
      // Pantalla y teclado de frente, desde arriba como se usa.
      pantalla: { target: consoleAnchor, direction: new THREE.Vector3(0.18, 0.95, 0.85), distance: 430 },
      // El protector entero y el cuerpo debajo.
      plato: { target: shieldMid, direction: new THREE.Vector3(0.45, 0.55, 1), distance: 900 },
      // Protector y pantalla a la vez: la muestra entra y la lectura se asienta.
      muestra: { target: wholeMid, direction: new THREE.Vector3(0.3, 0.75, 1), distance: 900 },
      // Desde atrás y a un lado: el asa, el hueco de los dedos y el apoyo azul.
      asa: { target: wholeMid, direction: new THREE.Vector3(-0.95, 0.45, -0.75), distance: 950 },
    },
    yaw: 0.92,

    setPower(on) {
      // En esta balanza «Pesar»/«Retirar» mueven la muestra; la pantalla no se apaga.
      // La historia por scroll fija `userData.sample` justo después, así que su pose manda.
      root.userData.sample = on ? 1 : 0;
    },

    update(dt) {
      // Muestra: la tapa sube, el platillo baja al plato, la tapa cierra.
      const want = root.userData.sample ? 1 : 0;
      const step = dt / 2.4;
      sampleT = want > sampleT ? Math.min(1, sampleT + step) : Math.max(0, sampleT - step);
      lid.position.y = SHIELD_H + 48 * lidBump(sampleT);
      const drop = smooth((sampleT - 0.28) / 0.42);
      sample.visible = sampleT > 0.25;
      sample.position.set(0, SAMPLE_UP + (PAN_TOP - SAMPLE_UP) * drop, ZC);
      landedFor = drop >= 1 ? (landedFor < 0 ? 0 : landedFor + dt) : -1;

      // Lectura: sube al apoyar la muestra y se asienta con una oscilación amortiguada.
      if (landedFor >= 0) {
        const t = landedFor;
        readValue = t > 1.6 ? SAMPLE_G : SAMPLE_G * (1 - Math.exp(-3.2 * t) * Math.cos(9 * t));
        stable = t > 1.6;
      } else {
        readValue += (0 - readValue) * Math.min(1, dt * 6);
        if (Math.abs(readValue) < 0.0005) readValue = 0;
        stable = readValue === 0;
      }

      // Retroiluminación: siempre encendida.
      lcdMat.emissiveIntensity = 0.42;
      redraw();
    },

    dispose,
  };
}
