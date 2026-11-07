/**
 * OHAUS MB32 — analizador de humedad por calentamiento de fibra de carbono.
 *
 * Geometría propia (no es un MB120 escalado): cuerpo más bajo, tapa con
 * varillas calefactoras en lugar de lámpara halógena, pantalla LCD de
 * segmentos (no táctil) y perilla de navegación.
 *
 * Dimensiones (mm): 211 × 353 × 137 (ancho × fondo × alto) con la tapa cerrada.
 *   Fuente: 3d_assets.md, tabla «MB32 vs MB120»: «211 × ~137 × ~353 mm». Esa
 *   tabla pone el MB120 como «211 × 355 × 180» (ancho × fondo × alto), de modo
 *   que en el MB32 se lee igual: 137 mm es el ALTO y 353 mm el FONDO (la tabla
 *   dice además «H=180 vs estimado ~130 mm» para el MB32). El alto incluye
 *   las patas (5 mm).
 *   Plato Ø 90 mm (brandModels / 3d_assets); el resto del detalle es estimado:
 *   consola inclinada 14°, tapa de 70 mm, varillas Ø 7 mm.
 *
 * Ejes: x = ancho, y = alto (0 = suelo), z = fondo; el FRENTE (consola) mira a +z
 * y la bisagra de la tapa está atrás (z = −176,5). La cámara general mira desde +z.
 *
 * Animación (`level` de 0 a 1):
 *   > 0,08       la tapa se abre (bisagra trasera, hasta 96°) y SE MANTIENE abierta
 *                mientras el equipo está en marcha; al apagar, se cierra
 *   abierta + `setPower(true)`   las varillas se ponen al rojo
 *   > 0          la humedad de la pantalla baja; vuelve a su valor inicial en reposo
 * El ángulo de la tapa se lee en `lidAngle` (radianes) y se fuerza con `setLidAngle`.
 */
import * as THREE from 'three';
import type { BuiltModel } from '../types';
import { SANS, canvasTexture, lathe, disposer } from './kit';

// ── Dimensiones (mm) ──────────────────────────────────────────────────────────
const W = 211; // ancho
const D = 353; // fondo
const H = 137; // alto total, con patas
const FEET = 5; // patas de goma
const FLOOR = 62; // suelo de la cámara de secado, sobre el fondo del cuerpo
const LID_H = H - FEET - FLOOR; // 70: alto de la tapa cerrada
const LID_D = 203; // fondo de la tapa (cubre la cámara)
const HALF_D = D / 2;
const PAN_R = 45; // plato Ø 90 mm
const PAN_S = -74; // z del centro del plato = centro de la cámara
const B = 6; // bisel de las piezas extruidas
const LID_OPEN = (96 * Math.PI) / 180;

// Consola: pendiente de la parte delantera, de (z, y) = (30, 76) a (176,5, 40).
const SLOPE_REAR = new THREE.Vector2(30, 76);
const SLOPE_FRONT = new THREE.Vector2(HALF_D, 40);

// Pantalla: tamaño del panel y mapa de la consola (mm sobre el panel).
const PW = 181;
const PL = 108;
const CV_W = 768;
const CV_H = Math.round((768 * PL) / PW);
const K = CV_W / PW; // píxeles por mm del lienzo

// Pantalla LCD de 4″ en diagonal: 88 × 50 mm útiles.
const LCD = { x: 15, y: 11, w: 88, h: 50 };
const KNOB = { x: 146, y: 36, r: 16 };
const KEY_LABELS = ['Start/Stop', 'Tare', 'Print', 'Menu'];
const KEY_X = (i: number) => 28 + i * 42; // centro de cada tecla, mm desde el borde izquierdo
const KEY_Y = 84;

// Lo que marca el equipo (cifras ilustrativas de pantalla, no especificaciones).
const MOIST_START = 18.4;
const MOIST_END = 0.42;
const DRY_RATE = 0.9; // % por segundo a nivel 1
const DRY_MASS = 4; // g de sólido seco de la muestra ilustrada
const SET_TEMP = 105;

// ── Geometría auxiliar ────────────────────────────────────────────────────────
type P = [number, number];

/** Desplaza hacia dentro (d > 0) un polígono en sentido antihorario. */
function inset(points: P[], d: number): P[] {
  const n = points.length;
  const normal = (a: P, b: P): P => {
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const len = Math.hypot(dx, dy);
    return [-dy / len, dx / len];
  };
  return points.map((p, i) => {
    const n1 = normal(points[(i + n - 1) % n], p);
    const n2 = normal(p, points[(i + 1) % n]);
    const k = d / (1 + n1[0] * n2[0] + n1[1] * n2[1]);
    return [p[0] + (n1[0] + n2[0]) * k, p[1] + (n1[1] + n2[1]) * k];
  });
}

/** Polígono con las esquinas redondeadas (radio máximo `r`). */
function roundedShape(points: P[], r: number): THREE.Shape {
  const n = points.length;
  const ends = points.map((p, i) => {
    const prev = points[(i + n - 1) % n];
    const next = points[(i + 1) % n];
    const toward = (q: P) => {
      const dx = q[0] - p[0];
      const dy = q[1] - p[1];
      const len = Math.hypot(dx, dy);
      const t = Math.min(r, len / 2);
      return new THREE.Vector2(p[0] + (dx / len) * t, p[1] + (dy / len) * t);
    };
    return { a: toward(prev), b: toward(next), p };
  });
  const shape = new THREE.Shape();
  shape.moveTo(ends[0].b.x, ends[0].b.y);
  for (let i = 1; i < n; i++) {
    shape.lineTo(ends[i].a.x, ends[i].a.y);
    shape.quadraticCurveTo(ends[i].p[0], ends[i].p[1], ends[i].b.x, ends[i].b.y);
  }
  shape.lineTo(ends[0].a.x, ends[0].a.y);
  shape.quadraticCurveTo(ends[0].p[0], ends[0].p[1], ends[0].b.x, ends[0].b.y);
  return shape;
}

/**
 * Perfil lateral (u = fondo → +z, y = alto) extruido a todo el ancho, con bisel.
 * `outer` es el contorno final, en sentido antihorario.
 */
function profileGeometry(outer: P[], radius: number): THREE.ExtrudeGeometry {
  const depth = W - 2 * B;
  const geo = new THREE.ExtrudeGeometry(roundedShape(inset(outer, B), radius), {
    depth,
    bevelEnabled: true,
    bevelSize: B,
    bevelThickness: B,
    bevelSegments: 4,
    curveSegments: 10,
  });
  geo.translate(0, 0, -depth / 2);
  geo.rotateY(-Math.PI / 2); // el eje del perfil (x) pasa a ser +z; la extrusión, el ancho
  return geo;
}

// ── Dígitos de siete segmentos ────────────────────────────────────────────────
const SEGMENTS: Record<string, string> = {
  '0': 'abcdef', '1': 'bc', '2': 'abdeg', '3': 'abcdg', '4': 'bcfg',
  '5': 'acdfg', '6': 'acdefg', '7': 'abc', '8': 'abcdefg', '9': 'abcdfg', '-': 'g', ' ': '',
};

function drawSegmentDigit(
  g: CanvasRenderingContext2D,
  ch: string,
  x: number,
  y: number,
  w: number,
  h: number,
  t: number,
  on: string,
  off: string,
) {
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
  const lit = SEGMENTS[ch] ?? '';
  for (const key of 'abcdefg') {
    g.fillStyle = lit.includes(key) ? on : off;
    g.beginPath();
    parts[key].forEach(([px, py], i) => (i ? g.lineTo(px, py) : g.moveTo(px, py)));
    g.closePath();
    g.fill();
  }
}

export interface Mb32Model extends BuiltModel {
  /** Ángulo actual de la tapa en radianes (0 cerrada, ~1,68 abierta). */
  readonly lidAngle: number;
  /** Fuerza el ángulo de la tapa (radianes); `null` la devuelve a su animación. */
  setLidAngle(angle: number | null): void;
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
    new THREE.MeshPhysicalMaterial({ color: 0xe6e7e3, roughness: 0.42, clearcoat: 0.25, clearcoatRoughness: 0.4 }),
  );
  const trimMat = keep(new THREE.MeshStandardMaterial({ color: 0x3a3e41, roughness: 0.55, metalness: 0.2 }));
  const chamberMat = keep(new THREE.MeshStandardMaterial({ color: 0x4b4f53, roughness: 0.5, metalness: 0.55 }));
  const steelMat = keep(
    new THREE.MeshPhysicalMaterial({ color: 0xbbbfc2, metalness: 0.88, roughness: 0.2, side: THREE.DoubleSide }),
  );
  const reflectorMat = keep(
    new THREE.MeshStandardMaterial({ color: 0xc9b98f, metalness: 0.85, roughness: 0.32, emissive: 0xff5a14, emissiveIntensity: 0 }),
  );
  const ceramicMat = keep(new THREE.MeshStandardMaterial({ color: 0xe9dfc9, roughness: 0.8 }));
  const rubberMat = keep(new THREE.MeshStandardMaterial({ color: 0x25282a, roughness: 0.9 }));
  const rodMat = keep(
    new THREE.MeshStandardMaterial({ color: 0x2b2522, roughness: 0.5, emissive: 0xff4a0e, emissiveIntensity: 0 }),
  );
  const sampleMat = keep(new THREE.MeshStandardMaterial({ color: 0xd8c48c, roughness: 0.9 }));
  const keyMat = keep(new THREE.MeshStandardMaterial({ color: 0x4d5256, roughness: 0.5 }));
  const startKeyMat = keep(new THREE.MeshStandardMaterial({ color: 0x2f6f48, roughness: 0.5 }));
  const knobMat = keep(new THREE.MeshStandardMaterial({ color: 0x1f2326, roughness: 0.4, metalness: 0.35 }));
  const knobMarkMat = keep(new THREE.MeshBasicMaterial({ color: 0xf2f2f0 }));

  // ── Estado ──────────────────────────────────────────────────────────────────
  let powered = false;
  let screenOn = false;
  let moisture = MOIST_START;
  let readout: number | null = null;
  let heat = 0;
  let lidAngle = 0;
  let lidOverride: number | null = null;
  let elapsed = 0;
  let lastRedraw = 0;
  let dirty = false; // la pantalla cambió y hay que repintarla

  // ── Cuerpo: perfil lateral con la cámara a la altura FLOOR y la consola delante ──
  const bodyOutline: P[] = [
    [-HALF_D, 0],
    [HALF_D, 0],
    [SLOPE_FRONT.x, SLOPE_FRONT.y],
    [SLOPE_REAR.x, SLOPE_REAR.y],
    [SLOPE_REAR.x, FLOOR],
    [-HALF_D, FLOOR],
  ];
  const body = add(lifted, profileGeometry(bodyOutline, 9), bodyMat);
  body.castShadow = true;
  body.receiveShadow = true;

  // Suelo oscuro de la cámara y aro de protección contra corrientes.
  add(lifted, new THREE.BoxGeometry(W - 22, 1.2, 150), chamberMat, 0, FLOOR + 0.5, PAN_S);
  const ring = add(lifted, new THREE.TorusGeometry(56, 1.8, 8, 72), steelMat, 0, FLOOR + 2.2, PAN_S);
  ring.rotation.x = Math.PI / 2;

  // Plato Ø 90 mm sobre su soporte, con la lengüeta para retirarlo.
  const PAN_Y = FLOOR + 10;
  add(lifted, new THREE.CylinderGeometry(9, 12, 10, 28), steelMat, 0, FLOOR + 5, PAN_S);
  add(
    lifted,
    lathe([[0, 0], [PAN_R - 5, 0], [PAN_R - 1, 1.5], [PAN_R, 5.2], [PAN_R - 0.9, 5.2], [PAN_R - 2, 2], [PAN_R - 5.4, 0.9], [0, 0.9]], 72),
    steelMat,
    0,
    PAN_Y,
    PAN_S,
  );
  add(lifted, new THREE.BoxGeometry(14, 1.2, 26), steelMat, 0, PAN_Y + 4.6, PAN_S + PAN_R + 10);
  // Muestra ilustrativa: un montículo que se aplana al secarse.
  const sampleGeo = keep(new THREE.SphereGeometry(22, 28, 14));
  sampleGeo.scale(1, 0.26, 1);
  const sample = new THREE.Mesh(sampleGeo, sampleMat);
  sample.position.set(0, PAN_Y + 1.6, PAN_S);
  sample.visible = false;
  lifted.add(sample);

  // Bisagra: dos cilindros a los lados, detrás del suelo de la cámara.
  for (const sx of [-1, 1]) {
    const barrel = add(lifted, new THREE.CylinderGeometry(7, 7, 38, 20), trimMat, sx * 62, FLOOR + 3, -HALF_D + 8.5);
    barrel.rotation.z = Math.PI / 2;
  }

  // Pies de goma.
  for (const sx of [-1, 1]) {
    for (const sz of [-1, 1]) {
      add(lifted, new THREE.CylinderGeometry(10, 11, FEET + 1.5, 20), rubberMat, sx * (W / 2 - 22), -FEET / 2 + 0.7, sz * (HALF_D - 26));
    }
  }

  // ── Tapa: articulada en el borde trasero, a la altura del suelo ─────────────
  const lid = new THREE.Group();
  lid.position.set(0, FLOOR, -HALF_D);
  lifted.add(lid);
  const lidOutline: P[] = [
    [0, 0],
    [LID_D, 0],
    [LID_D, 48],
    [LID_D - 24, LID_H],
    [26, LID_H],
    [0, LID_H - 24],
  ];
  // u = 0 en la bisagra y avanza hacia +z (el grupo `lid` ya está en el borde trasero).
  const lidShell = add(lid, profileGeometry(lidOutline, 9), bodyMat);
  lidShell.castShadow = true;
  // Agarradera delantera y rótulo en la cara superior.
  add(lid, new THREE.BoxGeometry(120, 10, 3), trimMat, 0, 26, LID_D + 0.6);
  const logoTex = canvasTexture(512, 128, (g, w, h) => {
    g.clearRect(0, 0, w, h);
    g.fillStyle = '#c01010';
    g.font = `900 ${h * 0.7}px ${SANS}`;
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText('OHAUS', w / 2, h / 2);
  });
  keep(logoTex.texture);
  const logoMat = keep(new THREE.MeshStandardMaterial({ map: logoTex.texture, transparent: true, roughness: 0.5 }));
  const logo = add(lid, new THREE.PlaneGeometry(72, 18), logoMat, 0, LID_H + 0.3, 0.55 * LID_D);
  logo.rotation.x = -Math.PI / 2;

  // Calefactor de fibra de carbono: reflector con tres varillas rectas bajo la tapa.
  const U_H = PAN_S + HALF_D; // distancia de la bisagra al centro del plato
  add(lid, new THREE.BoxGeometry(172, 2.6, 140), reflectorMat, 0, -1.3, U_H);
  const rods: THREE.Mesh[] = [];
  for (const dz of [-36, 0, 36]) {
    const rod = add(lid, new THREE.CylinderGeometry(3.6, 3.6, 150, 18), rodMat, 0, -6, U_H + dz);
    rod.rotation.z = Math.PI / 2;
    rods.push(rod);
    for (const sx of [-1, 1]) add(lid, new THREE.BoxGeometry(10, 11, 12), ceramicMat, sx * 80, -5.5, U_H + dz);
  }

  // ── Consola: panel con LCD, perilla y teclas, apoyado en la pendiente ───────
  const slopeDir = new THREE.Vector2().subVectors(SLOPE_REAR, SLOPE_FRONT).normalize(); // (z, y) hacia atrás y arriba
  const alongW = new THREE.Vector3(0, slopeDir.y, slopeDir.x); // el +y del panel en el mundo
  const xAxis = new THREE.Vector3(1, 0, 0);
  const normalW = new THREE.Vector3().crossVectors(xAxis, alongW); // sale de la pendiente, hacia arriba y adelante
  const panel = new THREE.Group();
  panel.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(xAxis, alongW, normalW));
  const mid = new THREE.Vector3(0, (SLOPE_REAR.y + SLOPE_FRONT.y) / 2, (SLOPE_REAR.x + SLOPE_FRONT.x) / 2).addScaledVector(normalW, 0.5);
  // El tramo útil de la pendiente es algo más corto que el panel hacia el hombro trasero: se corre 2 mm al frente.
  mid.addScaledVector(alongW, -2);
  panel.position.copy(mid);
  lifted.add(panel);

  const mm = (x: number, y: number) => new THREE.Vector3(x - PW / 2, PL / 2 - y, 0); // mm del lienzo → local del panel

  const drawPanel = (g: CanvasRenderingContext2D, w: number, h: number) => {
    g.fillStyle = '#2a2d30';
    g.fillRect(0, 0, w, h);
    g.strokeStyle = '#1b1d1f';
    g.lineWidth = 4;
    g.strokeRect(2, 2, w - 4, h - 4);

    // Marco de la pantalla.
    g.fillStyle = '#111315';
    g.beginPath();
    g.roundRect((LCD.x - 6) * K, (LCD.y - 4) * K, (LCD.w + 12) * K, (LCD.h + 8) * K, 10);
    g.fill();

    // LCD de segmentos.
    const lx = LCD.x * K;
    const ly = LCD.y * K;
    const lw = LCD.w * K;
    const lh = LCD.h * K;
    g.fillStyle = screenOn ? '#d3e2cb' : '#7b8878';
    g.fillRect(lx, ly, lw, lh);
    const ink = screenOn ? '#16221a' : 'rgba(30,40,32,0.18)';
    const ghost = screenOn ? 'rgba(22,34,26,0.07)' : 'rgba(30,40,32,0.1)';
    const shown = readout !== null ? Math.min(99.99, Math.max(0, readout)) : moisture;
    const text = screenOn ? shown.toFixed(2).padStart(5, ' ') : '88.88';
    let cx = lx + 14;
    const dw = 46;
    const dh = 100;
    for (const ch of text) {
      if (ch === '.') {
        g.fillStyle = ink;
        g.fillRect(cx - 5, ly + 14 + dh - 10, 10, 10);
        cx += 11;
      } else {
        drawSegmentDigit(g, ch, cx, ly + 14, dw, dh, 10, ink, ghost);
        cx += dw + 8;
      }
    }
    g.textBaseline = 'alphabetic';
    g.fillStyle = ink;
    g.textAlign = 'right';
    g.font = `800 34px ${SANS}`;
    g.fillText('%MC', lx + lw - 12, ly + 52);
    if (screenOn) {
      g.font = `700 18px ${SANS}`;
      g.fillText('MOISTURE', lx + lw - 12, ly + 82);
    }
    // Segunda línea: peso, y temperatura y tiempo al pie.
    g.textAlign = 'left';
    g.font = `700 34px "Courier New", monospace`;
    if (screenOn) {
      const mass = DRY_MASS / (1 - shown / 100);
      g.fillText(`${mass.toFixed(3)} g`, lx + 14, ly + 152);
      g.font = `700 22px "Courier New", monospace`;
      g.fillText(`${Math.round(25 + (SET_TEMP - 25) * heat)}°C`, lx + 14, ly + lh - 14);
      const s = Math.floor(elapsed);
      g.textAlign = 'right';
      g.fillText(`${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`, lx + lw - 12, ly + lh - 14);
    }

    // Perilla (marca de posición) y rótulos de marca.
    g.textAlign = 'center';
    g.fillStyle = '#c9cdd0';
    g.font = `700 ${5.5 * K}px ${SANS}`;
    g.fillText('MB32', KNOB.x * K, 12 * K);
    g.textAlign = 'left';
    g.fillStyle = '#d11a1a';
    g.font = `900 ${5 * K}px ${SANS}`;
    g.fillText('OHAUS', LCD.x * K, 74 * K);
    g.textAlign = 'center';
    g.fillStyle = '#aeb3b6';
    g.font = `600 ${3.6 * K}px ${SANS}`;
    KEY_LABELS.forEach((label, i) => g.fillText(label, KEY_X(i) * K, (KEY_Y + 13) * K, 36 * K));
  };
  const panelTex = canvasTexture(CV_W, CV_H, drawPanel);
  keep(panelTex.texture);
  const panelMat = keep(
    new THREE.MeshStandardMaterial({
      map: panelTex.texture,
      roughness: 0.55,
      envMapIntensity: 0.35,
      emissive: 0x9fd0ff,
      emissiveMap: panelTex.texture,
      emissiveIntensity: 0,
    }),
  );
  add(panel, new THREE.PlaneGeometry(PW, PL), panelMat);

  // Perilla de navegación: cilindro con aro más claro y una marca.
  const knob = new THREE.Group();
  const knobPos = mm(KNOB.x, KNOB.y);
  knob.position.copy(knobPos);
  panel.add(knob);
  const knobBody = add(knob, new THREE.CylinderGeometry(KNOB.r - 1, KNOB.r, 12, 48), knobMat, 0, 0, 6);
  knobBody.rotation.x = Math.PI / 2;
  const knobTop = add(knob, new THREE.CylinderGeometry(KNOB.r - 4, KNOB.r - 2.5, 3, 48), trimMat, 0, 0, 13);
  knobTop.rotation.x = Math.PI / 2;
  add(knob, new THREE.BoxGeometry(2.2, 8, 0.8), knobMarkMat, 0, KNOB.r - 6.5, 14.8);
  knob.rotation.z = 0.5;

  // Teclas en relieve.
  KEY_LABELS.forEach((label, i) => {
    const p = mm(KEY_X(i), KEY_Y);
    add(panel, new THREE.BoxGeometry(32, 12, 3), i === 0 ? startKeyMat : keyMat, p.x, p.y, 1.5);
  });

  // ── Anclas y vistas ─────────────────────────────────────────────────────────
  const place = (parent: THREE.Object3D, x: number, y: number, z: number) => {
    const o = new THREE.Object3D();
    o.position.set(x, y, z);
    parent.add(o);
    return o;
  };
  const lcdLocal = mm(LCD.x + LCD.w / 2, LCD.y + LCD.h / 2);
  const anchors = {
    pantalla: place(panel, lcdLocal.x, lcdLocal.y, 8),
    perilla: place(panel, knobPos.x, knobPos.y, 20),
    tapa: place(lid, 0, LID_H + 6, 0.5 * LID_D),
    calefactor: place(lid, 0, -14, U_H),
    plato: place(lifted, 0, PAN_Y + 14, PAN_S),
  };
  // Puntos fijos que miran las vistas (la tapa se mueve, la cámara no debe seguirla).
  const panelFocus = place(lifted, mid.x, mid.y, mid.z);
  const lidFocus = place(lifted, 0, FLOOR + 48, PAN_S);

  return {
    root,
    anchors,
    views: {
      pantalla: {
        target: panelFocus,
        direction: normalW.clone(),
        distance: 290,
      },
      tapa: {
        target: lidFocus,
        direction: new THREE.Vector3(-0.35, 0.55, 0.85).normalize(),
        distance: 620,
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

    setPower(on: boolean) {
      powered = on;
      if (on && !screenOn) {
        screenOn = true;
        dirty = true;
      }
    },

    setReadout(percent: number | null) {
      readout = percent;
      dirty = true;
    },

    update(dt: number, t: number, level: number) {
      // Tapa: se abre mientras el nivel está entre 0,08 y 0,5 y se cierra después.
      if (lidOverride === null) {
        const want = level > 0.08 ? LID_OPEN : 0;
        lidAngle += (want - lidAngle) * Math.min(1, dt * 3.5);
        if (Math.abs(want - lidAngle) < 0.002) lidAngle = want;
        lid.rotation.x = -lidAngle;
      }

      // Calefactor: se pone al rojo al encender y a partir de la mitad del recorrido.
      const heatWant = powered && lidAngle > LID_OPEN * 0.6 ? 1 : 0;
      heat += (heatWant - heat) * Math.min(1, dt * (heatWant ? 2.2 : 1.1));
      rodMat.emissiveIntensity = heat * 2.6;
      rodMat.color.setRGB(0.17 + heat * 0.55, 0.15 + heat * 0.1, 0.13);
      reflectorMat.emissiveIntensity = heat * 0.22;

      // Humedad: baja mientras haya nivel; en reposo vuelve al valor inicial.
      if (level > 0.001) {
        moisture = Math.max(MOIST_END, moisture - dt * DRY_RATE * level);
        if (powered) elapsed += dt * level;
      } else if (!powered) {
        moisture = MOIST_START;
        elapsed = 0;
      }

      // Muestra: aparece con la tapa abierta y se aplana al secarse.
      sample.visible = level > 0.2;
      sample.scale.set(1, 0.55 + 0.45 * ((moisture - MOIST_END) / (MOIST_START - MOIST_END)), 1);

      // Perilla: un giro lento mientras seca.
      knob.rotation.z = 0.5 + (powered ? Math.sin(t * 0.8) * 0.35 * level : 0);

      // Pantalla.
      if (screenOn && !powered && level < 0.02) {
        screenOn = false;
        dirty = true;
      }
      if (dirty || (screenOn && t - lastRedraw > 1 / 15)) {
        lastRedraw = t;
        dirty = false;
        panelTex.redraw();
      }
      panelMat.emissiveIntensity = screenOn ? 0.3 : 0;
    },

    dispose,
  };
}
