/**
 * OHAUS Pioneer PX — balanza analítica con protector de vidrio.
 *
 * Fuentes de dimensiones:
 *   Hoja de datos OHAUS PX (80775267-D) vía ei.uk/wp-content/uploads/ei/product/
 *     documents/Ohaus_Pioneer_PX_Datasheet.pdf (texto extraído con pdftotext):
 *       Dimensiones totales (W×D×H): 209×321×309 mm  ← D=321 mm es el fondo
 *       Cuerpo solo (modelo de precisión, sin protector): 209×321×98 mm → H_cuerpo=98 mm
 *       Dimensiones internas del protector: 172×131×205 mm (W×D×H)
 *   brandModels.ts: plato Ø 90 mm
 *
 * Estimados (marcados «est.»):
 *   Patas            Ø 26 mm, H ≈ 14 mm, con aro más claro en el centro
 *   Consola delantera  H desde 18 mm hasta 96 mm con pendiente ~29°
 *   Marco del protector  paredes blancas, cuadradas, 9×9 mm; franja roja en la tapa
 *   Pedestal del plato   Ø 8 mm, H ≈ 26 mm (est.)
 *   Masa ilustrativa   12,3456 g (solo en pantalla; no es especificación)
 *
 * Convención de ejes (igual que hielscher-up400st):
 *   Perfil en plano XY: X = fondo (0=trasero, D=delantero), Y = altura
 *   Extrusión en Z (eje del ancho)
 *   La cara delantera (consola con display) queda al frente (+X tras la rotación yaw)
 */
import * as THREE from 'three';
import type { BuiltModel } from '../types';
import { SANS, canvasTexture, lathe, labGlass, disposer } from './kit';

// ── Dimensiones (mm) ──────────────────────────────────────────────────────────
const W      = 209;   // ancho total
const D      = 321;   // fondo total (X en el perfil)
const H_BODY = 98;    // alto del cuerpo (de la hoja de datos: modelo de precisión)
const H_TOT  = 309;   // alto total con protector
const H_SH   = H_TOT - H_BODY;   // 211 mm — alto del protector
const PAN_R  = 45;    // radio del plato = Ø 90 / 2
const BEVEL  = 12;  // generous bevel → soft, sculpted corners

// Pendiente de la consola delantera
// De (D, 18) a (D-42, 96) → ~29° desde la vertical
const SL_BOT = new THREE.Vector2(D, 18);
const SL_TOP = new THREE.Vector2(D - 42, 96);
const slopeAlong   = new THREE.Vector3(SL_TOP.x - SL_BOT.x, SL_TOP.y - SL_BOT.y, 0).normalize();
const slopeOutward = new THREE.Vector3(slopeAlong.y, -slopeAlong.x, 0); // normal que apunta hacia afuera (+X, +Y)
const slopeLen     = SL_BOT.distanceTo(SL_TOP);  // ≈ 89 mm
const slopeMid2    = new THREE.Vector2(
  (SL_BOT.x + SL_TOP.x) / 2,
  (SL_BOT.y + SL_TOP.y) / 2,
);

// Protector: dimensiones del marco
// El protector se centra sobre la zona trasera del cuerpo.
// Delantera del protector a X_SF mm del frente del cuerpo.
const X_SF   = 102;  // fondo de la zona de consola (est.) → frente del protector en X=D-X_SF=219
const X_SR   = 24;   // borde trasero de los postes del protector en X=24
const SH_Z   = 90;   // semiancho de los postes del protector (centrados en Z=0)
const POST_W = 9;    // sección cuadrada de los postes (blanco)

// Masa ilustrativa (valor de pantalla; no es una especificación del equipo)
const SAMPLE_G = 12.3456;

export function build(): BuiltModel {
  const { keep, dispose } = disposer();
  const root = new THREE.Group();

  // ── Materiales ──────────────────────────────────────────────────────────────
  const bodyMat = keep(
    new THREE.MeshPhysicalMaterial({
      color: 0xf0ede8,
      roughness: 0.32,
      metalness: 0,
      clearcoat: 0.5,
      clearcoatRoughness: 0.22,
    }),
  );
  const panMat = keep(
    new THREE.MeshPhysicalMaterial({
      color: 0xbbbfc2,
      metalness: 0.88,
      roughness: 0.18,
    }),
  );
  const redMat = keep(
    new THREE.MeshStandardMaterial({ color: 0xc21212, roughness: 0.6 }),
  );
  const footRingMat = keep(
    new THREE.MeshStandardMaterial({ color: 0xe03030, roughness: 0.35, metalness: 0.05 }),
  );
  const glassMat  = keep(labGlass());
  // MeshBasicMaterial para la tapa: no recibe iluminación ni entorno, así
  // el panel horizontal no refleja los tonos cálidos del HDRI como losa roja.
  const topGlassMat = keep(
    new THREE.MeshBasicMaterial({
      color: 0xdff0ee,
      transparent: true,
      opacity: 0.10,
      side: THREE.DoubleSide,
      depthWrite: false,
    }),
  );
  const boatMat = keep(
    new THREE.MeshPhysicalMaterial({
      color: 0xf5f2e6,
      roughness: 0.7,
      transparent: true,
      opacity: 0,
      depthWrite: false,
    }),
  );
  const sampleMat = keep(
    new THREE.MeshStandardMaterial({
      color: 0xd4c28a,
      roughness: 0.85,
      transparent: true,
      opacity: 0,
      depthWrite: false,
    }),
  );

  // ── Canvas: consola delantera (LCD + teclado) ────────────────────────────────
  let screenOn    = false;
  let dispReading = 0;
  let stableFlag  = false;

  // El canvas tiene proporciones del área de la pendiente (≈ 200 × 150 px útiles)
  const CV_W = 512, CV_H = 384;

  const drawConsole = (g: CanvasRenderingContext2D, w: number, h: number) => {
    // Fondo del panel de consola (ligeramente más oscuro que el cuerpo blanco)
    g.fillStyle = '#eae7e2';
    g.fillRect(0, 0, w, h);
    // Borde interior oscuro (simula panel hundido)
    g.strokeStyle = '#c0bdb8';
    g.lineWidth = 3;
    g.strokeRect(1.5, 1.5, w - 3, h - 3);

    // ── Franja roja superior (rasgo de marca OHAUS) ──
    const stripeH = h * 0.065;
    g.fillStyle = '#c01010';
    g.fillRect(0, 0, w, stripeH);

    // «PIONEER» pequeño, blanco, arriba a la derecha sobre la franja
    g.fillStyle = '#ffffff';
    g.font = `700 ${stripeH * 0.62}px ${SANS}`;
    g.textAlign = 'right';
    g.textBaseline = 'middle';
    g.fillText('PIONEER', w - w * 0.04, stripeH / 2);

    // ── Ventana LCD ──────────────────────────────────────────────────────────
    const lx = w * 0.06;
    const ly = stripeH + h * 0.06;
    const lw = w * 0.72;
    const lh = h * 0.42;

    // Marco bezel negro del LCD
    g.fillStyle = '#1a1a1a';
    g.beginPath();
    g.roundRect(lx - 5, ly - 5, lw + 10, lh + 10, 5);
    g.fill();

    // Pantalla: blanco puro con retroiluminación cuando encendida
    const lcdBg = screenOn ? '#f8fffe' : '#cccfcc';
    g.fillStyle = lcdBg;
    g.fillRect(lx, ly, lw, lh);

    // Línea roja delgada en la parte superior del LCD
    g.fillStyle = '#c01010';
    g.fillRect(lx, ly, lw, 3);

    if (screenOn) {
      // Lectura principal: dígitos oscuros estilo LCD
      const txt = dispReading.toFixed(4);
      g.fillStyle = '#1a2a10';
      g.font = `700 ${lh * 0.54}px "Courier New", monospace`;
      g.textAlign = 'right';
      g.textBaseline = 'middle';
      g.fillText(txt, lx + lw - 8, ly + lh * 0.44);
      // Unidad
      g.font = `600 ${lh * 0.26}px ${SANS}`;
      g.fillText('g', lx + lw - 6, ly + lh * 0.76);
      // Indicador de estabilidad (cuadrado sólido, estilo LCD)
      if (stableFlag) {
        g.fillStyle = '#1a2a10';
        g.font = `700 ${lh * 0.22}px ${SANS}`;
        g.textAlign = 'left';
        g.textBaseline = 'middle';
        g.fillText('▪', lx + 6, ly + lh * 0.44);
      }
      // «Place sample» si en cero
      if (dispReading < 0.0005) {
        g.fillStyle = '#2a3a20';
        g.font = `400 ${lh * 0.17}px ${SANS}`;
        g.textAlign = 'center';
        g.textBaseline = 'bottom';
        g.fillText('Place sample', lx + lw / 2, ly + lh - 5);
      }
    } else {
      // Apagada: segmentos tenues
      g.fillStyle = '#b0b4b0';
      g.font = `700 ${lh * 0.46}px "Courier New", monospace`;
      g.textAlign = 'center';
      g.textBaseline = 'middle';
      g.fillText('– – – – –', lx + lw / 2, ly + lh * 0.48);
    }

    // ── Burbuja de nivel (bola de aire, a la derecha del LCD) ────────────────
    const bubR  = h * 0.055;
    const bubX  = lx + lw + (w - lx - lw) / 2;
    const bubY  = ly + lh * 0.42;
    g.strokeStyle = '#aaa8a0';
    g.lineWidth = 2;
    g.beginPath();
    g.arc(bubX, bubY, bubR, 0, Math.PI * 2);
    g.fillStyle = '#d8e8d0';
    g.fill();
    g.stroke();
    // Burbuja interior
    g.beginPath();
    g.arc(bubX + bubR * 0.08, bubY - bubR * 0.08, bubR * 0.3, 0, Math.PI * 2);
    g.fillStyle = 'rgba(255,255,255,0.75)';
    g.fill();

    // ── Tira de teclas (franja delgada, como en la foto) ────────────────────
    const kyTop  = ly + lh + h * 0.038;      // cerca del LCD
    const kyH    = h * 0.17;                  // franja delgada ≈ 65 px
    const labels = [['Zero', ''], ['Print', ''], ['Function', 'Mode'], ['Tare', 'Cal / On']];
    const kyW    = lw / labels.length;
    labels.forEach(([top, bot], i) => {
      const kx = lx + i * kyW + kyW / 2;
      // Tecla
      g.fillStyle = '#d0cdc8';
      g.beginPath();
      g.roundRect(kx - kyW * 0.44, kyTop + 2, kyW * 0.88, kyH - 4, 3);
      g.fill();
      g.strokeStyle = '#b0aca6';
      g.lineWidth = 0.8;
      g.stroke();
      // Label: fuente compacta con maxWidth para que «Function» no desborde
      const fSz = h * 0.037;
      const maxLabelW = kyW * 0.86;
      g.fillStyle = '#1e1e1e';
      g.font = `500 ${fSz}px ${SANS}`;
      g.textAlign = 'center';
      g.textBaseline = 'middle';
      const cy = bot ? kyTop + kyH * 0.32 : kyTop + kyH * 0.50;
      g.fillText(top, kx, cy, maxLabelW);
      if (bot) {
        g.fillStyle = '#505050';
        g.font = `400 ${fSz * 0.80}px ${SANS}`;
        g.fillText(bot, kx, kyTop + kyH * 0.70, maxLabelW);
      }
    });

    // ── Logo OHAUS (marca redonda pequeña, abajo a la izquierda) ────────────
    const logoR = h * 0.07;
    const logoX = lx * 0.5;
    const logoY = kyTop + kyH * 0.5;
    g.beginPath();
    g.arc(logoX, logoY, logoR, 0, Math.PI * 2);
    g.fillStyle = '#c01010';
    g.fill();
    g.beginPath();
    g.arc(logoX, logoY, logoR * 0.55, 0, Math.PI * 2);
    g.fillStyle = '#dddad4';
    g.fill();
    g.fillStyle = '#c01010';
    g.font = `700 ${logoR * 0.72}px ${SANS}`;
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText('O', logoX, logoY);
  };

  const consoleTex = canvasTexture(CV_W, CV_H, drawConsole);
  keep(consoleTex.texture);

  const consoleMat = keep(
    new THREE.MeshStandardMaterial({
      map: consoleTex.texture,
      roughness: 0.55,
      // Sin clearcoat: a 0° (vista frontal) el clearcoat producía un destello
      // blanco que tapaba la textura del canvas. MeshStandardMaterial muestra
      // la pantalla y las teclas con claridad desde la vista perpendicular.
      emissive: 0x88ccff,
      emissiveMap: consoleTex.texture,
      emissiveIntensity: 0,
    }),
  );

  // ── Perfil lateral del cuerpo y extrusión ───────────────────────────────────
  // Perfil en XY: X = fondo (0=atrás, D=adelante), Y = altura (0=suelo, H_BODY=tope)
  const profile = new THREE.Shape();
  profile.moveTo(BEVEL, 0);
  profile.lineTo(D - BEVEL, 0);
  profile.quadraticCurveTo(D, 0, D, BEVEL);
  // Cara delantera vertical (tramo inferior antes de la pendiente)
  profile.lineTo(SL_BOT.x, SL_BOT.y);
  // Pendiente de la consola (face angled toward user)
  profile.lineTo(SL_TOP.x, SL_TOP.y);
  // Hombro redondeado → tope
  profile.quadraticCurveTo(D - 46, H_BODY, D - 60, H_BODY);
  // Tope plano hacia atrás
  profile.lineTo(BEVEL, H_BODY);
  // Pared trasera
  profile.quadraticCurveTo(0, H_BODY, 0, H_BODY - BEVEL);
  profile.lineTo(0, BEVEL);
  profile.quadraticCurveTo(0, 0, BEVEL, 0);

  const housingGeo = keep(
    new THREE.ExtrudeGeometry(profile, {
      depth: W - BEVEL * 2,
      bevelEnabled: true,
      bevelSize: BEVEL,
      bevelThickness: BEVEL,
      bevelSegments: 5,
      curveSegments: 20,
    }),
  );
  housingGeo.translate(0, 0, -(W - BEVEL * 2) / 2);
  const housing = new THREE.Mesh(housingGeo, bodyMat);
  housing.castShadow  = true;
  housing.receiveShadow = true;
  root.add(housing);

  // ── Display en la pendiente ──────────────────────────────────────────────────
  // Posición: punto medio de la pendiente + 1.5 mm de offset (sin z-fighting)
  // El bisel de la extrusión lleva la cara BEVEL mm hacia fuera: el panel va por delante.
  const PANEL_OFFSET = BEVEL + 1.5;
  const mid3 = new THREE.Vector3(
    slopeMid2.x + slopeOutward.x * PANEL_OFFSET,
    slopeMid2.y + slopeOutward.y * PANEL_OFFSET,
    0,
  );
  const dispW = W - BEVEL * 2 - 24;  // panel claramente dentro del bevel
  const dispH = slopeLen - 22;
  const consoleGeo = keep(new THREE.PlaneGeometry(dispW, dispH));
  const consoleMesh = new THREE.Mesh(consoleGeo, consoleMat);
  consoleMesh.position.copy(mid3);
  consoleMesh.up.copy(slopeAlong);
  consoleMesh.lookAt(mid3.clone().add(slopeOutward));
  root.add(consoleMesh);

  // ── Rótulo «OHAUS» en el costado izquierdo del cuerpo ───────────────────────
  // Canvas pequeño con el wordmark en rojo
  const sideLogoCv = canvasTexture(256, 64, (g, w, h) => {
    g.fillStyle = '#c01010';
    g.font = `900 ${h * 0.78}px ${SANS}`;
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText('OHAUS', w / 2, h / 2);
  });
  keep(sideLogoCv.texture);
  const sideLogoMat = keep(
    new THREE.MeshStandardMaterial({
      map: sideLogoCv.texture,
      transparent: true,
      roughness: 0.4,
    }),
  );
  // Plano del rótulo: en el costado derecho del cuerpo (cara +Z, hacia afuera)
  // El normal del PlaneGeometry por defecto apunta a +Z → no necesita rotación extra,
  // solo se desplaza a z = +W/2 + 0.3 para sobresalir de la cara del cuerpo.
  const logoGeo = keep(new THREE.PlaneGeometry(72, 18));
  const logoMesh = new THREE.Mesh(logoGeo, sideLogoMat);
  // X ≈ D-110 (cerca del frente pero antes del ángulo de la consola), Y a 45 % de altura
  logoMesh.position.set(D - 110, H_BODY * 0.45, W / 2 + 0.3);
  // Sin rotación extra: la cara del plano (normal +Z) queda mirando hacia fuera del costado
  root.add(logoMesh);

  // ── Patas de goma (rojas, anchas y bajas con aro más claro) ─────────────────
  const FW = 13;   // radio de la pata
  const FH = 10;   // altura de la pata (disco bajo y ancho)
  const FINX = 26; // inset desde el borde del cuerpo (X)
  const FINZ = 24; // inset desde el borde del cuerpo (Z)

  // Patas parcialmente bajo el cuerpo: centro en Y=-FH/3 → tope visible a Y=+FH/6 (dentro del bevel).
  const FOOT_CY = -(FH / 3);
  for (const [sx, sz] of [[0, -1], [D, -1], [0, 1], [D, 1]] as const) {
    const legGeo = keep(new THREE.CylinderGeometry(FW, FW * 1.08, FH, 28));
    const leg = new THREE.Mesh(legGeo, redMat);
    leg.position.set(sx === 0 ? FINX : D - FINX, FOOT_CY, sz * (W / 2 - FINZ));
    leg.castShadow = true;
    root.add(leg);
    // Aro más claro en el borde inferior visible
    const ringGeo = keep(new THREE.TorusGeometry(FW * 0.88, 1.6, 8, 28));
    const ring = new THREE.Mesh(ringGeo, footRingMat);
    ring.rotation.x = Math.PI / 2;
    ring.position.set(sx === 0 ? FINX : D - FINX, FOOT_CY - FH * 0.26, sz * (W / 2 - FINZ));
    root.add(ring);
  }

  // ── Plato y pedestal ────────────────────────────────────────────────────────
  const PED_H = 26;  // est.
  const PED_R = 4;
  const PAN_TOP = H_BODY + PED_H + 5;  // 129 mm

  // Pedestal
  const pedGeo = keep(new THREE.CylinderGeometry(PED_R, PED_R * 1.08, PED_H, 20));
  const pedMesh = new THREE.Mesh(pedGeo, panMat);
  // El pedestal está centrado en la cámara del protector
  // (a mitad del ancho: Z=0, y en X a la mitad entre X_SR y D-X_SF)
  const panCtrX = (X_SR + (D - X_SF)) / 2;  // ≈ 121
  pedMesh.position.set(panCtrX, H_BODY + PED_H / 2, 0);
  pedMesh.renderOrder = 1;
  root.add(pedMesh);

  // Anillo del plato (wind ring)
  const windGeo = keep(new THREE.TorusGeometry(PAN_R + 2, 1.5, 8, 64));
  const windRing = new THREE.Mesh(windGeo, panMat);
  windRing.rotation.x = Math.PI / 2;
  windRing.position.set(panCtrX, H_BODY + PED_H + 2, 0);
  windRing.renderOrder = 1;
  root.add(windRing);

  // Plato (lathe, acero inox)
  const panGeo = keep(
    lathe([
      [0, 0],
      [PAN_R * 0.9, 0],
      [PAN_R, 1.5],
      [PAN_R, 4.5],
      [PAN_R - 1, 5.5],
      [3, 5.5],
      [0, 5.5],
    ]),
  );
  const panMesh = new THREE.Mesh(panGeo, panMat);
  panMesh.position.set(panCtrX, H_BODY + PED_H, 0);
  panMesh.renderOrder = 1;
  panMesh.castShadow = true;
  root.add(panMesh);

  // Torre trasera del protector (visible en la foto, atrás del plato)
  const towerGeo = keep(new THREE.CylinderGeometry(7, 7, H_SH * 0.45, 16));
  const tower = new THREE.Mesh(towerGeo, bodyMat);
  tower.position.set(X_SR + 20, H_BODY + H_SH * 0.225, 0);
  tower.castShadow = true;
  root.add(tower);

  // ── Marco del protector (blanco, postes cuadrados) ──────────────────────────
  const X_SF_POS = D - X_SF;  // X del poste delantero = 219
  const X_SR_POS = X_SR;      // X del poste trasero  = 24

  // 4 postes
  const postGeo = keep(new THREE.BoxGeometry(POST_W, H_SH, POST_W));
  for (const [px, pz] of [
    [X_SF_POS, -SH_Z], [X_SF_POS, SH_Z],
    [X_SR_POS, -SH_Z], [X_SR_POS, SH_Z],
  ] as const) {
    const post = new THREE.Mesh(postGeo, bodyMat);
    post.position.set(px, H_BODY + H_SH / 2, pz);
    post.castShadow = true;
    root.add(post);
  }

  // Travesaños horizontales (4 barras que forman la tapa)
  const capBarH = keep(new THREE.BoxGeometry(X_SF_POS - X_SR_POS - POST_W, POST_W, POST_W));
  const capBarW = keep(new THREE.BoxGeometry(POST_W, POST_W, SH_Z * 2 - POST_W));
  for (const pz of [-SH_Z, SH_Z]) {
    const b = new THREE.Mesh(capBarH, bodyMat);
    b.position.set((X_SF_POS + X_SR_POS) / 2, H_TOT, pz);
    root.add(b);
  }
  for (const px of [X_SF_POS, X_SR_POS]) {
    const b = new THREE.Mesh(capBarW, bodyMat);
    b.position.set(px, H_TOT, 0);
    root.add(b);
  }

  // Franja roja: línea MUY delgada (2.5 mm en Y, 0.5 mm en X) en el borde superior
  // de la CARA FRONTAL del travesaño delantero. No sobresale hacia arriba — solo
  // colorea el borde superior de la cara blanca.
  {
    // MeshBasicMaterial: sin iluminación → sin bloom. La franja aparece como
    // una línea roja plana de 3 mm en el borde frontal del marco de la tapa.
    const topRedMat = keep(new THREE.MeshBasicMaterial({ color: 0xc01010 }));
    const STRIPE_H = 3;  // 3 mm (spec: 2–3 mm)
    // Caja de 1 mm de profundidad × 3 mm de alto × ancho interior del marco.
    // Queda en la cara frontal del travesaño frontal superior.
    const topRedGeo = keep(new THREE.BoxGeometry(1.5, STRIPE_H, SH_Z * 2 + POST_W));
    const topRed = new THREE.Mesh(topRedGeo, topRedMat);
    // Centro: cara frontal del travesaño (X = X_SF_POS + POST_W/2 = 223.5),
    // en los 3 mm superiores de la cara (Y = H_TOT + POST_W/2 - STRIPE_H/2)
    topRed.position.set(
      X_SF_POS + POST_W / 2 + 0.75,
      H_TOT + POST_W / 2 - STRIPE_H / 2,
      0,
    );
    root.add(topRed);
  }

  // Rieles inferiores (base del protector)
  const baseBarH = keep(new THREE.BoxGeometry(X_SF_POS - X_SR_POS - POST_W, 5, 5));
  const baseBarW = keep(new THREE.BoxGeometry(5, 5, SH_Z * 2 - POST_W));
  for (const pz of [-SH_Z, SH_Z]) {
    const b = new THREE.Mesh(baseBarH, bodyMat);
    b.position.set((X_SF_POS + X_SR_POS) / 2, H_BODY + 2.5, pz);
    root.add(b);
  }
  for (const px of [X_SF_POS, X_SR_POS]) {
    const b = new THREE.Mesh(baseBarW, bodyMat);
    b.position.set(px, H_BODY + 2.5, 0);
    root.add(b);
  }

  // ── Paneles de vidrio ────────────────────────────────────────────────────────
  // Todos con renderOrder=2, depthWrite=false (labGlass ya lo pone).
  // NO se usan boxGeometry de borde: muestran su cara grande desde el ángulo 3/4
  // y crean la ilusión de paneles flotantes fuera del marco. Solo el panel trasero
  // lleva una caja de borde (queda dentro del protector, da profundidad visual).
  const glassMidY = H_BODY + H_SH / 2;
  const glassH    = H_SH - POST_W;
  const glassXW   = X_SF_POS - X_SR_POS - POST_W;  // ancho de vidrio frente/atrás
  const glassZW   = SH_Z * 2 - POST_W;               // ancho de vidrio laterales

  const addGlass = (geo: THREE.BufferGeometry, x: number, y: number, z: number, ry: number) => {
    const g = keep(geo);
    const m = new THREE.Mesh(g, glassMat);
    m.position.set(x, y, z);
    m.rotation.y = ry;
    m.renderOrder = 2;
    root.add(m);
    return m;
  };

  // Paneles laterales: izquierdo (Z=-SH_Z) y derecho (Z=+SH_Z).
  // ry=0 → normal apunta a ±Z. El pane queda DENTRO del marco de postes.
  const glassCtrX = (X_SF_POS + X_SR_POS) / 2;
  for (const sz of [-SH_Z, SH_Z]) {
    addGlass(new THREE.PlaneGeometry(glassXW, glassH), glassCtrX, glassMidY, sz, 0);
  }

  // Panel trasero: X = X_SR_POS, perpendicular al eje X (ry=PI/2 → normal hacia +X).
  // Extiende ±glassZW/2 en Z, dentro de los postes laterales.
  // Conserva un box de borde tenue: solo la pared del fondo (oculta desde el frente).
  addGlass(new THREE.PlaneGeometry(glassZW, glassH), X_SR_POS, glassMidY, 0, Math.PI / 2);

  // Panel superior (horizontal) — usa topGlassMat (sin clearcoat) para evitar que
  // la reflexión del entorno aparezca como una losa roja sólida.
  {
    const geo = keep(new THREE.PlaneGeometry(glassXW, glassZW));
    geo.rotateX(-Math.PI / 2);
    const m = new THREE.Mesh(geo, topGlassMat);
    m.position.set((X_SF_POS + X_SR_POS) / 2, H_TOT + POST_W * 0.5 - 0.5, 0);
    m.renderOrder = 2;
    root.add(m);
  }

  // Panel delantero izquierdo (fijo) — sin caja de borde
  const HALF_DOOR_Z = glassZW / 2;  // cada hoja ocupa la mitad del ancho
  addGlass(
    new THREE.PlaneGeometry(HALF_DOOR_Z, glassH),
    X_SF_POS, glassMidY, -HALF_DOOR_Z / 2,
    -Math.PI / 2,
  );

  // Hoja deslizante delantera (derecha): empieza en Z = +HALF_DOOR_Z/2
  const slidingGeo = keep(new THREE.PlaneGeometry(HALF_DOOR_Z, glassH));
  const slidingDoor = new THREE.Mesh(slidingGeo, glassMat);
  slidingDoor.position.set(X_SF_POS, glassMidY, HALF_DOOR_Z / 2);
  slidingDoor.rotation.y = -Math.PI / 2;
  slidingDoor.renderOrder = 2;
  root.add(slidingDoor);

  // Variable para animar la puerta (sin caja de borde separada)
  const slidingEdge = slidingDoor;  // alias para el bloque de animación

  // Tirador de la puerta deslizante (en el borde izquierdo de la hoja, Z≈0)
  const handleGeo = keep(new THREE.CylinderGeometry(2, 2, 44, 8));
  const handle = new THREE.Mesh(handleGeo, bodyMat);
  handle.position.set(X_SF_POS + 3.5, glassMidY, 0);
  root.add(handle);

  // ── Barca de pesaje (aparece animada) ─────────────────────────────────────
  const boatGroup = new THREE.Group();
  boatGroup.position.set(panCtrX, PAN_TOP, 0);
  boatGroup.visible = false;
  root.add(boatGroup);

  const boatGeo = keep(new THREE.BoxGeometry(34, 6, 42));
  const boatMesh = new THREE.Mesh(boatGeo, boatMat);
  boatMesh.position.y = 3;
  boatMesh.renderOrder = 1;
  boatGroup.add(boatMesh);

  const foldGeo = keep(new THREE.BoxGeometry(36, 1, 44));
  const foldMesh = new THREE.Mesh(foldGeo, boatMat);
  foldMesh.position.y = 7;
  foldMesh.renderOrder = 1;
  boatGroup.add(foldMesh);

  const smplGeo = keep(new THREE.SphereGeometry(8, 14, 10));
  smplGeo.scale(1, 0.34, 1);
  const smplMesh = new THREE.Mesh(smplGeo, sampleMat);
  smplMesh.position.set(1, 10, -2);
  smplMesh.renderOrder = 1;
  boatGroup.add(smplMesh);

  // ── Anclas para la escena ───────────────────────────────────────────────────
  const displayAnchor = new THREE.Object3D();
  displayAnchor.position.copy(mid3).add(slopeOutward.clone().multiplyScalar(10));
  root.add(displayAnchor);

  const panAnchor = new THREE.Object3D();
  panAnchor.position.set(panCtrX, PAN_TOP + 5, 0);
  root.add(panAnchor);

  const shieldAnchor = new THREE.Object3D();
  shieldAnchor.position.set(X_SF_POS + 8, H_BODY + H_SH * 0.55, SH_Z + 5);
  root.add(shieldAnchor);

  const footAnchor = new THREE.Object3D();
  footAnchor.position.set(D - FINX, -(FH + BEVEL), W / 2 - FINZ);
  root.add(footAnchor);

  // ── Estado de animación ─────────────────────────────────────────────────────
  let powered    = false;
  let doorSlideZ = 0;   // desplazamiento actual de la hoja deslizante en Z
  let boatAlpha  = 0;
  let reading    = 0;
  let lastRedraw = 0;
  const DOOR_MAX = HALF_DOOR_Z * 0.96;

  return {
    root,
    anchors: {
      display: displayAnchor,
      pan:     panAnchor,
      shield:  shieldAnchor,
      foot:    footAnchor,
    },
    views: {
      pantalla: {
        target:    displayAnchor,
        // Dirección en espacio del modelo: slopeOutward (normal de la pendiente) + sesgo +Y
        // muy pequeño. Con sesgo 0.28 la cámara queda en Y=386 mm (sobre la tapa del
        // protector) y apunta hacia abajo en lugar de hacia la pantalla.
        // Con sesgo 0.05 y distancia 270 la cámara queda a ~195 mm de altura,
        // mirando casi perpendicularmente a la cara de la consola.
        direction: new THREE.Vector3(slopeOutward.x, slopeOutward.y + 0.05, 0),
        distance:  270,
      },
      plato: {
        target:    panAnchor,
        direction: new THREE.Vector3(0.15, 0.9, 0.8).normalize(),
        distance:  270,
      },
      protector: {
        target:    shieldAnchor,
        direction: new THREE.Vector3(0.7, 0.18, 0.8).normalize(),
        distance:  500,
      },
    },
    frameTop: H_TOT + 12,
    yaw: -Math.PI / 2 + 0.55, // la consola mira a +X: se gira para que quede de frente, en tres cuartos

    setPower(on: boolean) {
      powered = on;
      if (on) screenOn = true;
    },

    update(dt: number, t: number, level: number) {
      // Puerta: se abre a mitad de la animación y se cierra antes del fin
      const wantDoor = level > 0.16 && level < 0.84 ? DOOR_MAX : 0;
      doorSlideZ += (wantDoor - doorSlideZ) * Math.min(1, dt * 3.0);
      slidingDoor.position.z  = HALF_DOOR_Z / 2 + doorSlideZ;
      slidingEdge.position.z  = HALF_DOOR_Z / 2 + doorSlideZ;
      handle.position.z       = doorSlideZ;

      // Barca
      const wantAlpha = level > 0.36 ? 0.96 : 0;
      boatAlpha += (wantAlpha - boatAlpha) * Math.min(1, dt * 2.0);
      boatGroup.visible  = boatAlpha > 0.02;
      boatMat.opacity    = boatAlpha;
      sampleMat.opacity  = boatAlpha;

      // Ligera bajada del plato al pesar
      panMesh.position.y = H_BODY + PED_H - level * 1.0 * Math.max(0, boatAlpha - 0.3);

      // Pantalla
      if (screenOn && !powered && level < 0.02) screenOn = false;
      const wantR = powered && level > 0.52 ? SAMPLE_G : 0;
      reading += (wantR - reading) * Math.min(1, dt * (powered ? 1.0 : 7));
      dispReading = reading;
      stableFlag  = powered && Math.abs(reading - SAMPLE_G) < 0.08 && level > 0.7;

      if (t - lastRedraw > 1 / 15) {
        lastRedraw = t;
        consoleTex.redraw();
      }
      consoleMat.emissiveIntensity = screenOn ? 0.28 * level : 0;
    },

    dispose,
  };
}
