/**
 * OHAUS MB120, analizador de humedad con lámpara halógena, modelado sobre la
 * fotografía del fabricante que usa el sitio (`public/products/ohaus/mb120.jpg`,
 * ver `src/data/productImages.ts`).
 *
 * Todo en milímetros; la escena lo reduce a su escala al final. El frente mira
 * a +z; el ancho corre por x y la altura por y.
 *
 *   conjunto         211 de ancho × 355 de fondo × 180 de alto, tapa cerrada
 *   tapa abierta     373 de alto (cota del plano de dimensiones de OHAUS)
 *   plato            Ø 90, acero inoxidable
 *   pantalla         táctil a color de 4,3″ (95 × 54 mm)
 *
 * Lo que se ve en la foto y aquí se reproduce: carcasa blanca sobre una base
 * esculpida que baja hasta cuatro patas redondas rojas, una franja gris claro
 * a lo largo del borde inferior (más ancha atrás), la tapa de cantos muy
 * redondeados con la ventana ahumada oscura arriba (con su adhesivo de
 * advertencia) y la lengüeta oscura al frente, la pantalla en el frente
 * inclinado con la fila de teclas debajo, el indicador redondo a su derecha y
 * el rótulo OHAUS rojo en los costados.
 *
 * Estimaciones de forma (no salen de la fuente): el reparto de alturas (base 92,
 * tapa 88), la pendiente del frente (~41°), el ancho de carcasa (194; los 211
 * se alcanzan con las patas), la posición de la ventana y del plato, el ángulo
 * de apertura (~95°, el que da los 373 de alto) y los colores (OHAUS no publica
 * RAL). La interfaz de la pantalla imita la de la foto (biblioteca de métodos,
 * «10.05 %MC», barra verde) traducida al español.
 *
 * La tapa gira sobre una bisagra trasera y la gobierna la marcha: con el equipo
 * en marcha (`setPower(true)`) se abre y se queda abierta; al detenerlo se
 * cierra. La lámpara sólo brilla con la tapa abierta y el equipo en marcha. En
 * marcha, la pantalla recorre un secado (el %MC sube desde 0,00 hasta el
 * resultado, como en el equipo real); en reposo muestra el último resultado.
 * Una historia puede tomar el mando:
 *
 *   model.setLidAngle(1.2);            // radianes, 0 … LID_MAX; `null` vuelve al modo automático
 *   model.root.userData.lidOpen = 1;   // lo mismo en fracción 0 … 1; `undefined` = automático
 *   model.root.userData.dry = 0.5;     // avance del secado 0 … 1; `undefined` = automático
 *   model.root.userData.lidAngle;      // ángulo actual en radianes (sólo lectura)
 *
 * La vista `tapa` mira al interior con la tapa abierta; `pantalla`, de frente
 * a la pantalla.
 */
import * as THREE from 'three';
import type { BuiltModel } from '../types';
import { SANS, canvasTexture, lathe, type Draw } from './kit';

/* ── medidas ── */
const BASE_W = 194; // carcasa; las patas llegan a los 211
const GREY_W = 188;
const LID_W = 178;
const SEAM = 100; // altura de la junta tapa/base
const H = 180;
const HINGE_Z = -172;
const LID_FRONT = 98;
const LID_MAX = 1.66; // ~95°
const PAN_Z = -40;
const FOOT_X = 91;
const FOOT_Z = 158;
/** Resultado de la foto, en %MC: la cifra a la que llega cada secado. */
const RESULT_MC = 10.05;
const DRY_S = 9; // segundos de un secado automático a plena marcha

type Keep = <T extends { dispose(): void }>(item: T) => T;

/** Perfil lateral (u = z, v = y) extruido a lo ancho, centrado en x. */
function sideExtrude(shape: THREE.Shape, width: number, bevel: number) {
  const depth = width - 2 * bevel;
  const geo = new THREE.ExtrudeGeometry(shape, {
    depth,
    bevelEnabled: true,
    bevelSize: bevel,
    bevelThickness: bevel,
    bevelSegments: 6,
    curveSegments: 24,
  });
  geo.translate(0, 0, -depth / 2);
  // (u, v, extrusión) → (z, y, −x)
  geo.rotateY(-Math.PI / 2);
  return geo;
}

/**
 * Ensancha la pieza hacia abajo: cada vértice se abre en x por `1 + k(y)`. Así
 * la base esculpida de la foto se abre hacia las patas en vez de caer a plomo.
 */
function flare(geo: THREE.BufferGeometry, k: (y: number) => number) {
  const pos = geo.attributes.position;
  for (let i = 0; i < pos.count; i++) pos.setX(i, pos.getX(i) * (1 + k(pos.getY(i))));
  pos.needsUpdate = true;
  geo.computeVertexNormals();
  return geo;
}
const ramp = (y: number, from: number, to: number) => Math.min(1, Math.max(0, (from - y) / (from - to)));

/** Placa de esquinas redondeadas tendida en el plano x–z, de `t` de espesor hacia +y. */
function slab(w: number, d: number, t: number, r: number) {
  const s = new THREE.Shape();
  const x = -w / 2 + 1;
  const y = -d / 2 + 1;
  const ww = w - 2;
  const dd = d - 2;
  s.moveTo(x + r, y);
  s.lineTo(x + ww - r, y);
  s.quadraticCurveTo(x + ww, y, x + ww, y + r);
  s.lineTo(x + ww, y + dd - r);
  s.quadraticCurveTo(x + ww, y + dd, x + ww - r, y + dd);
  s.lineTo(x + r, y + dd);
  s.quadraticCurveTo(x, y + dd, x, y + dd - r);
  s.lineTo(x, y + r);
  s.quadraticCurveTo(x, y, x + r, y);
  const geo = new THREE.ExtrudeGeometry(s, { depth: t - 2, bevelEnabled: true, bevelSize: 1, bevelThickness: 1, bevelSegments: 3 });
  geo.translate(0, 0, 1);
  // forma y → −z, extrusión → +y
  geo.rotateX(-Math.PI / 2);
  return geo;
}

export function build(): BuiltModel & { setLidAngle(angle: number | null): void } {
  const root = new THREE.Group();
  const disposables: { dispose(): void }[] = [];
  const keep: Keep = (item) => {
    disposables.push(item);
    return item;
  };

  /* ── materiales ── */
  const white = keep(
    new THREE.MeshPhysicalMaterial({ color: 0xf1f1ee, roughness: 0.42, metalness: 0, clearcoat: 0.35, clearcoatRoughness: 0.35 }),
  );
  const grey = keep(new THREE.MeshPhysicalMaterial({ color: 0x949ba3, roughness: 0.55, clearcoat: 0.1, clearcoatRoughness: 0.5 }));
  const red = keep(new THREE.MeshStandardMaterial({ color: 0xc4141c, roughness: 0.6 }));
  const dark = keep(new THREE.MeshStandardMaterial({ color: 0x2b2e31, roughness: 0.5 }));
  const handleMat = keep(new THREE.MeshPhysicalMaterial({ color: 0x55595d, roughness: 0.45, clearcoat: 0.3 }));
  const smoked = keep(
    new THREE.MeshPhysicalMaterial({ color: 0x3b3f43, roughness: 0.22, metalness: 0.1, clearcoat: 1, clearcoatRoughness: 0.08 }),
  );
  const metal = keep(new THREE.MeshPhysicalMaterial({ color: 0x8a9096, roughness: 0.35, metalness: 0.85 }));
  const alu = keep(new THREE.MeshPhysicalMaterial({ color: 0xe2e4e6, metalness: 0.85, roughness: 0.3, side: THREE.DoubleSide }));
  const panMat = keep(
    new THREE.MeshPhysicalMaterial({ color: 0xcfd3d6, metalness: 1, roughness: 0.24, envMapIntensity: 1.2, side: THREE.DoubleSide }),
  );
  const reflectorMat = keep(
    new THREE.MeshPhysicalMaterial({ color: 0xe6dcc2, metalness: 1, roughness: 0.2, emissive: 0xff7a1a, emissiveIntensity: 0, side: THREE.DoubleSide }),
  );
  const lampMat = keep(new THREE.MeshStandardMaterial({ color: 0xfff2d8, roughness: 0.2, emissive: 0xff8a1f, emissiveIntensity: 0 }));

  /* ── base blanca: frente inclinado, borde inferior que sube hacia atrás ── */
  const B = 8;
  const P1 = new THREE.Vector2(104, SEAM - B); // canto superior del frente (u = z, v = y)
  const P2 = new THREE.Vector2(166, 30); // pie de la pendiente
  const shell = new THREE.Shape();
  shell.moveTo(-169.5, SEAM - B);
  shell.lineTo(P1.x, P1.y);
  shell.lineTo(P2.x, P2.y);
  shell.lineTo(169.5, 30);
  // Borde inferior: bajo al frente, sube hacia atrás y deja ver la franja gris.
  shell.quadraticCurveTo(60, 28, -169.5, 80);
  shell.lineTo(-169.5, SEAM - B);
  // Se abre ~5 % por debajo de la cintura (y < 66) hasta el borde inferior.
  const housing = new THREE.Mesh(keep(flare(sideExtrude(shell, BASE_W, B), (y) => 0.05 * ramp(y, 74, 22) ** 1.6)), white);
  housing.castShadow = true;
  housing.receiveShadow = true;
  root.add(housing);

  /* ── franja gris: el casco inferior, en arco entre las patas ── */
  const under = new THREE.Shape();
  under.moveTo(-170, 70);
  under.lineTo(150, 24);
  under.lineTo(166, 22);
  under.lineTo(166, 18);
  under.quadraticCurveTo(0, 34, -170, 18);
  under.lineTo(-170, 60);
  // Más abierta aún: llega a los 211 de ancho a la altura de las patas.
  const greyShell = new THREE.Mesh(keep(flare(sideExtrude(under, GREY_W, 6), (y) => 0.03 + 0.08 * ramp(y, 70, 12) ** 1.4)), grey);
  greyShell.castShadow = true;
  greyShell.receiveShadow = true;
  root.add(greyShell);

  /* ── patas: zapata roja y el ensanche gris que baja a ella ── */
  const footGeo = keep(lathe([[0, 0], [17.5, 0], [19.4, 1.8], [19.4, 11.4], [17.8, 13], [0, 13]], 40));
  const flareGeo = keep(lathe([[0, 12.5], [17, 12.5], [14.5, 15], [10.5, 20], [8, 26], [0, 26]], 40));
  for (const sx of [-1, 1]) {
    for (const sz of [-1, 1]) {
      const foot = new THREE.Mesh(footGeo, red);
      foot.position.set(sx * FOOT_X, 0, sz * FOOT_Z);
      foot.castShadow = true;
      root.add(foot);
      const flare = new THREE.Mesh(flareGeo, grey);
      flare.position.set(sx * (FOOT_X - 2), 0, sz * (FOOT_Z - 2));
      flare.castShadow = true;
      root.add(flare);
    }
  }

  /* ── rótulo OHAUS rojo en los costados, cerca del frente ── */
  const logoTex = canvasTexture(512, 128, (g, w, h) => {
    g.clearRect(0, 0, w, h);
    g.fillStyle = '#d8261c';
    g.font = `800 104px ${SANS}`;
    g.textBaseline = 'middle';
    g.textAlign = 'center';
    g.fillText('OHAUS', w / 2, h / 2 + 4);
  });
  keep(logoTex.texture);
  const logoMat = keep(
    new THREE.MeshStandardMaterial({ map: logoTex.texture, transparent: true, roughness: 0.45, polygonOffset: true, polygonOffsetFactor: -2 }),
  );
  const logoGeo = keep(new THREE.PlaneGeometry(72, 18));
  for (const sx of [-1, 1]) {
    const logo = new THREE.Mesh(logoGeo, logoMat);
    // Mira hacia afuera; en cada costado el texto se lee de atrás hacia adelante… visto desde ese lado.
    logo.rotation.y = sx * (Math.PI / 2);
    logo.position.set(sx * (BASE_W / 2 + 0.3), 84, 66);
    root.add(logo);
  }

  /* ── frente inclinado: placa blanca con teclas e indicador, y la pantalla ── */
  const along3 = new THREE.Vector3(0, P1.y - P2.y, P1.x - P2.x).normalize(); // sube por la pendiente
  const outward = new THREE.Vector3(0, P2.x - P1.x, P1.y - P2.y).normalize(); // hacia el frente y arriba
  const slopeLen = P1.distanceTo(P2);
  const faceW = BASE_W - 2 * B - 6;
  const faceH = slopeLen - 4;
  const faceMid = new THREE.Vector3(0, (P1.y + P2.y) / 2, (P1.x + P2.x) / 2).addScaledVector(outward, B + 0.3);
  const FPX = 6;
  // Medidas en la placa (mm desde su esquina superior izquierda).
  const DISP = { x: 16, y: 4, w: 95, h: 54 };
  const drawFace: Draw = (g, w, h) => {
    const k = w / faceW;
    const grad = g.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, '#f6f6f3');
    grad.addColorStop(1, '#e9e9e6');
    g.fillStyle = grad;
    g.fillRect(0, 0, w, h);
    // Marco de la pantalla: cristal gris claro, como en la foto.
    g.fillStyle = '#c3c8cd';
    g.beginPath();
    g.roundRect((DISP.x - 1.6) * k, (DISP.y - 1.6) * k, (DISP.w + 3.2) * k, (DISP.h + 3.2) * k, 2.5 * k);
    g.fill();
    // Indicador redondo (nivel) a la derecha de la pantalla.
    const cx = 138 * k;
    const cy = 15 * k;
    g.fillStyle = '#3a3e42';
    g.beginPath();
    g.arc(cx, cy, 7.5 * k, 0, Math.PI * 2);
    g.fill();
    const ig = g.createRadialGradient(cx - 2 * k, cy - 2 * k, k, cx, cy, 6 * k);
    ig.addColorStop(0, '#f4f6f7');
    ig.addColorStop(1, '#b9bfc4');
    g.fillStyle = ig;
    g.beginPath();
    g.arc(cx, cy, 5.8 * k, 0, Math.PI * 2);
    g.fill();
    g.strokeStyle = '#7d858c';
    g.lineWidth = 0.4 * k;
    g.beginPath();
    g.arc(cx, cy, 2.2 * k, 0, Math.PI * 2);
    g.stroke();
    // Fila de teclas táctiles bajo la pantalla, con su icono rojo.
    const keyY = DISP.y + DISP.h + 4.5;
    for (let i = 0; i < 4; i++) {
      const kx = (52 + i * 24) * k;
      const ky = keyY * k;
      const kw = 20 * k;
      const kh = 8.5 * k;
      g.fillStyle = '#fbfbfa';
      g.strokeStyle = '#b8bcbf';
      g.lineWidth = 0.5 * k;
      g.beginPath();
      g.roundRect(kx, ky, kw, kh, 1.2 * k);
      g.fill();
      g.stroke();
      g.strokeStyle = '#d8261c';
      g.fillStyle = '#d8261c';
      g.lineWidth = 0.55 * k;
      const mx = kx + kw / 2;
      const my = ky + kh / 2;
      g.beginPath();
      if (i === 0) {
        g.arc(mx, my, 2.4 * k, -Math.PI / 2 + 0.6, (3 * Math.PI) / 2 - 0.6);
        g.moveTo(mx, my - 3 * k);
        g.lineTo(mx, my - 0.6 * k);
        g.stroke();
      } else if (i === 1) {
        g.rect(mx - 3 * k, my - 1.4 * k, 6 * k, 3 * k);
        g.rect(mx - 1.8 * k, my - 2.8 * k, 3.6 * k, 1.4 * k);
        g.stroke();
      } else if (i === 2) {
        g.arc(mx - 1.5 * k, my, 2 * k, 0, Math.PI * 2);
        g.moveTo(mx + 1.4 * k, my - 1 * k);
        g.lineTo(mx + 4 * k, my - 1 * k);
        g.lineTo(mx + 2.7 * k, my + 1.4 * k);
        g.closePath();
        g.stroke();
      } else {
        g.font = `700 ${4.2 * k}px ${SANS}`;
        g.textAlign = 'center';
        g.textBaseline = 'middle';
        g.fillText('→T←', mx, my + 0.2 * k);
      }
    }
  };
  const face = canvasTexture(Math.round(faceW * FPX), Math.round(faceH * FPX), drawFace);
  keep(face.texture);
  const faceMat = keep(new THREE.MeshPhysicalMaterial({ map: face.texture, roughness: 0.4, clearcoat: 0.35, clearcoatRoughness: 0.3 }));
  const facePlate = new THREE.Mesh(keep(new THREE.PlaneGeometry(faceW, faceH)), faceMat);
  facePlate.position.copy(faceMid);
  facePlate.up.copy(along3);
  facePlate.lookAt(faceMid.clone().add(outward));
  root.add(facePlate);

  // Pantalla: lienzo propio, delante de la placa, con brillo propio.
  const SPX = 8;
  let progress = 0; // avance del secado 0 … 1
  let phase: 'rest' | 'drying' | 'done' = 'rest';
  let readout: number | null = null;
  /**
   * %MC = masa perdida respecto de la inicial: como en el equipo real, sube con
   * el secado desde 0,00, rápido al principio, hasta el resultado.
   */
  const mcAt = (p: number) => (RESULT_MC * (1 - Math.exp(-4 * p))) / (1 - Math.exp(-4));
  const drawScreen: Draw = (g, w, h) => {
    const s = w / 570;
    const W = 570;
    const Hh = h / s;
    g.save();
    g.scale(s, s);
    g.fillStyle = '#ffffff';
    g.fillRect(0, 0, W, Hh);
    g.textBaseline = 'middle';
    g.textAlign = 'left';
    // Encabezado: biblioteca de métodos.
    g.fillStyle = '#2a6fb6';
    g.fillRect(12, 12, 22, 20);
    g.fillStyle = '#fff';
    g.fillRect(16, 17, 14, 2.5);
    g.fillRect(16, 22, 14, 2.5);
    g.fillRect(16, 27, 10, 2.5);
    g.fillStyle = '#1d3557';
    g.font = `700 24px ${SANS}`;
    g.fillText('Biblioteca de métodos', 44, 23);
    // Iconos de color del encabezado.
    const icon = (x: number, color: string) => {
      g.strokeStyle = color;
      g.lineWidth = 3;
      g.beginPath();
      g.moveTo(x, 30);
      g.lineTo(x + 8, 14);
      g.lineTo(x + 14, 26);
      g.lineTo(x + 22, 12);
      g.stroke();
    };
    icon(318, '#2a6fb6');
    icon(410, '#e2731e');
    g.fillStyle = '#d8261c';
    g.fillRect(492, 12, 6, 20);
    g.fillStyle = '#f2b705';
    g.fillRect(500, 12, 6, 20);
    g.fillStyle = '#3fae49';
    g.fillRect(508, 12, 6, 20);
    g.fillStyle = '#e1e5e9';
    g.fillRect(12, 42, W - 24, 2);
    // Fila de datos del método.
    g.font = `500 14px ${SANS}`;
    const cols: [number, string, string][] = [
      [14, 'Método actual', 'Temp. de secado'],
      [150, 'Predet.', '105 °C'],
      [300, 'Perfil de secado', 'Criterio de fin'],
      [455, 'Estándar', 'Auto A'],
    ];
    for (const [x, a, b] of cols) {
      g.fillStyle = '#3b4652';
      g.fillText(a, x, 60);
      g.fillStyle = '#6b7682';
      g.fillText(b, x, 79);
    }
    // Marca OHAUS a la izquierda.
    g.fillStyle = '#d8261c';
    g.beginPath();
    g.moveTo(30, 160);
    g.lineTo(54, 112);
    g.lineTo(80, 160);
    g.closePath();
    g.fill();
    g.fillStyle = '#ffffff';
    g.beginPath();
    g.ellipse(55, 143, 11, 8, 0, 0, Math.PI * 2);
    g.fill();
    g.fillStyle = '#d8261c';
    g.font = `800 14px ${SANS}`;
    g.fillText('OHAUS', 32, 176);
    // Temperatura y tiempo del método.
    const seconds = phase === 'rest' ? 600 : Math.round(progress * 600);
    const clock = `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')} min`;
    g.fillStyle = '#2a6fb6';
    g.fillRect(104, 152, 4, 14);
    g.beginPath();
    g.arc(106, 168, 4.5, 0, Math.PI * 2);
    g.fill();
    g.fillStyle = '#26303a';
    g.font = `500 15px ${SANS}`;
    g.fillText('105 °C', 116, 158);
    g.fillText(clock, 116, 180);
    // La cifra: humedad en %MC; en reposo, el último resultado (el de la foto).
    const mc = readout ?? (phase === 'rest' ? RESULT_MC : mcAt(progress));
    const value = mc.toFixed(2);
    g.font = `500 96px ${SANS}`;
    g.textAlign = 'right';
    g.fillStyle = '#121418';
    g.fillText(value, 448, 150);
    g.textAlign = 'left';
    g.font = `500 34px ${SANS}`;
    g.fillText('%MC', 456, 162);
    // Barra de estado.
    const done = readout === null && phase !== 'drying';
    g.fillStyle = done ? '#2c9a36' : '#e07a00';
    g.fillRect(0, 210, W, 34);
    g.fillStyle = '#ffffff';
    g.font = `600 18px ${SANS}`;
    g.textAlign = 'center';
    g.fillText(done ? 'Prueba terminada. Pulse tara.' : 'Secando…', W / 2, 228);
    // Fecha y modelo.
    g.textAlign = 'right';
    g.fillStyle = '#6b7682';
    g.font = `500 13px ${SANS}`;
    g.fillText('01/11/2026  17:58', W - 14, 258);
    g.fillStyle = '#26303a';
    g.font = `800 15px ${SANS}`;
    g.fillText('MB120', W - 14, 278);
    // Franja inferior: usuario.
    g.fillStyle = '#26323e';
    g.fillRect(0, Hh - 38, W, 38);
    g.fillStyle = '#2a6fb6';
    g.fillRect(12, Hh - 29, 20, 20);
    g.fillStyle = '#ffffff';
    g.textAlign = 'left';
    g.font = `500 15px ${SANS}`;
    g.fillText('Administrador', 42, Hh - 19);
    g.restore();
  };
  const screen = canvasTexture(DISP.w * SPX, DISP.h * SPX, drawScreen);
  keep(screen.texture);
  const screenMat = keep(
    new THREE.MeshPhysicalMaterial({
      map: screen.texture,
      emissive: 0xffffff,
      emissiveMap: screen.texture,
      emissiveIntensity: 0.32,
      roughness: 0.12,
      clearcoat: 1,
      clearcoatRoughness: 0.05,
      envMapIntensity: 0.5,
    }),
  );
  const screenMesh = new THREE.Mesh(keep(new THREE.PlaneGeometry(DISP.w, DISP.h)), screenMat);
  // Centro de la pantalla en la placa: x desde el centro, y hacia arriba de la pendiente.
  const dispCx = DISP.x + DISP.w / 2 - faceW / 2;
  const dispCy = faceH / 2 - (DISP.y + DISP.h / 2);
  const screenPos = faceMid
    .clone()
    .addScaledVector(new THREE.Vector3(1, 0, 0), dispCx)
    .addScaledVector(along3, dispCy)
    .addScaledVector(outward, 0.25);
  screenMesh.position.copy(screenPos);
  screenMesh.up.copy(along3);
  screenMesh.lookAt(screenPos.clone().add(outward));
  root.add(screenMesh);

  /* ── cámara de secado sobre la base: aro, soporte, plato y muestra ── */
  const ring = new THREE.Mesh(keep(lathe([[64, 0], [72, 0], [72, 7], [70.5, 8.5], [64, 8.5], [64, 0]], 96)), alu);
  ring.position.set(0, SEAM, PAN_Z);
  root.add(ring);
  const floorGeo = keep(new THREE.CircleGeometry(64, 64));
  floorGeo.rotateX(-Math.PI / 2);
  const floor = new THREE.Mesh(floorGeo, dark);
  floor.position.set(0, SEAM + 0.3, PAN_Z);
  root.add(floor);
  const post = new THREE.Mesh(keep(new THREE.CylinderGeometry(6, 7, 6, 24)), metal);
  post.position.set(0, SEAM + 3, PAN_Z);
  root.add(post);
  const support = new THREE.Mesh(keep(new THREE.CylinderGeometry(30, 30, 1.6, 48)), metal);
  support.position.set(0, SEAM + 6.8, PAN_Z);
  root.add(support);
  const PAN_Y = SEAM + 7.6;
  const pan = new THREE.Mesh(
    keep(lathe([[0, 0], [40, 0], [43.2, 1.6], [45, 8.4], [46, 9.8], [45, 10.2], [43.9, 9], [42.2, 2.4], [0, 2.4]], 96)),
    panMat,
  );
  pan.castShadow = true;
  pan.receiveShadow = true;
  pan.position.set(0, PAN_Y, PAN_Z);
  root.add(pan);
  const damp = new THREE.Color(0x9a7a50);
  const dry = new THREE.Color(0xe6d8ae);
  const sampleMat = keep(new THREE.MeshStandardMaterial({ color: damp.clone(), roughness: 0.95, side: THREE.DoubleSide }));
  const sample = new THREE.Mesh(keep(lathe([[0, 6.4], [12, 6], [24, 4.6], [32, 3.2], [33, 2.4], [0, 2.4]], 48)), sampleMat);
  sample.position.set(0, PAN_Y, PAN_Z);
  root.add(sample);
  const plateTop = new THREE.Object3D();
  plateTop.position.set(0, PAN_Y + 9, PAN_Z);
  root.add(plateTop);

  /* ── tapa: casco de cantos redondeados que gira sobre la bisagra trasera ── */
  const lidPivot = new THREE.Group();
  lidPivot.position.set(0, SEAM, HINGE_Z);
  root.add(lidPivot);
  const lid = new THREE.Group();
  lidPivot.add(lid);
  const L = 12; // radio de los cantos laterales
  const RF = 18; // canto frontal superior (30 por fuera)
  const RR = 8; // canto trasero superior (20 por fuera)
  const zb = -176 + L;
  const zf = LID_FRONT - L;
  const yb = SEAM + L;
  const yt = H - L;
  const lidShape = new THREE.Shape();
  lidShape.moveTo(zb, yb);
  lidShape.lineTo(zf, yb);
  // La cubierta baja apenas hacia el frente (domo), como en la foto.
  const FALL = 5;
  lidShape.lineTo(zf, yt - FALL - RF);
  lidShape.quadraticCurveTo(zf, yt - FALL, zf - RF, yt - FALL);
  lidShape.quadraticCurveTo((zf - RF + zb + RR) / 2, yt + 1.5, zb + RR, yt);
  lidShape.quadraticCurveTo(zb, yt, zb, yt - RR);
  lidShape.lineTo(zb, yb);
  const lidGeo = keep(sideExtrude(lidShape, LID_W, L));
  lidGeo.translate(0, -SEAM, -HINGE_Z);
  const lidShell = new THREE.Mesh(lidGeo, white);
  lidShell.castShadow = true;
  lidShell.receiveShadow = true;
  lid.add(lidShell);
  /** Punto del mundo (tapa cerrada) en el marco de la tapa. */
  const toLid = (x: number, y: number, z: number) => new THREE.Vector3(x, y - SEAM, z - HINGE_Z);

  // Ventana ahumada oscura arriba, de la parte trasera hasta pasado el medio.
  const WIN_D = 128;
  const WIN_Z = -96;
  const windowMesh = new THREE.Mesh(keep(slab(146, WIN_D, 7, 12)), smoked);
  windowMesh.position.copy(toLid(0, H - 2, WIN_Z));
  windowMesh.castShadow = true;
  lid.add(windowMesh);
  // Adhesivo de advertencia amarillo.
  const warnTex = canvasTexture(128, 112, (g, w, h) => {
    g.clearRect(0, 0, w, h);
    g.fillStyle = '#f5c400';
    g.strokeStyle = '#111';
    g.lineWidth = 7;
    g.lineJoin = 'round';
    g.beginPath();
    g.moveTo(w / 2, 8);
    g.lineTo(w - 8, h - 8);
    g.lineTo(8, h - 8);
    g.closePath();
    g.fill();
    g.stroke();
    g.fillStyle = '#111';
    g.font = `900 52px ${SANS}`;
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText('!', w / 2, h / 2 + 14);
  });
  keep(warnTex.texture);
  const warnMat = keep(
    new THREE.MeshStandardMaterial({ map: warnTex.texture, transparent: true, roughness: 0.4, polygonOffset: true, polygonOffsetFactor: -2 }),
  );
  const warnGeo = keep(new THREE.PlaneGeometry(17, 15));
  warnGeo.rotateX(-Math.PI / 2);
  const warn = new THREE.Mesh(warnGeo, warnMat);
  warn.position.copy(toLid(14, H + 5.2, WIN_Z + WIN_D / 2 - 18));
  lid.add(warn);

  // Lengüeta oscura al frente de la tapa.
  const tabShape = new THREE.Shape();
  tabShape.moveTo(-23, 0);
  tabShape.lineTo(23, 0);
  tabShape.lineTo(21, 13);
  tabShape.lineTo(-21, 13);
  tabShape.closePath();
  const tabGeo = keep(new THREE.ExtrudeGeometry(tabShape, { depth: 10, bevelEnabled: true, bevelSize: 1.4, bevelThickness: 1.4, bevelSegments: 3 }));
  const tab = new THREE.Mesh(tabGeo, handleMat);
  tab.position.copy(toLid(0, SEAM + 14, LID_FRONT - 3));
  tab.castShadow = true;
  lid.add(tab);

  // Interior de la tapa: reflector y lámpara halógena en anillo sobre el plato.
  const reflectorGeo = keep(new THREE.CircleGeometry(66, 72));
  reflectorGeo.rotateX(Math.PI / 2);
  const reflector = new THREE.Mesh(reflectorGeo, reflectorMat);
  reflector.position.copy(toLid(0, SEAM - 0.4, PAN_Z));
  lid.add(reflector);
  const lampGeo = keep(new THREE.TorusGeometry(40, 3.4, 16, 72));
  lampGeo.rotateX(Math.PI / 2);
  const lamp = new THREE.Mesh(lampGeo, lampMat);
  lamp.position.copy(toLid(0, SEAM - 7, PAN_Z));
  lid.add(lamp);
  const cupGeo = keep(new THREE.CylinderGeometry(3.8, 3.8, 8, 16));
  for (const sx of [-1, 1]) {
    const cup = new THREE.Mesh(cupGeo, metal);
    cup.position.copy(toLid(sx * 11, SEAM - 4, PAN_Z - 40));
    lid.add(cup);
  }
  const glowTex = canvasTexture(128, 128, (g) => {
    const r = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    r.addColorStop(0, 'rgba(255,214,150,.95)');
    r.addColorStop(0.4, 'rgba(255,140,40,.4)');
    r.addColorStop(1, 'rgba(255,120,20,0)');
    g.fillStyle = r;
    g.fillRect(0, 0, 128, 128);
  });
  keep(glowTex.texture);
  const glowMat = keep(
    new THREE.SpriteMaterial({ map: glowTex.texture, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0 }),
  );
  const glow = new THREE.Sprite(glowMat);
  // La geometría del sprite es la que three comparte entre todos; liberarla sólo vacía su copia en la GPU.
  keep(glow.geometry);
  glow.scale.set(130, 130, 1);
  glow.position.copy(toLid(0, SEAM - 14, PAN_Z));
  lid.add(glow);

  // Bisagras traseras.
  const hingeGeo = keep(new THREE.CylinderGeometry(5, 5, 30, 20));
  hingeGeo.rotateZ(Math.PI / 2);
  for (const sx of [-1, 1]) {
    const hinge = new THREE.Mesh(hingeGeo, dark);
    hinge.position.set(sx * 66, SEAM + 1, HINGE_Z - 4);
    root.add(hinge);
  }

  /* ── anclas y vistas ── */
  const screenAnchor = new THREE.Object3D();
  screenAnchor.position.copy(screenPos);
  root.add(screenAnchor);
  const lidAnchor = new THREE.Object3D();
  lidAnchor.position.copy(toLid(0, H + 4, 20));
  lid.add(lidAnchor);
  const lampAnchor = new THREE.Object3D();
  lampAnchor.position.copy(lamp.position);
  lid.add(lampAnchor);
  // Punto entre la lámpara abierta y el plato: lo que mira la vista `tapa`.
  const chamber = new THREE.Object3D();
  chamber.position.set(0, 185, -110);
  root.add(chamber);

  root.userData.lidAngle = 0;
  let lidOverride: number | null = null;
  let powered = false;
  let angle = 0;
  let lampK = 0;
  let hold = 0;
  let lastScreen = -1;
  let drawnKey = '';

  return {
    root,
    anchors: { pantalla: screenAnchor, tapa: lidAnchor, lampara: lampAnchor, plato: plateTop },
    views: {
      pantalla: { target: screenAnchor, direction: outward.clone().add(new THREE.Vector3(0.2, 0.12, 0)), distance: 320 },
      tapa: { target: chamber, direction: new THREE.Vector3(0.4, 0.42, 1), distance: 760 },
    },
    frameTop: H + 4,
    // La foto mira el frente en tres cuartos desde la izquierda, con el costado largo a la vista.
    yaw: 0.75,
    setPower(on) {
      powered = on;
    },
    setReadout(percent) {
      readout = percent;
    },
    setLidAngle(value) {
      lidOverride = value === null ? null : Math.min(LID_MAX, Math.max(0, value));
    },
    update(dt, t, level) {
      // Tapa: abierta mientras hay marcha, salvo que una historia la fije.
      const userOpen = root.userData.lidOpen;
      const want =
        lidOverride !== null
          ? lidOverride
          : typeof userOpen === 'number'
            ? Math.min(1, Math.max(0, userOpen)) * LID_MAX
            : powered && level > 0.08
              ? LID_MAX
              : 0;
      angle += (want - angle) * Math.min(1, dt * 4);
      lid.rotation.x = -angle;
      root.userData.lidAngle = angle;

      // Secado: el avance lo fija la historia o corre solo mientras hay marcha.
      const userDry = root.userData.dry;
      if (typeof userDry === 'number') {
        progress = Math.min(1, Math.max(0, userDry));
        phase = progress >= 1 ? 'done' : 'drying';
        hold = 0;
      } else if (powered && level > 0.05) {
        if (phase === 'rest') {
          phase = 'drying';
          progress = 0;
        } else if (phase === 'drying') {
          progress = Math.min(1, progress + (dt * level) / DRY_S);
          if (progress >= 1) phase = 'done';
        } else {
          hold += dt;
          if (hold > 3) {
            hold = 0;
            phase = 'drying';
            progress = 0;
          }
        }
      } else if (!powered) {
        phase = 'rest';
        progress = 0;
        hold = 0;
      }
      sampleMat.color.copy(damp).lerp(dry, phase === 'rest' ? 0 : progress);

      // Pantalla: se repinta sólo cuando cambia lo que muestra, a lo sumo 15 veces por segundo.
      const key = `${phase}|${readout ?? ''}|${phase === 'rest' ? '' : progress.toFixed(3)}`;
      if (key !== drawnKey && t - lastScreen > 1 / 15) {
        drawnKey = key;
        lastScreen = t;
        screen.redraw();
      }

      // Lámpara: brillo cálido con un parpadeo mínimo, sólo con la tapa abierta y en marcha.
      const flick = 0.96 + 0.04 * Math.sin(t * 47) * Math.sin(t * 13);
      lampK += ((powered && angle > LID_MAX * 0.8 ? level : 0) - lampK) * Math.min(1, dt * 5);
      lampMat.emissiveIntensity = 3 * lampK * flick;
      reflectorMat.emissiveIntensity = 0.22 * lampK;
      glowMat.opacity = 0.8 * lampK * flick;
      panMat.emissive.setRGB(0.36 * lampK, 0.1 * lampK, 0.02 * lampK);
      panMat.emissiveIntensity = 0.25;
    },
    dispose() {
      for (const item of disposables) item.dispose();
      disposables.length = 0;
    },
  };
}
