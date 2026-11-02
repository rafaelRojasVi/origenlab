/**
 * SERVA BlueVertical™ PRiME™ (BV-104) con fuente BluePower™ 600 PRIME.
 *
 * Medidas del fabricante
 *   https://www.serva.de/enDE/ProductDetails/4741_BV-104_BlueVertical_TM_PRiME_TM_Mini_Slab_Gel_Unit_0_0.html
 *   Cámara:       160 × 156 × 95 mm (W × H × D)
 *   Gel:          100 × 100 × 0,7 mm (gel propio o precolado)
 *   Tampón:       200 ml cámara interior, 450 ml cámara exterior
 *   Máximos:      500 V / 250 mA
 *   Temperatura:  4–65 °C de operación
 *
 * Estimaciones (sin fuente de medida directa — marcadas «est.»):
 *   Grosor paredes acrílico:   4 mm est.
 *   Altura del pie azul:       18 mm est.
 *   Grosor divisor interior:   6 mm est.
 *   Placas de vidrio:          3 mm c/u est.
 *   Peine y espaciadores:      8 mm de alto est.
 *   Conectores banana:         Ø 6 mm × 18 mm est.
 *   BluePower 600 PRIME (est.): 280 × 110 × 220 mm (W × H × D)
 */
import * as THREE from 'three';
import type { BuiltModel } from '../types';
import { SANS, canvasTexture, disposer } from './kit';

// ── Dimensiones de la cámara BlueVertical PRiME ───────────────────────────
const TW = 160;    // ancho exterior (X), fabricante
const TH = 156;    // alto exterior (Y), fabricante
const TD = 95;     // fondo exterior (Z), fabricante
const WL = 4;      // grosor pared acrílico, est.

// Formato del gel (fabricante)
const GW = 100;    // ancho gel
const GH = 100;    // alto gel
// GT = 0.7 mm (gel en sí, invisible a escala — modelamos las placas)

// Posiciones verticales dentro del tanque (est.)
const BASE_H = 18;              // altura del pie azul inferior
const GEL_BOT = BASE_H + 14;   // base del gel a ~32 mm
const GEL_TOP = GEL_BOT + GH;  // cima del gel a ~132 mm
const WELL_H = 9;               // altura de los pocillos
const UPPER_BUF_Y = GEL_TOP + WELL_H + 6; // nivel del tampón superior (~147 mm)

const PLATE_T = 3;              // grosor placa de vidrio, est.
const CASSETTE_Z = -3;          // z del eje del cassette (ligeramente al frente), est.

// Color SERVA
const SERVA_BLUE = 0x0d52a5;   // azul SERVA (est., de la fotografía oficial)

// ── Dimensiones BluePower 600 PRIME (est. de la fotografía) ───────────────
const PSW = 165;    // ancho, est.
const PSH = 90;     // alto, est.
const PSD = 135;    // profundidad, est.
// La cámara general mira desde +Z world hacia -Z.  Stage +Z es MÁS CERCANO
// a la cámara; stage -Z es MÁS LEJOS (detrás del tanque).  La fuente debe
// quedar en stage -Z para estar detrás del tanque, y a la derecha (+X).
const PS_CX = TW / 2 + 30 + PSW / 2;   // = 192.5 mm a la derecha del tanque
const PS_CZ = -(TD / 2 + 40);           // = -87.5 mm: detrás del tanque (lejos de cámara)

// ── Pocillos y bandas en la animación del gel ─────────────────────────────
// 10 carriles en el gel (1 escalera + 9 muestras, pero sólo 3 muestras)
const N_WELLS = 10;
// Escalera pre-teñida: [color, fracción del gel recorrida al final del ciclo]
// Velocidades comprimidas a [0.30–1.09] para que todos los carriles sean legibles
// desde la vista general en los ~7.6 s de carrera activa del ciclo de 9 s.
const LADDER: [string, number][] = [
  ['#003dc2', 0.30],  // ~250 kDa, azul marino
  ['#9900a0', 0.42],  // ~150 kDa, magenta
  ['#cc1414', 0.54],  // ~100 kDa, rojo
  ['#d46800', 0.64],  // ~75 kDa, naranja
  ['#c8a000', 0.75],  // ~50 kDa, amarillo
  ['#2d9e2d', 0.86],  // ~37 kDa, verde
  ['#009aab', 0.97],  // ~25 kDa, cian
  ['#5c3fd0', 1.09],  // ~15 kDa, violeta — cap at 0.93
];
// Muestras: [carril, color, velocidad]
const SAMPLES: [number, string, number][] = [
  [2, '#1a55cc', 0.48],
  [2, '#e07800', 0.73],
  [2, '#1a8840', 1.05],
  [4, '#bb1a1a', 0.55],
  [4, '#44771a', 0.90],
  [6, '#6600bb', 0.65],
  [6, '#bb6600', 0.95],
];

export function build(): BuiltModel {
  const { keep, dispose } = disposer();
  const root = new THREE.Group();

  // ── MATERIALES ─────────────────────────────────────────────────────────
  const tankGlass = keep(
    new THREE.MeshPhysicalMaterial({
      color: 0xeef8ff,
      roughness: 0.02,
      clearcoat: 1,
      clearcoatRoughness: 0.02,
      envMapIntensity: 2.4,
      transparent: true,
      opacity: 0.10,
      side: THREE.DoubleSide,
      depthWrite: false,
    }),
  );

  const bluePlasticMat = keep(
    new THREE.MeshPhysicalMaterial({
      color: SERVA_BLUE,
      roughness: 0.40,
      clearcoat: 0.45,
      clearcoatRoughness: 0.35,
    }),
  );

  const whitePlasticMat = keep(
    new THREE.MeshStandardMaterial({ color: 0xe6eaec, roughness: 0.72 }),
  );

  const glassPlate = keep(
    new THREE.MeshPhysicalMaterial({
      color: 0xeaf8ff,
      roughness: 0.02,
      clearcoat: 1,
      envMapIntensity: 2.0,
      transparent: true,
      opacity: 0.28,
      side: THREE.DoubleSide,
      depthWrite: false,
    }),
  );

  const bufferMat = keep(
    new THREE.MeshPhysicalMaterial({
      color: 0x9acce4,
      roughness: 0.06,
      clearcoat: 0.9,
      clearcoatRoughness: 0.1,
      transparent: true,
      opacity: 0.14,
      depthWrite: false,
    }),
  );

  const platinumMat = keep(
    new THREE.MeshPhysicalMaterial({
      color: 0xdeddd5,
      metalness: 1.0,
      roughness: 0.12,
    }),
  );

  const redJackMat = keep(
    new THREE.MeshStandardMaterial({ color: 0xcc2020, roughness: 0.30 }),
  );
  const blackJackMat = keep(
    new THREE.MeshStandardMaterial({ color: 0x111111, roughness: 0.50 }),
  );

  // ── PANEL FRONTAL DEL TANQUE: textura de marca ─────────────────────────
  const frontTex = canvasTexture(1024, 780, (g, w, h) => {
    g.clearRect(0, 0, w, h);
    // Sello "BlueVertical" (izquierda, centrado verticalmente)
    g.font = `600 44px ${SANS}`;
    g.fillStyle = `rgba(13,82,165,0.90)`;
    g.textBaseline = 'middle';
    g.fillText('BlueVertical', 38, h * 0.5);
    // PRiME debajo
    g.font = `500 30px ${SANS}`;
    g.fillStyle = `rgba(13,82,165,0.75)`;
    g.fillText('PRiME™', 46, h * 0.5 + 46);
    // Logotipo SERVA (derecha, grande)
    g.font = `800 88px ${SANS}`;
    g.fillStyle = `rgba(13,82,165,0.95)`;
    g.textAlign = 'right';
    g.fillText('SERVA', w - 34, h * 0.46);
    g.font = `400 18px ${SANS}`;
    g.fillStyle = `rgba(13,82,165,0.60)`;
    g.fillText('Electrophoresis Systems', w - 34, h * 0.46 + 36);
    g.textAlign = 'left';
  });
  keep(frontTex.texture);

  const frontMat = keep(
    new THREE.MeshPhysicalMaterial({
      map: frontTex.texture,
      roughness: 0.05,
      clearcoat: 1,
      clearcoatRoughness: 0.04,
      transparent: true,
      opacity: 0.18,
      depthWrite: false,
      side: THREE.FrontSide,
    }),
  );

  // ── GRUPO DEL TANQUE ─────────────────────────────────────────────────────
  const tankGroup = new THREE.Group();
  root.add(tankGroup);

  // Cinco paneles del tanque exterior (acrílico transparente)
  const addPanel = (
    w: number,
    h: number,
    d: number,
    mat: THREE.Material,
    x: number,
    y: number,
    z: number,
    ry = 0,
    order = 4,
  ) => {
    const m = new THREE.Mesh(keep(new THREE.BoxGeometry(w, h, d)), mat);
    m.position.set(x, y, z);
    m.rotation.y = ry;
    m.renderOrder = order;
    m.castShadow = false;
    tankGroup.add(m);
    return m;
  };

  // Pared trasera
  addPanel(TW, TH, WL, tankGlass, 0, TH / 2, TD / 2 - WL / 2);
  // Pared frontal (con marca)
  addPanel(TW, TH, WL, frontMat, 0, TH / 2, -TD / 2 + WL / 2, 0, 5);
  // Paredes laterales
  addPanel(WL, TH, TD, tankGlass, -TW / 2 + WL / 2, TH / 2, 0);
  addPanel(WL, TH, TD, tankGlass, TW / 2 - WL / 2, TH / 2, 0);
  // Fondo del tanque
  addPanel(TW, WL, TD, tankGlass, 0, WL / 2, 0);

  // ── PIES / BASE AZUL ───────────────────────────────────────────────────
  // Dos pilares laterales (el azul visible a través del acrílico)
  for (const xPos of [-TW / 2 + WL + 10, TW / 2 - WL - 10] as [number, number]) {
    const pillar = new THREE.Mesh(
      keep(new THREE.BoxGeometry(14, BASE_H - WL - 0.6, TD - WL * 2 - 6)),
      bluePlasticMat,
    );
    // Sobre el fondo del tanque, no dentro: compartían la cara y = 0 y parpadeaban.
    pillar.position.set(xPos, WL + 0.6 + (BASE_H - WL - 0.6) / 2, 0);
    pillar.castShadow = true;
    tankGroup.add(pillar);
  }
  // Barra frontal del pie
  {
    const bar = new THREE.Mesh(
      keep(new THREE.BoxGeometry(TW - WL * 2 - 2, BASE_H - WL - 0.6, 9)),
      bluePlasticMat,
    );
    // 1 mm separada de la pared: su cara coincidía con la cara interior del acrílico.
    bar.position.set(0, WL + 0.6 + (BASE_H - WL - 0.6) / 2, -(TD / 2 - WL - 5.5));
    bar.castShadow = true;
    tankGroup.add(bar);
  }
  // Barra trasera del pie
  {
    const bar = new THREE.Mesh(
      keep(new THREE.BoxGeometry(TW - WL * 2 - 2, BASE_H - WL - 0.6, 9)),
      bluePlasticMat,
    );
    bar.position.set(0, WL + 0.6 + (BASE_H - WL - 0.6) / 2, TD / 2 - WL - 5.5);
    bar.castShadow = true;
    tankGroup.add(bar);
  }

  // ── DIVISOR INTERIOR (cámara interior / cámara exterior) ──────────────
  // Marco U de plástico azul semiopaco que separa las dos cámaras de tampón.
  // Visible a través de las paredes de acrílico.
  const divMat = keep(
    new THREE.MeshPhysicalMaterial({
      color: SERVA_BLUE,
      roughness: 0.48,
      clearcoat: 0.3,
      // Opaco. Semitransparente, sus piezas se cruzaban con el tampón y el
      // acrílico (todos transparentes) y el orden de dibujo cambiaba al girar.
    }),
  );
  const divH = TH - WL - BASE_H;
  const divW = GW + 20;       // ancho del divisor, est.
  const divWL = 6;            // grosor de la pared del divisor, est.

  // (La pared de cierre del marco quedaba entre la cámara y el gel: se omite.)
  // Paredes laterales del divisor
  for (const xSide of [-divW / 2, divW / 2] as [number, number]) {
    const m = new THREE.Mesh(
      keep(new THREE.BoxGeometry(divWL, divH, TD - WL * 2 - divWL)),
      divMat,
    );
    m.position.set(xSide, BASE_H + 0.5 + divH / 2, WL + (TD - WL * 2 - divWL) / 2 - TD / 2);
    tankGroup.add(m);
  }

  // ── TAMPÓN (líquido visible a través de las paredes) ─────────────────
  // (La caja del tampón exterior se omite: un volumen azul transparente que
  // envolvía todo y desordenaba el dibujo de las demás piezas al girar.)
  // Cámara interior (tampón superior, 200 ml) — sobre el gel
  {
    const innerBufH = UPPER_BUF_Y - GEL_TOP - WELL_H;
    if (innerBufH > 0) {
      const m = new THREE.Mesh(
        keep(new THREE.BoxGeometry(GW + 8, innerBufH, TD / 2 - 10)),
        keep(
          new THREE.MeshPhysicalMaterial({
            color: 0xb4ddf5,
            roughness: 0.06,
            clearcoat: 0.9,
            transparent: true,
            opacity: 0.28,
            depthWrite: false,
          }),
        ),
      );
      m.position.set(0, GEL_TOP + WELL_H + innerBufH / 2, CASSETTE_Z);
      m.renderOrder = 3;
      tankGroup.add(m);
    }
  }

  // ── CASSETTE DEL GEL ──────────────────────────────────────────────────
  const cassetteGroup = new THREE.Group();
  cassetteGroup.position.set(0, 0, CASSETTE_Z);
  tankGroup.add(cassetteGroup);

  // Placa frontal de vidrio (cara hacia el observador)
  {
    const m = new THREE.Mesh(
      keep(new THREE.BoxGeometry(GW + 4, GH + WELL_H + 4, PLATE_T)),
      glassPlate,
    );
    m.position.set(0, GEL_BOT + (GH + WELL_H) / 2, -PLATE_T);
    m.renderOrder = 3;
    cassetteGroup.add(m);
  }
  // Placa trasera de vidrio
  {
    const m = new THREE.Mesh(
      keep(new THREE.BoxGeometry(GW + 4, GH + WELL_H + 4, PLATE_T)),
      glassPlate,
    );
    m.position.set(0, GEL_BOT + (GH + WELL_H) / 2, PLATE_T);
    m.renderOrder = 3;
    cassetteGroup.add(m);
  }
  // Espaciador blanco inferior (sella el cassette por abajo)
  {
    const m = new THREE.Mesh(
      keep(new THREE.BoxGeometry(GW + 4, 8, PLATE_T * 2 + 6)),
      whitePlasticMat,
    );
    m.position.set(0, GEL_BOT - 4, 0);
    cassetteGroup.add(m);
  }
  // Peine blanco (crea los pocillos en la parte superior del gel)
  {
    const combGroup = new THREE.Group();
    combGroup.position.set(0, GEL_TOP + WELL_H / 2, 0);
    cassetteGroup.add(combGroup);

    // Cuerpo del peine
    const comb = new THREE.Mesh(
      keep(new THREE.BoxGeometry(GW + 4, WELL_H + 2, PLATE_T * 2 + 4)),
      whitePlasticMat,
    );
    combGroup.add(comb);

    // Dientes del peine: 10 huecos
    const toothW = (GW - 8) / N_WELLS - 2;
    const startX = -(GW / 2 - 4);
    const step = (GW - 8) / N_WELLS;
    const toothMat = keep(new THREE.MeshStandardMaterial({ color: 0x222240, roughness: 0.8 }));
    for (let i = 0; i < N_WELLS; i++) {
      const tooth = new THREE.Mesh(
        keep(new THREE.BoxGeometry(toothW, WELL_H - 1, PLATE_T * 2 + 6)),
        toothMat,
      );
      tooth.position.set(startX + i * step + step / 2, -1, 0);
      combGroup.add(tooth);
    }
  }

  // ── TEXTURA DEL GEL (bandas animadas) ─────────────────────────────────
  let gelT = 0;
  let gelLevel = 0;
  let lastGelRedraw = 0;

  const GEL_CW = 512;
  const GEL_CH = 640;
  const GEL_WELL_PX = 90;  // píxeles reservados para la zona de pocillos
  const GEL_BODY_PX = GEL_CH - GEL_WELL_PX;

  const gelTex = canvasTexture(GEL_CW, GEL_CH, (g, w, h) => {
    // Fondo del gel (amarillo-crema, como gel con tampón Tris-glicina)
    g.fillStyle = '#f5f0e2';
    g.fillRect(0, 0, w, h);

    // Zona de gel de resolución
    g.fillStyle = '#ede8d5';
    g.fillRect(0, GEL_WELL_PX, w, GEL_BODY_PX);

    // Zona de gel de apilamiento (encima de la zona de resolución)
    g.fillStyle = '#d9d4c2';
    g.fillRect(0, 0, w, GEL_WELL_PX);

    // Línea de separación entre geles
    g.strokeStyle = 'rgba(120,110,80,0.35)';
    g.lineWidth = 2;
    g.beginPath();
    g.moveTo(0, GEL_WELL_PX);
    g.lineTo(w, GEL_WELL_PX);
    g.stroke();

    // Pocillos (ranuras oscuras en la parte superior)
    const laneW = w / N_WELLS;
    const wellPad = 5;
    g.fillStyle = '#1a1824';
    for (let i = 0; i < N_WELLS; i++) {
      g.fillRect(i * laneW + wellPad, 8, laneW - wellPad * 2, GEL_WELL_PX - 14);
    }

    if (gelLevel < 0.01) return;

    // Ciclo de migración: 10 s por pasada completa; fade-out en los últimos 13%
    // (~1.3 s) para un reinicio suave (bandas se desvanecen antes de volver al pozo).
    const CYCLE = 10.0;
    const phase = (gelT % CYCLE) / CYCLE;
    const cycleFade = phase > 0.87 ? (1 - phase) / 0.13 : 1.0;

    // Frente del colorante de carga (azul tenue, migra más rápido que las proteínas)
    const dyeFrac = Math.min(phase * 1.2 * gelLevel, 0.97);
    if (dyeFrac > 0.01) {
      const dyeY = GEL_WELL_PX + dyeFrac * GEL_BODY_PX;
      g.strokeStyle = `rgba(20,60,200,${0.45 * gelLevel})`;
      g.lineWidth = 1.5;
      g.setLineDash([5, 4]);
      g.beginPath();
      g.moveTo(0, dyeY);
      g.lineTo(w, dyeY);
      g.stroke();
      g.setLineDash([]);
    }

    // Bandas de la escalera (carril 0) — más gruesas y opacas para ser legibles en general
    for (const [color, speed] of LADDER) {
      const frac = Math.min(phase * speed * gelLevel, 0.93);
      if (frac < 0.005) continue;
      const bandY = GEL_WELL_PX + frac * GEL_BODY_PX;
      // Bandas más anchas para ser visibles desde lejos
      const bandH = 5 + frac * 6;
      g.globalAlpha = Math.min(0.97, 0.55 + frac * 0.42) * cycleFade;
      g.fillStyle = color;
      g.fillRect(wellPad, bandY - bandH / 2, laneW - wellPad * 2, bandH);
    }

    // Bandas de las muestras — más gruesas y opacas
    for (const [lane, color, speed] of SAMPLES) {
      const frac = Math.min(phase * speed * gelLevel, 0.93);
      if (frac < 0.005) continue;
      const bandY = GEL_WELL_PX + frac * GEL_BODY_PX;
      const bandH = 4 + frac * 5;
      g.globalAlpha = Math.min(0.95, 0.50 + frac * 0.45) * cycleFade;
      g.fillStyle = color;
      g.fillRect(lane * laneW + wellPad, bandY - bandH / 2, laneW - wellPad * 2, bandH);
    }

    g.globalAlpha = 1;
  });
  keep(gelTex.texture);

  // Plano del gel (visible entre las dos placas de vidrio del cassette).
  // renderOrder alto: se dibuja después de los objetos transparentes del tanque
  // (tampón, paredes) para que las bandas sean siempre visibles a través del acrílico.
  // DoubleSide: visible tanto desde el frente (-Z) como desde el fondo (+Z).
  const gelMat = keep(
    new THREE.MeshPhysicalMaterial({
      map: gelTex.texture,
      roughness: 0.2,
      clearcoat: 0.3,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: 0.97,
      depthWrite: false,
    }),
  );
  const gelPlane = new THREE.Mesh(keep(new THREE.PlaneGeometry(GW, GH + WELL_H)), gelMat);
  gelPlane.position.set(0, GEL_BOT + (GH + WELL_H) / 2, -0.5);
  gelPlane.renderOrder = 6;
  cassetteGroup.add(gelPlane);

  // ── ELECTRODOS DE PLATINO ─────────────────────────────────────────────
  // Cátodo superior (dentro de la cámara interior, en el tampón superior)
  {
    const rod = new THREE.Mesh(
      keep(new THREE.CylinderGeometry(1, 1, divW - 10, 16)),
      platinumMat,
    );
    rod.rotation.z = Math.PI / 2;
    rod.position.set(0, UPPER_BUF_Y - 6, CASSETTE_Z + 5);
    tankGroup.add(rod);
  }
  // Ánodo inferior (en la cámara exterior, tampón exterior)
  {
    const rod = new THREE.Mesh(
      keep(new THREE.CylinderGeometry(1, 1, TW - WL * 2 - 20, 16)),
      platinumMat,
    );
    rod.rotation.z = Math.PI / 2;
    rod.position.set(0, BASE_H + 8, 20);
    tankGroup.add(rod);
  }

  // ── CONECTORES BANANA (en la parte superior del tanque) ───────────────
  const addJack = (x: number, mat: THREE.Material) => {
    const g = new THREE.Group();
    g.position.set(x, TH, 0);
    tankGroup.add(g);
    // Cuerpo del jack
    const body = new THREE.Mesh(
      keep(new THREE.CylinderGeometry(4.5, 4.5, 14, 24)),
      mat,
    );
    body.position.y = 5;
    g.add(body);
    // Casquete superior
    const cap = new THREE.Mesh(
      keep(new THREE.SphereGeometry(4.5, 16, 8, 0, Math.PI * 2, 0, Math.PI / 2)),
      mat,
    );
    cap.position.y = 12;
    g.add(cap);
    // Collar metálico
    const collar = new THREE.Mesh(
      keep(new THREE.CylinderGeometry(5.2, 5.2, 4, 24)),
      platinumMat,
    );
    collar.position.y = 0;
    g.add(collar);
    return g;
  };

  addJack(-24, redJackMat);
  addJack(24, blackJackMat);

  // ── BURBUJAS (electrólisis en los electrodos cuando hay corriente) ────
  const BUBBLE_COUNT = 280;
  const bubbles = new THREE.InstancedMesh(
    keep(new THREE.SphereGeometry(1.5, 8, 6)),
    keep(
      new THREE.MeshPhysicalMaterial({
        color: 0xeef8ff,
        roughness: 0.04,
        clearcoat: 1,
        transparent: true,
        opacity: 0.88,
        emissive: 0xd0eeff,
        emissiveIntensity: 0.28,
        depthWrite: false,
      }),
    ),
    BUBBLE_COUNT,
  );
  bubbles.renderOrder = 2;
  tankGroup.add(bubbles);

  const bDummy = new THREE.Object3D();
  bDummy.scale.setScalar(0);
  bDummy.updateMatrix();
  for (let i = 0; i < BUBBLE_COUNT; i++) bubbles.setMatrixAt(i, bDummy.matrix);

  // Estado de cada burbuja
  interface Bubble { x: number; y: number; z: number; vy: number; s: number; life: number; max: number; electrode: number }
  const bState: Bubble[] = Array.from({ length: BUBBLE_COUNT }, () => ({
    x: 0, y: 0, z: 0, vy: 0, s: 0, life: 0, max: 1, electrode: 0,
  }));

  // Spawnear una burbuja en un electrodo (0=cátodo superior, 1=ánodo inferior)
  const spawnBubble = (b: Bubble, electrode: number) => {
    b.electrode = electrode;
    if (electrode === 0) {
      // Cátodo: a lo largo del electrodo superior, dentro de la cámara interior
      b.x = (Math.random() - 0.5) * (divW - 14);
      b.y = UPPER_BUF_Y - 6;
      b.z = CASSETTE_Z + 5 + (Math.random() - 0.5) * 3;
    } else {
      // Ánodo: a lo largo del electrodo inferior, cámara exterior
      b.x = (Math.random() - 0.5) * (TW - WL * 2 - 24);
      b.y = BASE_H + 8;
      b.z = 20 + (Math.random() - 0.5) * 3;
    }
    b.vy = 4 + Math.random() * 6;
    b.s = 0.4 + Math.random() * 0.9;
    b.life = 0;
    b.max = 0.8 + Math.random() * 1.4;
  };

  // ── FUENTE DE ALIMENTACIÓN BluePower 600 PRIME ────────────────────────
  // La fuente está detrás y a la derecha del tanque. Con yaw=0.18, la cámara
  // general ve la cara -Z del psGroup (la cara del panel frontal con display).
  const psGroup = new THREE.Group();
  psGroup.position.set(PS_CX, 0, PS_CZ);
  root.add(psGroup);

  // Carcasa metálica: aluminio gris claro (top y frente), sin rejilla oscura
  const psMetal = keep(
    new THREE.MeshPhysicalMaterial({
      color: 0xcdd1d4,
      metalness: 0.70,
      roughness: 0.35,
    }),
  );
  const psBody = new THREE.Mesh(keep(new THREE.BoxGeometry(PSW, PSH, PSD)), psMetal);
  psBody.position.set(0, PSH / 2, 0);
  psBody.castShadow = true;
  psBody.receiveShadow = true;
  psGroup.add(psBody);

  // Tapas laterales azules SERVA (izquierda y derecha)
  const psBlueMat = keep(
    new THREE.MeshPhysicalMaterial({
      color: SERVA_BLUE,
      roughness: 0.35,
      clearcoat: 0.5,
      clearcoatRoughness: 0.3,
    }),
  );
  // Las tapas envuelven la caja 1,5 mm por cada lado: con la misma altura y
  // fondo, sus caras coincidían con las de la caja y parpadeaban al girar.
  for (const [xS] of [[-PSW / 2 + 3], [PSW / 2 - 3]]) {
    const cap = new THREE.Mesh(
      keep(new THREE.BoxGeometry(10, PSH + 3, PSD + 3)),
      psBlueMat,
    );
    cap.position.set(xS, PSH / 2, 0);
    psGroup.add(cap);
  }

  // Panel frontal con textura (cara -Z local = cara visible a la cámara con yaw=0.18)
  let psOn = false;
  let psDisplayV = 0;
  let psDisplayMA = 0;

  // Canvas del panel frontal: fondo plata, LCD verde pequeño, botones redondos, SERVA
  const psTex = canvasTexture(640, 320, (g, w, h) => {
    // Fondo plateado del panel
    g.fillStyle = '#c4c9cc';
    g.fillRect(0, 0, w, h);

    // Columna de jacks (4 pares rojo/negro) en el lado izquierdo
    const jColX = 40;
    const jStartY = h * 0.12;
    const jPairH = (h * 0.76) / 4;
    for (let i = 0; i < 4; i++) {
      const cy = jStartY + i * jPairH + jPairH / 2;
      // Jack rojo
      g.fillStyle = '#cc1414';
      g.beginPath(); g.arc(jColX, cy - 9, 9, 0, Math.PI * 2); g.fill();
      g.strokeStyle = '#882020'; g.lineWidth = 1.5;
      g.beginPath(); g.arc(jColX, cy - 9, 9, 0, Math.PI * 2); g.stroke();
      // Jack negro
      g.fillStyle = '#1a1a1a';
      g.beginPath(); g.arc(jColX, cy + 9, 9, 0, Math.PI * 2); g.fill();
      // Triángulo de advertencia amarillo
      g.fillStyle = '#e6b800';
      g.font = `bold 11px ${SANS}`;
      g.textBaseline = 'middle'; g.textAlign = 'left';
      g.fillText('▲', jColX + 14, cy);
    }

    // Pantalla LCD verde sobre fondo oscuro (centro-izquierda del panel)
    const lcdX = 78, lcdY = 14, lcdW = 210, lcdH = h - 28;
    g.fillStyle = '#181c10';
    g.fillRect(lcdX, lcdY, lcdW, lcdH);
    if (psOn) {
      g.fillStyle = '#88cc44';
      g.font = `bold 42px ${SANS}`;
      g.textBaseline = 'top'; g.textAlign = 'left';
      g.fillText(`${Math.round(psDisplayV)} V`, lcdX + 12, lcdY + 10);
      g.fillStyle = '#66aa22';
      g.font = `500 26px ${SANS}`;
      g.fillText(`${Math.round(psDisplayMA)} mA`, lcdX + 12, lcdY + 66);
      // Barra de progreso
      g.fillStyle = '#2a3a18';
      g.fillRect(lcdX + 12, lcdY + 108, lcdW - 24, 8);
      const prog = Math.min(1, psDisplayV / 180) * (lcdW - 24);
      g.fillStyle = '#88cc44';
      g.fillRect(lcdX + 12, lcdY + 108, prog, 8);
    }

    // Botones redondos: OK (verde-azul), ↑ (azul), ↓ (azul), ▶ (rojo)
    const btnDefs: [string, string][] = [
      ['OK', '#1a5f1a'],
      ['↑', '#0d52a5'],
      ['↓', '#0d52a5'],
      ['▶', '#aa1111'],
    ];
    const btnStartX = lcdX + 12;
    const btnY = h - 62;
    for (let i = 0; i < btnDefs.length; i++) {
      const [lbl, col] = btnDefs[i];
      const bx = btnStartX + i * 48;
      g.fillStyle = col;
      g.beginPath(); g.arc(bx + 16, btnY, 16, 0, Math.PI * 2); g.fill();
      g.fillStyle = '#fff';
      g.font = `600 13px ${SANS}`;
      g.textBaseline = 'middle'; g.textAlign = 'center';
      g.fillText(lbl, bx + 16, btnY);
    }

    // «BluePower™ 600 PRiME™» pequeño bajo los botones
    g.fillStyle = '#444';
    g.font = `400 11px ${SANS}`;
    g.textBaseline = 'bottom'; g.textAlign = 'left';
    g.fillText('BluePower™ 600 PRiME™', lcdX + 12, h - 6);

    // Logo SERVA arriba a la derecha
    g.fillStyle = '#0d52a5';
    g.font = `800 38px ${SANS}`;
    g.textBaseline = 'top'; g.textAlign = 'right';
    g.fillText('SERVA', w - 16, 14);
    g.textAlign = 'left';
  });
  keep(psTex.texture);

  const psFrontMat = keep(
    new THREE.MeshPhysicalMaterial({
      map: psTex.texture,
      roughness: 0.22,
      clearcoat: 0.7,
      // Offset outward so the panel never z-fights with the psBody face it sits on.
      polygonOffset: true,
      polygonOffsetFactor: -2,
      polygonOffsetUnits: -2,
    }),
  );
  // Panel frontal en la cara +Z del psGroup. Desplazado +2 mm más allá de la
  // cara del cuerpo para eliminar z-fighting: el panel sobresale 2 mm del cuerpo.
  const psFront = new THREE.Mesh(
    keep(new THREE.BoxGeometry(PSW - 20, PSH - 2, 4)),
    psFrontMat,
  );
  psFront.position.set(0, PSH / 2, PSD / 2 + 2);
  psGroup.add(psFront);

  // Tapa derecha azul con texto "SERVA" (cara +X, visible desde la cámara)
  {
    const sideTexRight = canvasTexture(512, 256, (g, w, h) => {
      g.fillStyle = `#0d52a5`;
      g.fillRect(0, 0, w, h);
      g.font = `800 70px ${SANS}`;
      g.fillStyle = 'rgba(255,255,255,0.88)';
      g.textBaseline = 'middle'; g.textAlign = 'center';
      g.fillText('SERVA', w / 2, h / 2);
    });
    keep(sideTexRight.texture);
    const capRight = new THREE.Mesh(
      keep(new THREE.BoxGeometry(2, PSH - 4, PSD - 4)),
      keep(new THREE.MeshPhysicalMaterial({
        map: sideTexRight.texture,
        roughness: 0.36,
        color: SERVA_BLUE,
      })),
    );
    // Por fuera de la tapa azul (su cara exterior está en PSW/2 + 2), no dentro.
    capRight.position.set(PSW / 2 + 3.2, PSH / 2, 0);
    psGroup.add(capRight);
    // Interruptor de encendido (rocker) en la tapa derecha
    const rocker = new THREE.Mesh(
      keep(new THREE.BoxGeometry(4, 22, 10)),
      keep(new THREE.MeshStandardMaterial({ color: 0x111111, roughness: 0.6 })),
    );
    rocker.position.set(PSW / 2 + 5, PSH * 0.4, -PSD * 0.25);
    psGroup.add(rocker);
  }

  // Los jacks de la fuente se muestran sólo en el canvas del panel frontal;
  // no se añaden cilindros 3D para evitar que floten fuera del dibujo.

  // Pies de goma de la fuente (4 esquinas)
  {
    const footMat = keep(new THREE.MeshStandardMaterial({ color: 0x111111, roughness: 0.9 }));
    const footGeo = keep(new THREE.CylinderGeometry(7, 8, 5, 16));
    for (const [fx, fz] of [
      [-PSW / 2 + 14, -PSD / 2 + 14],
      [PSW / 2 - 14, -PSD / 2 + 14],
      [-PSW / 2 + 14, PSD / 2 - 14],
      [PSW / 2 - 14, PSD / 2 - 14],
    ] as [number, number][]) {
      const foot = new THREE.Mesh(footGeo, footMat);
      foot.position.set(fx, 2.5, fz);
      psGroup.add(foot);
    }
  }

  // ── CABLES (rojo y negro conectando el tanque con la fuente) ──────────
  const addCable = (
    from: THREE.Vector3,
    to: THREE.Vector3,
    color: number,
  ) => {
    // El arco sube MUY por encima de ambos extremos (pico ~187 mm) para
    // cruzar sobre el techo de la fuente y bajar a los enchufes por arriba,
    // nunca por delante del display.  El punto de control mezcla Z hacia el
    // destino sin adelantar el arco respecto a la cara del panel.
    const mid = new THREE.Vector3(
      from.x * 0.2 + to.x * 0.8,
      Math.max(from.y, to.y) + 60,
      from.z * 0.4 + to.z * 0.6,
    );
    const curve = new THREE.QuadraticBezierCurve3(from, mid, to);
    const geo = keep(new THREE.TubeGeometry(curve, 40, 2, 8, false));
    const mat = keep(new THREE.MeshStandardMaterial({ color, roughness: 0.6 }));
    const m = new THREE.Mesh(geo, mat);
    m.castShadow = true;
    root.add(m);
    return m;
  };

  // Cables: desde los jacks del tanque (tapa superior) hasta la columna
  // izquierda de enchufes del panel frontal de la fuente.
  // La columna de jacks en el canvas está a jColX=40 px de 640 px → 9 mm
  // desde el borde izquierdo del panel de 145 mm → X = PS_CX - PSW/2 + 9 ≈ 129.
  // Z = PS_CZ + PSD/2 + 4: 4 mm por delante de la cara +Z del cuerpo de la fuente
  // (la cara visible para la cámara, que viene desde +Z world).
  const jackRedWorld = new THREE.Vector3(-24, TH + 18, 0);
  const jackBlackWorld = new THREE.Vector3(24, TH + 18, 0);
  const psJackX = PS_CX - PSW / 2 + 9;          // columna de jacks en el panel
  const psJackZ = PS_CZ + PSD / 2 + 4;           // cara del display + 4 mm
  const psJackRed   = new THREE.Vector3(psJackX, PSH * 0.22, psJackZ);   // par superior rojo
  const psJackBlack = new THREE.Vector3(psJackX, PSH * 0.28, psJackZ);   // par superior negro

  addCable(jackRedWorld, psJackRed, 0xcc2222);
  addCable(jackBlackWorld, psJackBlack, 0x111111);

  // ── ANCLAS ────────────────────────────────────────────────────────────
  const wellsAnchor = new THREE.Object3D();
  wellsAnchor.position.set(0, GEL_TOP + WELL_H / 2, CASSETTE_Z - PLATE_T - 2);
  tankGroup.add(wellsAnchor);

  const gelAnchor = new THREE.Object3D();
  gelAnchor.position.set(-30, GEL_BOT + GH * 0.5, CASSETTE_Z - PLATE_T - 2);
  tankGroup.add(gelAnchor);

  const electrodeAnchor = new THREE.Object3D();
  electrodeAnchor.position.set(0, UPPER_BUF_Y - 6, CASSETTE_Z + 5);
  tankGroup.add(electrodeAnchor);

  // Ancla del display: 6 mm por delante de la cara +Z del psFront
  // (que es la cara visible a la cámara, ubicada en stage Z = PS_CZ + PSD/2 + 2)
  const displayAnchor = new THREE.Object3D();
  displayAnchor.position.set(PS_CX, PSH * 0.55, PS_CZ + PSD / 2 + 6);
  root.add(displayAnchor);

  // ── ESTADO ────────────────────────────────────────────────────────────
  let powered = false;

  return {
    root,
    anchors: {
      wells: wellsAnchor,
      gel: gelAnchor,
      electrode: electrodeAnchor,
      display: displayAnchor,
    },
    views: {
      pocillos: {
        target: wellsAnchor,
        direction: new THREE.Vector3(0.2, 0.0, -1).normalize(),
        distance: 180,
      },
      bandas: {
        target: gelAnchor,
        direction: new THREE.Vector3(0.1, 0.1, -1).normalize(),
        distance: 260,
      },
      fuente: {
        target: displayAnchor,
        // La cámara general y la vista fuente están en +Z stage (cara visible).
        // Se apunta desde ligeramente arriba-derecha para ver el display completo.
        direction: new THREE.Vector3(0.15, 0.15, 1).normalize(),
        distance: 240,
      },
    },
    // frameTop: el pico del arco de los cables queda en ~187 mm.
    frameTop: TH + 50,
    yaw: 0.30,
    setPower(on: boolean) {
      powered = on;
      psOn = on;
    },
    update(dt: number, t: number, level: number) {
      gelT = t;
      gelLevel = level;

      // Actualizar pantalla de la fuente (20 fps)
      const redrawInterval = 1 / 20;
      if (t - lastGelRedraw > redrawInterval) {
        lastGelRedraw = t;
        gelTex.redraw();

        // Animación de voltaje/corriente ilustrativa
        if (powered) {
          psDisplayV += (140 + 12 * Math.sin(t * 0.4) - psDisplayV) * 0.06;
          psDisplayMA += (180 + 15 * Math.sin(t * 0.6 + 1) - psDisplayMA) * 0.06;
        } else {
          psDisplayV += (0 - psDisplayV) * 0.12;
          psDisplayMA += (0 - psDisplayMA) * 0.12;
        }
        psTex.redraw();
      }

      // Burbujas de electrólisis
      for (let i = 0; i < BUBBLE_COUNT; i++) {
        const b = bState[i];
        if (b.life >= b.max || b.s === 0) {
          if (level > 0.25 && Math.random() < 0.18 * level) {
            spawnBubble(b, i < BUBBLE_COUNT / 2 ? 0 : 1);
          } else {
            bDummy.scale.setScalar(0);
            bDummy.updateMatrix();
            bubbles.setMatrixAt(i, bDummy.matrix);
            continue;
          }
        }
        b.life += dt;
        b.y += b.vy * dt * 12;
        b.x += (Math.random() - 0.5) * 0.3;
        b.z += (Math.random() - 0.5) * 0.2;

        // Limitar dentro del tanque
        const capY = b.electrode === 0 ? TH - WL - 2 : BASE_H + 80;
        if (b.y > capY) { b.life = b.max; continue; }

        const k = Math.min(1, b.life * 5) * (1 - b.life / b.max) * level;
        bDummy.position.set(b.x, b.y, b.z);
        bDummy.scale.setScalar(b.s * (0.5 + k * 0.8));
        bDummy.updateMatrix();
        bubbles.setMatrixAt(i, bDummy.matrix);
      }
      bubbles.instanceMatrix.needsUpdate = true;
    },
    dispose,
  };
}
