/**
 * Ortoalresa Digicen 22 R — centrífuga universal refrigerada de sobremesa.
 *
 * Fuentes de medidas:
 *   ortoalresa.com/centrifugas/digicen-22-r/  (consultado 2026-11-02)
 *     → Dimensiones (a × l × h): 590 × 620 × 320 mm
 *     → Capacidad máx.: 4 × 100 ml; velocidad máx.: 16 500 RPM / 26 480 ×g
 *     → Pantalla TFT táctil a color; rotor REI (sin herramientas)
 *
 * Estimaciones a partir de la fotografía oficial:
 *   · Altura del cuerpo (patas + bloque, sin tapa):  ~240 mm
 *   · Tapa cerrada añade: ~80 mm  → total 320 ✓
 *   · Patas de goma: ~18 mm de alto
 *   · Cámara interior: radio ~188 mm, profundidad ~74 mm
 *   · Fascia delantera: izquierda ~40 % (louvres), derecha ~60 % (panel de control)
 *   · Ángulo de apertura de la tapa: ~72 ° (1.26 rad)
 *   · Rotor: disco grande R=170 mm, 4 cubetas basculantes con cúpula transparente
 *   · Pivote de cubeta a r=128 mm del centro, cerca del borde del disco
 */
import * as THREE from 'three';
import type { BuiltModel } from '../types';
import { SANS, canvasTexture, roundedBox, lathe, disposer } from './kit';

// ── dimensiones (mm) ─────────────────────────────────────────────────────────
const W      = 590;
const D      = 620;
const H_FOOT = 18;
const H_BODY = 222;  // bloque sin tapa (H_FOOT + H_BODY + H_LID = 320 ✓)
const H_LID  = 80;
const BV     = 8;

const BODY_Y = H_FOOT;
const BODY_T = H_FOOT + H_BODY; // = 240 — nivel de la apertura de la cámara

// cámara centrifugadora
const CHAM_R = 188;
const CHAM_D = 74;

// rotor: pivote EN el borde del disco, para que las cúpulas sobresalgan hacia fuera
// y sean visibles desde arriba con la tapa abierta.
const ROTOR_DISC_R = 152;  // radio del disco (= PIVOT_R → cúpulas más allá del borde)
const ROTOR_DISC_H = 12;   // espesor del disco (delgado)
const PIVOT_R      = 152;  // pivote en el borde del disco
const BUCKET_R     = 28;   // radio de la cubeta
const BUCKET_H     = 40;   // longitud de la cubeta (corta: cúpula queda a ~200 mm)
const DOME_R       = 30;   // radio de la cúpula hemisférica

// tapa
const LID_OPEN   = -1.26;
const LID_CLOSED =  0.0;

// colores
const C_BODY   = 0x3e4146;  // carbón oscuro
const C_FASCIA = 0xd0d0cc;  // gris cálido claro
const C_LID    = 0xcbcbc8;  // gris claro (tapa)

export function build(): BuiltModel {
  const { keep, dispose } = disposer();
  const root = new THREE.Group();

  // ── materiales ───────────────────────────────────────────────────────────────
  const bodyMat = keep(new THREE.MeshPhysicalMaterial({
    color: C_BODY, roughness: 0.50, metalness: 0.06, clearcoat: 0.15, clearcoatRoughness: 0.6,
  }));
  const fasciaMat = keep(new THREE.MeshPhysicalMaterial({
    color: C_FASCIA, roughness: 0.38, metalness: 0.06, clearcoat: 0.35, clearcoatRoughness: 0.4,
  }));
  const lidMat = keep(new THREE.MeshPhysicalMaterial({
    color: C_LID, roughness: 0.35, metalness: 0.06, clearcoat: 0.45, clearcoatRoughness: 0.35,
  }));
  const chamberMat = keep(new THREE.MeshPhysicalMaterial({
    color: 0xcdd2d6, metalness: 0.8, roughness: 0.16, side: THREE.DoubleSide,
  }));
  const rubberMat = keep(new THREE.MeshStandardMaterial({ color: 0x101316, roughness: 0.94 }));
  const darkMat   = keep(new THREE.MeshPhysicalMaterial({ color: 0x26272c, roughness: 0.38, metalness: 0.45 }));
  const redMat    = keep(new THREE.MeshPhysicalMaterial({ color: 0xc01515, roughness: 0.30, metalness: 0.18 }));
  const chromeMat = keep(new THREE.MeshPhysicalMaterial({ color: 0xd0d5da, metalness: 1, roughness: 0.18 }));
  const domeMat   = keep(new THREE.MeshPhysicalMaterial({
    color: 0xd4e8f6, roughness: 0.04, clearcoat: 1, clearcoatRoughness: 0.02,
    transparent: true, opacity: 0.64, side: THREE.DoubleSide, depthWrite: false,
  }));
  const whiteMat = keep(new THREE.MeshPhysicalMaterial({ color: 0xeeeeeb, roughness: 0.45, metalness: 0.04 }));
  const blackHingeMat = keep(new THREE.MeshStandardMaterial({ color: 0x18191c, roughness: 0.55 }));
  const panelInsetMat = keep(new THREE.MeshPhysicalMaterial({
    color: 0x2a2c30, roughness: 0.42, metalness: 0.08, clearcoat: 0.2,
  }));

  // ── bloque principal ─────────────────────────────────────────────────────────
  const body = new THREE.Group();
  root.add(body);

  // cuerpo inferior: hasta el fondo de la cámara (y = 166)
  const H_BODY_LOW = H_BODY - CHAM_D; // 148 mm
  const bodyGeo = keep(roundedBox(W, H_BODY_LOW, D, BV));
  const bodyMesh = new THREE.Mesh(bodyGeo, bodyMat);
  bodyMesh.position.y = BODY_Y + H_BODY_LOW / 2;
  bodyMesh.castShadow = true;
  bodyMesh.receiveShadow = true;
  body.add(bodyMesh);

  // collar lateral y trasero (y=166..240): continúa el cuerpo a ambos lados y atrás
  const COLLAR_T = BV * 2;
  const COLLAR_Y = BODY_T - CHAM_D / 2;
  const collarDefs: [number, number, number, number, number, number][] = [
    [-W / 2 + COLLAR_T / 2, COLLAR_Y, 0,               COLLAR_T, CHAM_D, D       ],
    [ W / 2 - COLLAR_T / 2, COLLAR_Y, 0,               COLLAR_T, CHAM_D, D       ],
    [0, COLLAR_Y, -D / 2 + COLLAR_T / 2, W,            CHAM_D, COLLAR_T          ],
  ];
  for (const [cx, cy, cz, cw, ch, cd] of collarDefs) {
    const col = new THREE.Mesh(keep(new THREE.BoxGeometry(cw, ch, cd)), bodyMat);
    col.position.set(cx, cy, cz);
    body.add(col);
  }

  // ── patas de goma ─────────────────────────────────────────────────────────────
  const footGeo = keep(new THREE.CylinderGeometry(15, 18, H_FOOT, 24));
  for (const [fx, fz] of [[-250, -262], [-250, 262], [250, -262], [250, 262]] as const) {
    const f = new THREE.Mesh(footGeo, rubberMat);
    f.position.set(fx, H_FOOT / 2, fz);
    body.add(f);
  }

  // ── cámara centrifugadora ─────────────────────────────────────────────────────
  // techo del cuerpo: anillo oscuro que rodea la apertura
  const topRingGeo = keep(new THREE.RingGeometry(CHAM_R, W / 2 - 6, 88));
  topRingGeo.rotateX(-Math.PI / 2);
  const topRing = new THREE.Mesh(topRingGeo, bodyMat);
  topRing.position.y = BODY_T + 0.5;
  body.add(topRing);

  // paredes de la cámara (acero inox)
  const chamWallGeo = keep(new THREE.CylinderGeometry(CHAM_R, CHAM_R, CHAM_D, 88, 1, true));
  const chamWall = new THREE.Mesh(chamWallGeo, chamberMat);
  chamWall.position.y = BODY_T - CHAM_D / 2;
  body.add(chamWall);

  // fondo de la cámara
  const chamFloorGeo = keep(new THREE.CircleGeometry(CHAM_R, 88));
  chamFloorGeo.rotateX(-Math.PI / 2);
  const chamFloor = new THREE.Mesh(chamFloorGeo, chamberMat);
  chamFloor.position.y = BODY_T - CHAM_D;
  body.add(chamFloor);

  // junta de goma (gasket) alrededor de la apertura
  const gasketGeo = keep(new THREE.TorusGeometry(CHAM_R + 4, 6, 12, 88));
  gasketGeo.rotateX(Math.PI / 2);
  const gasket = new THREE.Mesh(gasketGeo, rubberMat);
  gasket.position.y = BODY_T + 2;
  body.add(gasket);

  // ── fascia delantera (moulding gris cálido) ───────────────────────────────────
  const FASCIA_H   = 206;
  const FASCIA_Z   = D / 2 + 5;
  const FASCIA_Y_BOT = BODY_Y;

  // SPLIT: izquierda ~40 % (louvres), derecha ~60 % (panel control)
  const SPLIT_X    = -58;
  const leftFasciaW  = SPLIT_X - (-W / 2) + 6;   // ≈ 237 mm
  const rightFasciaW = W / 2 - SPLIT_X + 6;       // ≈ 359 mm

  const leftFascia = new THREE.Mesh(keep(roundedBox(leftFasciaW, FASCIA_H, 16, 6)), fasciaMat);
  leftFascia.position.set(-W / 2 + leftFasciaW / 2 - 3, FASCIA_Y_BOT + FASCIA_H / 2, FASCIA_Z - 2);
  leftFascia.castShadow = true;
  body.add(leftFascia);

  const rightFascia = new THREE.Mesh(keep(roundedBox(rightFasciaW, FASCIA_H, 18, 7)), fasciaMat);
  rightFascia.position.set(SPLIT_X + rightFasciaW / 2 - 3, FASCIA_Y_BOT + FASCIA_H / 2, FASCIA_Z - 2);
  rightFascia.castShadow = true;
  body.add(rightFascia);

  // labio lateral derecho (wrapped lip)
  const lipH = FASCIA_H + 28;
  const lipD = 66;
  const lip = new THREE.Mesh(keep(roundedBox(22, lipH, lipD, 6)), fasciaMat);
  lip.position.set(W / 2 + 3, FASCIA_Y_BOT + lipH / 2, FASCIA_Z - lipD / 2 - 4);
  body.add(lip);

  // ── louvres del panel izquierdo (8 ranuras horizontales) ────────────────────
  const LOUVRE_W = leftFasciaW - 28;
  const LOUVRE_H = FASCIA_H - 44;
  const louvresTex = canvasTexture(512, 512, (g, w, h) => {
    g.fillStyle = '#cececa';
    g.fillRect(0, 0, w, h);
    const slots = 8;
    const slotH = Math.floor(h / (slots + 1));
    for (let i = 0; i < slots; i++) {
      const y = slotH * (i + 1) - slotH * 0.3;
      const sw = w * 0.84;
      const sx = (w - sw) / 2;
      // sombra superior sutil
      g.fillStyle = 'rgba(0,0,0,0.08)';
      g.beginPath();
      g.roundRect(sx, y - 6, sw, 4, 2);
      g.fill();
      // ranura oscura
      g.fillStyle = '#1a1b1f';
      g.beginPath();
      g.roundRect(sx, y - 4, sw, 8, 4);
      g.fill();
      // reflejo inferior sutil
      g.fillStyle = 'rgba(255,255,255,0.06)';
      g.beginPath();
      g.roundRect(sx, y + 4, sw, 3, 2);
      g.fill();
    }
  });
  keep(louvresTex.texture);
  const louvreMesh = new THREE.Mesh(
    keep(new THREE.PlaneGeometry(LOUVRE_W, LOUVRE_H)),
    keep(new THREE.MeshStandardMaterial({ map: louvresTex.texture, roughness: 0.45, transparent: true })),
  );
  louvreMesh.position.set(-W / 2 + leftFasciaW / 2 - 3, FASCIA_Y_BOT + FASCIA_H / 2, FASCIA_Z + 10);
  body.add(louvreMesh);

  // ── rejilla lateral izquierda (ranuras verticales pequeñas) ──────────────────
  const sideVentTex = canvasTexture(256, 400, (g, w, h) => {
    g.fillStyle = '#444850';
    g.fillRect(0, 0, w, h);
    const cols = 6;
    const cw = Math.floor(w / (cols + 1));
    for (let c = 0; c < cols; c++) {
      const x = cw * (c + 1) - cw * 0.4;
      g.fillStyle = '#1a1b1f';
      g.beginPath();
      g.roundRect(x - 3, 18, 6, h - 36, 3);
      g.fill();
    }
  });
  keep(sideVentTex.texture);
  const sideVent = new THREE.Mesh(
    keep(new THREE.PlaneGeometry(124, 170)),
    keep(new THREE.MeshStandardMaterial({ map: sideVentTex.texture, roughness: 0.6 })),
  );
  sideVent.rotation.y = Math.PI / 2;
  sideVent.position.set(-W / 2 - 0.5, BODY_Y + 88, D / 2 - 86);
  body.add(sideVent);

  // ── panel de control inset ────────────────────────────────────────────────────
  const PANEL_X  = 96;
  const PANEL_W  = 228;
  const PANEL_H  = 168;
  const SCREEN_W = 172;
  const SCREEN_H = 114;

  const panelInset = new THREE.Mesh(keep(roundedBox(PANEL_W, PANEL_H, 8, 5)), panelInsetMat);
  panelInset.position.set(PANEL_X, FASCIA_Y_BOT + PANEL_H / 2 + 16, FASCIA_Z + 6);
  body.add(panelInset);

  // ── pantalla TFT ─────────────────────────────────────────────────────────────
  let screenOn  = false;
  let rpmAnim   = 0;
  let tempAnim  = 22.0;
  let sessTime  = 0;
  let lastDraw  = 0;

  const DISP_RPM  = 6800;   // ilustrativo — muy por debajo del máx. 16 500
  const DISP_TEMP = 8.0;    // °C dentro del rango −20…40
  const DISP_MINS = 8;

  const screen = canvasTexture(1024, 680, (g, w, h) => {
    const PX = w / 512;
    if (!screenOn) {
      g.fillStyle = '#080a0c';
      g.fillRect(0, 0, w, h);
      return;
    }
    // fondo general
    g.fillStyle = '#f0f0ee';
    g.fillRect(0, 0, w, h);

    // barra superior con iconos (fondo gris claro)
    g.fillStyle = '#e0e0de';
    g.fillRect(0, 0, w, 40 * PX);
    // iconos simbólicos en la barra superior
    g.fillStyle = '#3a5a9c';
    g.font = `600 ${Math.round(16 * PX)}px ${SANS}`;
    g.textBaseline = 'middle';
    g.textAlign = 'left';
    g.fillText('⚙  ≡  ✓', 12 * PX, 20 * PX);
    g.textAlign = 'right';
    g.fillStyle = '#444';
    g.font = `500 ${Math.round(14 * PX)}px ${SANS}`;
    g.fillText(screenOn && rpmAnim > 50 ? 'EN MARCHA' : 'LISTO', w - 12 * PX, 20 * PX);
    g.textAlign = 'left';

    // separador
    g.fillStyle = '#c8c8c6';
    g.fillRect(0, 40 * PX, w, 1.5 * PX);

    // tabla de parámetros (6 filas en 2 columnas)
    const rows: [string, string][] = [
      ['Prog',   '000'],
      ['Rotor',  'AF 314'],
      ['RPM',    `${Math.round(rpmAnim)}`],
      ['RCF',    `${Math.round(1.118e-6 * (PIVOT_R + BUCKET_H / 2) * rpmAnim ** 2)} ×g`],
      ['Temp',   `${tempAnim.toFixed(1)} °C`],
      ['Tiempo', (() => {
        const rem = Math.max(0, DISP_MINS * 60 - sessTime);
        const m = Math.floor(rem / 60);
        const s = Math.floor(rem % 60);
        return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
      })()],
    ];
    const colX = [0, w / 2];
    const ROW_H = 88 * PX;
    const ROW_PAD = 44 * PX;
    for (let r = 0; r < 3; r++) {
      for (let c = 0; c < 2; c++) {
        const idx = r * 2 + c;
        const x = colX[c] + 10 * PX;
        const y = 42 * PX + r * ROW_H;
        g.fillStyle = '#5580cc';
        g.font = `600 ${Math.round(15 * PX)}px ${SANS}`;
        g.textBaseline = 'top';
        g.fillText(rows[idx][0], x, y + 8 * PX);
        // caja con borde naranja/rojo
        g.fillStyle = '#fff';
        g.strokeStyle = '#cc3300';
        g.lineWidth = 2 * PX;
        g.beginPath();
        g.roundRect(x, y + ROW_PAD - 6 * PX, w / 2 - 20 * PX, 38 * PX, 4 * PX);
        g.fill(); g.stroke();
        g.fillStyle = '#111';
        g.font = `700 ${Math.round(22 * PX)}px ${SANS}`;
        g.textBaseline = 'middle';
        g.fillText(rows[idx][1], x + 7 * PX, y + ROW_PAD + 13 * PX);
        g.textBaseline = 'alphabetic';
      }
    }
    // barra de progreso naranja en la parte inferior
    const barY = h - 28 * PX;
    g.fillStyle = '#ddd';
    g.fillRect(10 * PX, barY, w - 20 * PX, 14 * PX);
    g.fillStyle = '#e05010';
    g.fillRect(10 * PX, barY, (w - 20 * PX) * Math.min(1, rpmAnim / DISP_RPM), 14 * PX);
  });
  keep(screen.texture);

  const screenMat = keep(new THREE.MeshStandardMaterial({
    map: screen.texture,
    emissive: new THREE.Color(0xffffff),
    emissiveMap: screen.texture,
    emissiveIntensity: 0,
    roughness: 0.70, metalness: 0,
  }));
  const SCREEN_Z = FASCIA_Z + 22;
  const SCREEN_Y  = FASCIA_Y_BOT + PANEL_H / 2 + 16 + 22;
  const screenMesh = new THREE.Mesh(keep(new THREE.PlaneGeometry(SCREEN_W, SCREEN_H)), screenMat);
  screenMesh.position.set(PANEL_X, SCREEN_Y, SCREEN_Z);
  body.add(screenMesh);

  // bisel de la pantalla
  const screenBez = new THREE.Mesh(keep(new THREE.BoxGeometry(SCREEN_W + 14, SCREEN_H + 12, 6)), darkMat);
  screenBez.position.set(PANEL_X, SCREEN_Y, SCREEN_Z - 4);
  body.add(screenBez);

  // ── 4 botones redondos bajo la pantalla ──────────────────────────────────────
  // botones gris oscuro con icono canvas (igual que la foto: swirl, stop, lid, home)
  const BTN_Y  = FASCIA_Y_BOT + 30;
  const BTN_Z  = FASCIA_Z + 20;
  const BTN_ICONS = ['↺', '■', '□', '⌂'] as const;
  const btnGeo = keep(new THREE.CylinderGeometry(11, 11, 7, 32));
  const btnCapGeo = keep(new THREE.CircleGeometry(10.5, 32));

  for (let i = 0; i < 4; i++) {
    const btnMat = keep(new THREE.MeshPhysicalMaterial({
      color: 0x3a3c42, roughness: 0.35, metalness: 0.1, clearcoat: 0.4,
    }));
    const btn = new THREE.Mesh(btnGeo, btnMat);
    btn.rotation.x = Math.PI / 2;
    btn.position.set(PANEL_X - 48 + i * 32, BTN_Y, BTN_Z);
    body.add(btn);

    // icono pintado en canvas sobre el botón
    const iconTex = canvasTexture(128, 128, (g, w) => {
      g.fillStyle = '#3a3c42';
      g.fillRect(0, 0, w, w);
      g.fillStyle = '#e8e8e6';
      g.font = `600 48px ${SANS}`;
      g.textAlign = 'center';
      g.textBaseline = 'middle';
      g.fillText(BTN_ICONS[i], w / 2, w / 2);
    });
    keep(iconTex.texture);
    const cap = new THREE.Mesh(btnCapGeo.clone(), keep(new THREE.MeshStandardMaterial({
      map: iconTex.texture, roughness: 0.5, transparent: false,
    })));
    cap.position.set(PANEL_X - 48 + i * 32, BTN_Y, BTN_Z + 4);
    body.add(cap);
  }

  // etiqueta del panel (modelo + logo)
  const panelLabelTex = canvasTexture(512, 48, (g, w, h) => {
    g.clearRect(0, 0, w, h);
    // Claro sobre el panel oscuro: en oscuro no se leía.
    g.fillStyle = '#c9ccd1';
    g.font = `500 20px ${SANS}`;
    g.textBaseline = 'middle';
    g.fillText('digicen 22 R', 10, h / 2);
    g.textAlign = 'right';
    g.fillStyle = '#e04a2a';
    g.font = `700 15px ${SANS}`;
    g.fillText('orto', w - 8, h * 0.28);
    g.fillStyle = '#f4f4f2';
    g.font = `700 26px ${SANS}`;
    g.fillText('alresa', w - 8, h * 0.68);
    g.textAlign = 'left';
  });
  keep(panelLabelTex.texture);
  const panelLabel = new THREE.Mesh(
    keep(new THREE.PlaneGeometry(PANEL_W - 10, 17)),
    keep(new THREE.MeshStandardMaterial({ map: panelLabelTex.texture, roughness: 0.5, transparent: true })),
  );
  panelLabel.position.set(PANEL_X, BTN_Y - 8, BTN_Z + 2);
  body.add(panelLabel);

  // ── ROTOR ────────────────────────────────────────────────────────────────────
  // El rotor se sitúa al nivel del borde superior de la cámara (y = BODY_T)
  // para que los pivotes y las cúpulas queden justo bajo el gasket.
  const ROTOR_ORIG_Y = BODY_T; // = 240

  const rotor = new THREE.Group();
  rotor.position.set(0, ROTOR_ORIG_Y, 0);
  body.add(rotor);

  // disco base del rotor — grande, casi llena la cámara
  const rotorDiscGeo = keep(new THREE.CylinderGeometry(ROTOR_DISC_R, ROTOR_DISC_R - 14, ROTOR_DISC_H, 72));
  const rotorDisc = new THREE.Mesh(rotorDiscGeo, darkMat);
  rotorDisc.position.y = -ROTOR_DISC_H / 2; // parte superior del disco a y=0 del rotor
  rotor.add(rotorDisc);

  // anillo de borde del disco (más claro, cromado)
  const rimGeo = keep(new THREE.TorusGeometry(ROTOR_DISC_R - 6, 4, 10, 72));
  rimGeo.rotateX(Math.PI / 2);
  const rim = new THREE.Mesh(rimGeo, chromeMat);
  rim.position.y = -2;
  rotor.add(rim);

  // hub central rojo
  const hubGeo = keep(lathe([
    [0, 0], [12, 0], [15, 3], [16, 8], [16, 22], [13, 26], [8, 29], [3, 30], [0, 30],
  ]));
  const hub = new THREE.Mesh(hubGeo, redMat);
  hub.position.y = 0;
  rotor.add(hub);

  // tuerca central cromada
  const nutGeo = keep(new THREE.CylinderGeometry(8, 8, 6, 6));
  const nut = new THREE.Mesh(nutGeo, chromeMat);
  nut.position.y = 28;
  rotor.add(nut);

  // ── 4 cubetas basculantes ─────────────────────────────────────────────────────
  // Posición de reposo: −0.5 rad → cúpula a ~200 mm de alto, fuera del borde del disco.
  // En giro → −π/2 (horizontal, cubierta por la tapa).
  const REST_ANGLE = -0.5;

  const bBodyGeo = keep(new THREE.CylinderGeometry(BUCKET_R, BUCKET_R - 4, BUCKET_H, 28));
  const bCapGeo  = keep(new THREE.CylinderGeometry(BUCKET_R - 2, BUCKET_R - 3, 12, 28));
  const bDomeGeo = keep(new THREE.SphereGeometry(DOME_R, 28, 14, 0, Math.PI * 2, 0, Math.PI / 2));
  const axleGeo  = keep(new THREE.CylinderGeometry(4, 4, PIVOT_R - 14, 14));
  const pinGeo   = keep(new THREE.CylinderGeometry(7, 7, 16, 16));

  const bucketGroups: THREE.Group[] = [];

  for (let i = 0; i < 4; i++) {
    const baseAngle = (i / 4) * Math.PI * 2;

    const armGroup = new THREE.Group();
    armGroup.rotation.y = baseAngle;
    rotor.add(armGroup);

    // eje radial desde el centro hasta el pivote (en el borde del disco)
    const axle = new THREE.Mesh(axleGeo, darkMat);
    axle.rotation.x = Math.PI / 2;
    axle.position.set(0, -4, PIVOT_R / 2);
    armGroup.add(axle);

    // pin de la bisagra
    const pin = new THREE.Mesh(pinGeo, chromeMat);
    pin.position.set(0, -4, PIVOT_R);
    armGroup.add(pin);

    // grupo de la cubeta (pivota en X)
    const bg = new THREE.Group();
    bg.position.set(0, -4, PIVOT_R);
    bg.rotation.x = REST_ANGLE;
    armGroup.add(bg);
    bucketGroups.push(bg);

    // cuerpo de la cubeta (oscuro)
    const bBody = new THREE.Mesh(bBodyGeo, darkMat);
    bBody.position.y = -BUCKET_H / 2;
    bg.add(bBody);

    // tapa interior blanca
    const bCap = new THREE.Mesh(bCapGeo, whiteMat);
    bCap.position.y = -BUCKET_H + 5;
    bg.add(bCap);

    // cúpula hemisférica transparente (borde inferior = extremo exterior en giro)
    // rotation.x = π hace que el polo de la esfera apunte en −Y (hacia el extremo libre)
    const bDome = new THREE.Mesh(bDomeGeo, domeMat);
    bDome.rotation.x = Math.PI;
    bDome.position.y = -BUCKET_H;
    bDome.renderOrder = 3;
    bg.add(bDome);

    // aro de la cúpula (borde cromado)
    const bRimGeo = keep(new THREE.TorusGeometry(DOME_R - 1, 2.5, 10, 28));
    const bRim = new THREE.Mesh(bRimGeo, chromeMat);
    bRim.position.y = -BUCKET_H;
    bg.add(bRim);
  }

  // ── tapa motorizada ─────────────────────────────────────────────────────────
  const lidGroup = new THREE.Group();
  lidGroup.position.set(0, BODY_T, -D / 2 + BV);
  root.add(lidGroup);
  lidGroup.rotation.x = LID_OPEN;

  const LID_DEPTH = D - BV * 2;
  const lidBodyGeo = keep(roundedBox(W - 6, H_LID, LID_DEPTH, BV));
  const lidBody = new THREE.Mesh(lidBodyGeo, lidMat);
  lidBody.position.set(0, H_LID / 2, LID_DEPTH / 2);
  lidBody.castShadow = true;
  lidGroup.add(lidBody);

  // cara interior de la tapa (gris más oscuro)
  const lidInner = new THREE.Mesh(
    keep(new THREE.PlaneGeometry(W - 22, LID_DEPTH - 14)),
    keep(new THREE.MeshPhysicalMaterial({ color: 0x888a8e, roughness: 0.55, metalness: 0.05 })),
  );
  lidInner.rotation.x = Math.PI / 2;
  lidInner.position.set(0, 1.5, LID_DEPTH / 2);
  lidGroup.add(lidInner);

  // labio/tirador frontal
  const handleGeo = keep(roundedBox(W - 12, H_LID * 0.26, 28, 5));
  const handle = new THREE.Mesh(handleGeo, lidMat);
  handle.position.set(0, H_LID * 0.13, LID_DEPTH - 7);
  lidGroup.add(handle);

  // tornillos en el borde interior de la tapa
  const screwGeo = keep(new THREE.CylinderGeometry(3.5, 3.5, 3, 16));
  const screwSpacingX = (W - 40) / 5;
  for (let s = 0; s <= 5; s++) {
    const sx = -W / 2 + 20 + s * screwSpacingX;
    for (const sz of [LID_DEPTH * 0.06, LID_DEPTH * 0.94] as const) {
      const screw = new THREE.Mesh(screwGeo, chromeMat);
      screw.position.set(sx, 1.5, sz);
      lidGroup.add(screw);
    }
  }

  // pasadores de cierre (latches)
  const latchGeo = keep(new THREE.CylinderGeometry(6, 6, 20, 16));
  for (const lx of [-W / 2 + 58, W / 2 - 58] as const) {
    const latch = new THREE.Mesh(latchGeo, chromeMat);
    latch.position.set(lx, 4, LID_DEPTH - 28);
    lidGroup.add(latch);
  }

  // fitting central (pequeño disco en la cara interior)
  const cFitGeo = keep(new THREE.CylinderGeometry(12, 12, 4, 24));
  const cFit = new THREE.Mesh(cFitGeo, chromeMat);
  cFit.position.set(0, 2, LID_DEPTH * 0.5);
  lidGroup.add(cFit);

  // bisagras traseras negras
  const hingeBodyGeo = keep(new THREE.BoxGeometry(18, 30, 20));
  for (const hx of [-W / 2 + 44, W / 2 - 44] as const) {
    const hinge = new THREE.Mesh(hingeBodyGeo, blackHingeMat);
    hinge.position.set(hx, 14, 4);
    lidGroup.add(hinge);
  }

  // ── anclas ─────────────────────────────────────────────────────────────────
  const screenAnchor = new THREE.Object3D();
  screenAnchor.position.set(PANEL_X, SCREEN_Y, SCREEN_Z);
  body.add(screenAnchor);

  const rotorAnchor = new THREE.Object3D();
  // apunta hacia el centro del rotor: hub rojo y cubetas con cúpula
  rotorAnchor.position.set(0, ROTOR_ORIG_Y + 8, 40); // a la altura de la boca de la cámara
  body.add(rotorAnchor);

  const lidAnchor = new THREE.Object3D();
  // Sobre la cara interior de la tapa, la que mira al visitante con la tapa abierta.
  lidAnchor.position.set(0, -4, LID_DEPTH * 0.5);
  lidGroup.add(lidAnchor);

  const bodyAnchor = new THREE.Object3D();
  // En el costado izquierdo (rejilla de refrigeración), no en el centro del bloque.
  bodyAnchor.position.set(-W / 2 - 4, BODY_Y + H_BODY * 0.4, D * 0.15);
  body.add(bodyAnchor);

  // ── estado de animación ────────────────────────────────────────────────────
  let powered    = false;
  let lidAngle   = LID_OPEN;
  let rotorSpeed = 0;
  let rotorTheta = 0;

  return {
    root,
    anchors: {
      screen: screenAnchor,
      rotor:  rotorAnchor,
      lid:    lidAnchor,
      body:   bodyAnchor,
    },
    views: {
      pantalla: {
        target:    screenAnchor,
        direction: new THREE.Vector3(0.12, 0.08, 1),
        distance:  390,
      },
      rotor: {
        target:    rotorAnchor,
        direction: new THREE.Vector3(0.4, 0.85, 0.55),
        distance:  720,
      },
      lateral: {
        target:    bodyAnchor,
        direction: new THREE.Vector3(1, 0.45, 0.35),
        distance:  950,
      },
    },
    frameTop: BODY_T + 30,
    yaw: 0.56,

    setPower(on: boolean) {
      powered = on;
      if (on) { screenOn = true; sessTime = 0; }
    },

    update(dt: number, t: number, level: number) {
      // tapa
      const targetLid = powered ? LID_CLOSED : LID_OPEN;
      lidAngle += (targetLid - lidAngle) * Math.min(1, dt * 1.4);
      lidGroup.rotation.x = lidAngle;

      // pantalla
      if (screenOn) {
        sessTime += dt;
        rpmAnim  += ((powered ? DISP_RPM : 0) - rpmAnim)  * Math.min(1, dt * 0.65);
        tempAnim += ((powered ? DISP_TEMP : 22) - tempAnim) * Math.min(1, dt * 0.04);
        if (!powered && rpmAnim < 0.5) rpmAnim = 0;
        if (!powered && level < 0.01) screenOn = false;
        if (t - lastDraw > 1 / 15) { lastDraw = t; screen.redraw(); }
      }
      screenMat.emissiveIntensity = 0.60 * level;

      // rotor
      const targetSpeed = powered ? 1.0 : 0.0;
      rotorSpeed += (targetSpeed - rotorSpeed) * Math.min(1, dt * 0.55);
      if (!powered) rotorSpeed = Math.max(0, rotorSpeed - dt * 0.5);
      rotorTheta += rotorSpeed * dt * 32;
      rotor.rotation.y = rotorTheta;

      // cubetas: reposo = REST_ANGLE (visible), girando → horizontal (−π/2)
      const bucketTarget = REST_ANGLE + (-Math.PI / 2 - REST_ANGLE) * Math.min(1, rotorSpeed * 1.6);
      for (const bg of bucketGroups) {
        bg.rotation.x += (bucketTarget - bg.rotation.x) * Math.min(1, dt * 3.5);
      }
    },

    dispose,
  };
}
