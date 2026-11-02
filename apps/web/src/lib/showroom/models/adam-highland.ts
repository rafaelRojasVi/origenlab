/**
 * Adam Equipment Highland HCB — balanza portátil de precisión con protector azul.
 *
 * Fuentes de medidas:
 *   • Datasheet oficial: adamequipment.com/highland-portable-precision-balances-us.html
 *     Cuerpo: 174 × 252 × 80 mm (An × Fo × Al)      ← oficial
 *     Plato:  Ø 120 mm                                 ← oficial
 *     Protector: Ø 132 mm × 90 mm alto                ← oficial
 *
 * Estimaciones (†):
 *   • Forma orgánica del cuerpo: trazada de la fotografía oficial †
 *   • Lóbulo del asa trasera izquierda: ~12 mm de voladizo †
 *   • Falda azul de la base: 12 mm de alto, expand 5 mm †
 *   • Consola frontal inclinada ~9° hacia atrás †
 *   • Posición de botones, burbuja de nivel, almohadillas tara: fotografía oficial †
 *
 * Sistema de ejes (mm, modelo local):
 *   X  ancho: −87 … +87  (izquierda/derecha)
 *   Y  alto:  0 … 80 cuerpo; suelo en Y = 0
 *   Z  fondo: +Z = cara frontal con LCD (hacia la cámara); −Z = atrás
 *
 * El plan del cuerpo se traza EN SENTIDO HORARIO (CW) en el plano X-Y de THREE
 * (X = mundo X, Y_plan negativo = mundo +Z = frente), extruido en Z_local con
 * `geometry.rotateX(-Math.PI / 2)` para que la cara superior quede con normal
 * apuntando a +Y mundial (visible desde arriba).
 */
import * as THREE from 'three';
import type { BuiltModel } from '../types';
import { SANS, canvasTexture, disposer, lathe, roundedBox, type Draw } from './kit';

/* ── dimensiones oficiales ────────────────────────────────────────────────── */
const BW = 174;
const BD = 252;
const BH = 80;

const PAN_R = 60;           // radio del plato: Ø 120 mm
const PAN_CX = 0;
const PAN_CZ = -20;         // centro Z del plato (hacia atrás del cuerpo) †
const PAN_SUPPORT_H = 6;

const SHIELD_R = 66;        // radio del protector: Ø 132 mm
const SHIELD_H = 90;

export function build(): BuiltModel {
  const { keep, dispose } = disposer();
  const root = new THREE.Group();

  /* ── materiales ─────────────────────────────────────────────────────────── */
  const bodyMat = keep(new THREE.MeshPhysicalMaterial({
    color: 0xeef1f6, roughness: 0.40, metalness: 0.04,
    clearcoat: 0.55, clearcoatRoughness: 0.38,
  }));
  const skirtMat = keep(new THREE.MeshPhysicalMaterial({
    color: 0x1565c0, roughness: 0.52, metalness: 0.0,
    clearcoat: 0.28, clearcoatRoughness: 0.58,
  }));
  const yellowMat = keep(new THREE.MeshPhysicalMaterial({
    color: 0xf5c518, side: THREE.DoubleSide, roughness: 0.56, metalness: 0.0, clearcoat: 0.15,
  }));
  const panMat = keep(new THREE.MeshPhysicalMaterial({
    color: 0xcfd4da, metalness: 0.90, roughness: 0.16,
    clearcoat: 0.75, clearcoatRoughness: 0.10,
  }));
  const panSupportMat = keep(new THREE.MeshPhysicalMaterial({
    color: 0xe2e5e9, metalness: 0.20, roughness: 0.55,
  }));
  const buttonMat = keep(new THREE.MeshPhysicalMaterial({
    color: 0xf8f9fb, roughness: 0.33, clearcoat: 0.70, clearcoatRoughness: 0.20,
  }));
  /* Protector: azul translúcido saturado — opacidad alta para que se lea
     como el protector real en la fotografía oficial.                       */
  const shieldMat = keep(new THREE.MeshPhysicalMaterial({
    color: 0x1e88e5,
    roughness: 0.05, clearcoat: 1.0, clearcoatRoughness: 0.03,
    transparent: true, opacity: 0.52,
    side: THREE.DoubleSide, depthWrite: false,
    envMapIntensity: 2.8,
  }));
  const shieldSolidMat = keep(new THREE.MeshPhysicalMaterial({
    color: 0x1565c0, roughness: 0.35, clearcoat: 0.50,
  }));
  const seedMat = keep(new THREE.MeshPhysicalMaterial({
    color: 0xc8922a, roughness: 0.65,
  }));
  const glassBubble = keep(new THREE.MeshPhysicalMaterial({
    color: 0xdff0df, roughness: 0.02, clearcoat: 1.0, clearcoatRoughness: 0.02,
    transparent: true, opacity: 0.40, depthWrite: false, envMapIntensity: 2.0,
  }));

  /* ── plan del cuerpo (plano X-Y de THREE) ──────────────────────────────
     X  → mundo X (ancho, ±87 mm)
     Y_plan negativo → mundo +Z (frente, hacia cámara)
     Y_plan positivo → mundo –Z (atrás)
     Extrusión Z_local → mundo Y (alto) via rotateX(-π/2)
     Traza CW para que la cara superior (z=BH) apunte a +Y mundial.     */

  const W = BW / 2;   // 87
  const D = BD / 2;   // 126

  const makePlan = (expand: number): THREE.Shape => {
    const w = W + expand;
    const d = D + expand;
    const s = new THREE.Shape();

    /* CW desde el frente-centro: primero va a la DERECHA (+X) */
    s.moveTo(0, -(d - 14));

    /* frente → derecha */
    s.bezierCurveTo( w * 0.44, -(d - 13), w * 0.93, -(d - 32), w - 2, -(d - 62));
    /* derecha → atrás */
    s.bezierCurveTo( w + 1, -(d - 96), w + 1, 0, w, d * 0.42);
    /* atrás-derecha */
    s.bezierCurveTo( w - 1, d * 0.80, w - 10, d - 6, w - 28, d + 2);
    /* atrás centro */
    s.bezierCurveTo( w - 52, d + 5, w * 0.10, d + 6, 0, d + 4);
    /* atrás-izquierda (antes del lóbulo) */
    s.bezierCurveTo(-(w * 0.42), d + 5, -(w - 8), d + 4, -(w - 2), d);
    /* lóbulo del asa: sobresale ~12 mm a la izquierda */
    s.bezierCurveTo(-(w + 5), d - 4, -(w + 12), d - 18, -(w + 9), d * 0.72);
    /* izquierda → frente */
    s.bezierCurveTo(-(w + 5), d * 0.38, -(w + 1), 0, -(w - 3), -(d - 62));
    /* frente-izquierda */
    s.bezierCurveTo(-(w * 0.93), -(d - 32), -(w * 0.44), -(d - 13), 0, -(d - 14));

    return s;
  };

  /* ── cuerpo orgánico ──────────────────────────────────────────────────── */
  const BODY_BEVEL = 11;
  const bodyGeo = keep(new THREE.ExtrudeGeometry(makePlan(0), {
    depth: Math.max(BH - BODY_BEVEL * 2, 1),
    bevelEnabled: true,
    bevelSize: BODY_BEVEL,
    bevelThickness: BODY_BEVEL,
    bevelSegments: 10,
    curveSegments: 28,
  }));
  bodyGeo.rotateX(-Math.PI / 2);
  const bodyMesh = new THREE.Mesh(bodyGeo, bodyMat);
  bodyMesh.castShadow = true;
  bodyMesh.receiveShadow = true;
  root.add(bodyMesh);

  /* ── falda azul de la base ──────────────────────────────────────────── */
  const SKIRT_H = 12;
  const SKIRT_BEVEL = 3;
  // Más ancha que el cuerpo con su bisel (11 mm): con 5 quedaba escondida dentro.
  const skirtGeo = keep(new THREE.ExtrudeGeometry(makePlan(13), {
    depth: Math.max(SKIRT_H - SKIRT_BEVEL * 2, 1),
    bevelEnabled: true,
    bevelSize: SKIRT_BEVEL,
    bevelThickness: SKIRT_BEVEL,
    bevelSegments: 5,
    curveSegments: 28,
  }));
  skirtGeo.rotateX(-Math.PI / 2);
  const skirt = new THREE.Mesh(skirtGeo, skirtMat);
  skirt.castShadow = false;
  root.add(skirt);

  /* ── consola frontal inclinada ────────────────────────────────────────
     Grupo centrado en la cara frontal del cuerpo. Toda la consola (bezel,
     LCD, botones, pads tara) se añade a este grupo; la rotación x = −0.16 rad
     simula la inclinación de la cara hacia el operario.                  */
  const consoleGroup = new THREE.Group();
  /* La cara frontal del cuerpo en el plano X-Y del plan es y_plan = −(D−14),
     que en mundo queda en Z = +(D−14) = 112 mm; el bezel queda un poco
     por delante de la cara (la capa de bisel es en el borde, no en el centro). */
  /* z = 120 pone el frente del panel ~8 mm por delante de la cara frontal
     del cuerpo (Z≈112 mm) y ~6 mm por delante en la parte superior (que
     retrocede 4.8 mm por la inclinación de -0.16 rad), de modo que el
     PlaneGeometry del LCD queda siempre por delante del cuerpo.          */
  // El bisel de 11 mm lleva la cara a z ≈ 123 a media altura: la consola va por delante.
  consoleGroup.position.set(0, BH * 0.48, 129);
  consoleGroup.rotation.x = -0.16;   // ~9° inclinado hacia atrás
  root.add(consoleGroup);

  // Carcasa de la consola: un bloque blanco que une el panel con el cuerpo.
  // Sin él, de lado, el panel se veía como una pantalla flotando.
  {
    const pod = new THREE.Mesh(keep(roundedBox(144 + 10, 60 + 8, 20, 5)), bodyMat); // panel 144 × 60 mm
    pod.position.set(0, 0, -8);
    pod.castShadow = true;
    consoleGroup.add(pod);
  }

  /* ── costados: ranura de agarre y marca ──────────────────────────────── */
  {
    const gripTex = canvasTexture(512, 128, (g, w, h) => {
      g.clearRect(0, 0, w, h);
      g.fillStyle = '#b3bac5';
      g.beginPath();
      g.roundRect(8, h * 0.3, w * 0.62, h * 0.4, h * 0.2);
      g.fill();
      g.fillStyle = '#8c95a3';
      g.beginPath();
      g.roundRect(14, h * 0.4, w * 0.6, h * 0.2, h * 0.1);
      g.fill();
      g.fillStyle = '#1e5fb4';
      g.font = `800 ${h * 0.46}px ${SANS}`;
      g.textBaseline = 'middle';
      g.textAlign = 'right';
      g.fillText('ADAM', w - 10, h / 2);
    });
    keep(gripTex.texture);
    const gripMat = keep(new THREE.MeshStandardMaterial({ map: gripTex.texture, transparent: true, roughness: 0.5, polygonOffset: true, polygonOffsetFactor: -2 }));
    for (const side of [-1, 1] as const) {
      const plate = new THREE.Mesh(keep(new THREE.PlaneGeometry(150, 38)), gripMat);
      plate.rotation.y = side * Math.PI / 2;
      plate.position.set(side * (W + 12.4), BH * 0.5, -8);
      root.add(plate);
    }
  }

  /* ── panel frontal — LCD y bezel ─────────────────────────────────────── */
  let screenOn = false;
  let readValue = 0.0;
  let readTarget = 0.0;

  const PANEL_W = 144;
  const PANEL_H = 60;

  const drawFront: Draw = (g, w, h) => {
    g.clearRect(0, 0, w, h);

    /* escala mm→pixel */
    const sx = w / PANEL_W;
    const sy = h / PANEL_H;

    /* fondo del bezel */
    g.fillStyle = '#f3f5f8';
    g.fillRect(0, 0, w, h);

    /* franjas azules laterales al LCD */
    const lcdPx = 8 * sx;         // margen izq
    const lcdPy = 5 * sy;         // margen sup
    const lcdPw = 112 * sx;       // ancho LCD
    const lcdPh = 38 * sy;        // alto LCD
    g.fillStyle = '#1e88e5';
    g.fillRect(0, lcdPy, lcdPx, lcdPh);
    g.fillRect(lcdPx + lcdPw + 1, lcdPy, w - (lcdPx + lcdPw + 1), lcdPh * 0.70);

    /* pantalla LCD */
    if (screenOn) {
      /* retroiluminación azul */
      g.fillStyle = '#55b8f5';
      g.beginPath();
      g.roundRect(lcdPx, lcdPy, lcdPw, lcdPh, 4);
      g.fill();

      /* Highland™ — esquina superior izquierda del LCD */
      g.fillStyle = '#0f2d6e';
      g.font = `600 ${9.5 * sy}px ${SANS}`;
      g.textAlign = 'left';
      g.textBaseline = 'top';
      g.fillText('Highland™', lcdPx + 2 * sx, lcdPy + 1 * sy);

      /* punto LED azul */
      g.fillStyle = '#0055ee';
      g.shadowColor = '#4daaf8';
      g.shadowBlur = 6 * sx;
      g.beginPath();
      g.arc(lcdPx + 4 * sx, lcdPy + lcdPh * 0.55, 2.8 * sx, 0, Math.PI * 2);
      g.fill();
      g.shadowBlur = 0;

      /* dígitos */
      const val = readValue.toFixed(3);
      g.fillStyle = '#0a1e3c';
      g.font = `700 ${lcdPh * 0.56}px monospace`;
      g.textAlign = 'right';
      g.textBaseline = 'middle';
      g.fillText(val, lcdPx + lcdPw - 12 * sx, lcdPy + lcdPh * 0.54);

      /* unidad g */
      g.font = `600 ${12 * sy}px ${SANS}`;
      g.fillText('g', lcdPx + lcdPw - 1 * sx, lcdPy + lcdPh * 0.55);

      /* STABLE */
      if (Math.abs(readValue - readTarget) < 0.01) {
        g.fillStyle = '#0a2962';
        g.font = `700 ${8.5 * sy}px ${SANS}`;
        g.textAlign = 'left';
        g.textBaseline = 'bottom';
        g.fillText('STABLE', lcdPx + 2 * sx, lcdPy + lcdPh - 1 * sy);
      }

      /* bargraph ticks */
      const nTicks = 18;
      const tickW = lcdPw / nTicks * 0.55;
      const tickGap = lcdPw / nTicks;
      const barPos = Math.round((readValue / 15.24) * 14);
      for (let i = 0; i < nTicks; i++) {
        const tH = (i % 4 === 0) ? 5.5 * sy : 3.5 * sy;
        const filled = i <= barPos;
        g.fillStyle = filled ? (i < 12 ? '#1a52c8bb' : '#f57c00bb') : '#1240a044';
        g.fillRect(lcdPx + i * tickGap, lcdPy + lcdPh - tH - 1 * sy, tickW, tH);
      }
    } else {
      /* pantalla apagada: gris-azul oscuro */
      g.fillStyle = '#2a3540';
      g.beginPath();
      g.roundRect(lcdPx, lcdPy, lcdPw, lcdPh, 4);
      g.fill();
    }

    /* logo ≡≡ ADAM® — parte derecha del bezel, junto a la franja azul */
    const adamX = lcdPx + lcdPw + 8 * sx;  // FIX: was (lcdPx+lcdPw+8)*sx
    const adamY = lcdPy + 2 * sy;
    /* doble barra (icono de Adam Equipment) */
    g.fillStyle = '#1252a8';
    g.fillRect(adamX, adamY, 6 * sx, 2.8 * sy);
    g.fillRect(adamX, adamY + 4 * sy, 6 * sx, 2.8 * sy);
    g.fillRect(adamX + 8 * sx, adamY, 6 * sx, 2.8 * sy);
    g.fillRect(adamX + 8 * sx, adamY + 4 * sy, 6 * sx, 2.8 * sy);
    /* texto */
    g.fillStyle = '#1252a8';
    g.font = `800 ${10.5 * sy}px ${SANS}`;
    g.textAlign = 'left';
    g.textBaseline = 'top';
    g.fillText('ADAM®', adamX + 16 * sx, adamY);

    /* área de botones: fondo ligeramente recesado.
       btnCY: 52 mm desde la parte superior del panel = 30-8 = dentro del canvas. */
    const btnAreaY = lcdPy + lcdPh + 3 * sy;
    g.fillStyle = '#e8edf3';
    g.beginPath();
    g.roundRect(4 * sx, btnAreaY, w - 8 * sx, h - btnAreaY - 2 * sy, 5);
    g.fill();

    const btnCY = 52 * sy;           // centro Y de la fila de botones (52/60 del panel)
    /* marcadores de botones (cinco círculos en arco) */
    // Bucle más estrecho: las almohadillas amarillas de las esquinas quedan fuera.
    const bxs = [-30, -15, 0, 15, 30];
    for (let i = 0; i < 5; i++) {
      const bpx = w / 2 + bxs[i] * sx;
      g.strokeStyle = '#c0c8d4';
      g.lineWidth = 1.5 * sx;
      g.beginPath();
      g.arc(bpx, btnCY, 7.5 * sx, 0, Math.PI * 2);
      g.stroke();
    }

    /* bucle azul alrededor de los botones (óvalo) — ajustado para caber en canvas */
    g.strokeStyle = '#1e88e5';
    g.lineWidth = 3 * sx;
    g.beginPath();
    g.ellipse(w / 2, btnCY, 38 * sx, 7 * sy, 0, 0, Math.PI * 2);
    g.stroke();
  };

  const { texture: frontTex, redraw: redrawFront } = canvasTexture(1024, 428, drawFront);
  keep(frontTex);

  const frontMat = keep(new THREE.MeshPhysicalMaterial({
    map: frontTex,
    emissive: 0xffffff,
    emissiveMap: frontTex,
    emissiveIntensity: 0.0,
    roughness: 0.28, clearcoat: 0.65, clearcoatRoughness: 0.12,
    polygonOffset: true, polygonOffsetFactor: -2,
  }));
  const frontPanel = new THREE.Mesh(
    keep(new THREE.PlaneGeometry(PANEL_W, PANEL_H)),
    frontMat,
  );
  frontPanel.position.set(0, 0, 3);
  consoleGroup.add(frontPanel);

  /* ── botones 3D (cilindros sobre el panel) ───────────────────────────── */
  /* Disposición en arco (de izq-abajo a der-arriba) como en la fotografía */
  const btnDefs: [number, number, string][] = [
    [-30, -22, '⏻'],
    [-15, -22, 'Smpl'],
    [  0, -22, 'Mode'],
    [ 15, -22, 'Print'],
    [ 30, -22, '%'],
  ];
  const btnGeo = keep(new THREE.CylinderGeometry(6.5, 6.5, 3.5, 28));
  btnGeo.rotateX(Math.PI / 2);

  for (const [bx, by, label] of btnDefs) {
    const btn = new THREE.Mesh(btnGeo, buttonMat);
    btn.position.set(bx, by, 4.5);
    consoleGroup.add(btn);

    /* etiqueta sobre cada botón */
    const { texture: bTex } = canvasTexture(192, 192, (g, cw, ch) => {
      g.clearRect(0, 0, cw, ch);
      g.fillStyle = '#f0f2f6';
      g.beginPath();
      g.arc(cw / 2, ch / 2, cw / 2 - 3, 0, Math.PI * 2);
      g.fill();
      g.strokeStyle = '#bac4d0';
      g.lineWidth = 4;
      g.stroke();
      g.fillStyle = '#1a2030';
      const fs = label.length > 1 ? cw * 0.23 : cw * 0.40;
      g.font = `600 ${fs}px ${SANS}`;
      g.textAlign = 'center';
      g.textBaseline = 'middle';
      g.fillText(label, cw / 2, ch / 2);
    });
    keep(bTex);
    const bDisc = new THREE.Mesh(
      keep(new THREE.CircleGeometry(6.2, 24)),
      keep(new THREE.MeshBasicMaterial({ map: bTex, transparent: true })),
    );
    bDisc.position.set(bx, by, 6);
    bDisc.renderOrder = 1;
    consoleGroup.add(bDisc);
  }

  /* ── almohadillas tara amarillas (>0/T<) en esquinas inferiores ───────── */
  /* Segmentos de toroide (media luna) en cada esquina del panel frontal,
     curveando con la forma del cuerpo.                                   */
  for (const sign of [-1, 1] as const) {
    // Almohadilla plana, al ras de la consola, como en la foto: una cuña amarilla
    // en la esquina inferior (antes era un toro que sobresalía como un anillo).
    const padShape = new THREE.Shape();
    // Más pequeña y baja: ocupa la esquina, sin llegar a los botones.
    padShape.moveTo(0, 0);
    padShape.lineTo(26, 0);
    padShape.quadraticCurveTo(24, 13, 4, 16);
    padShape.quadraticCurveTo(0, 16, 0, 12);
    padShape.lineTo(0, 0);
    const padGeo = keep(new THREE.ExtrudeGeometry(padShape, { depth: 0.6, bevelEnabled: true, bevelSize: 0.6, bevelThickness: 0.4, bevelSegments: 3 }));
    const tara = new THREE.Mesh(padGeo, yellowMat);
    tara.scale.x = sign > 0 ? -1 : 1;
    tara.position.set(sign * (PANEL_W / 2 - 2), -PANEL_H / 2 + 1, 3.2);
    consoleGroup.add(tara);

    /* etiqueta ">0/T<" */
    const { texture: taraTex } = canvasTexture(128, 64, (g, cw, ch) => {
      g.clearRect(0, 0, cw, ch);
      g.fillStyle = '#1a1e2e';
      g.font = `700 ${ch * 0.52}px ${SANS}`;
      g.textAlign = 'center';
      g.textBaseline = 'middle';
      g.fillText('>0/T<', cw / 2, ch / 2);
    });
    keep(taraTex);
    const taraLabel = new THREE.Mesh(
      keep(new THREE.PlaneGeometry(12, 6)),
      keep(new THREE.MeshBasicMaterial({ map: taraTex, transparent: true })),
    );
    taraLabel.position.set(sign * (PANEL_W / 2 - 12), -PANEL_H / 2 + 6, 4.6);
    taraLabel.renderOrder = 2;
    consoleGroup.add(taraLabel);
  }

  /* ── burbuja de nivel ──────────────────────────────────────────────────
     Pequeña lente circular sobre la cara superior del cuerpo, entre la
     consola frontal y el collar del protector.                           */
  const bubbleRingGeo = keep(new THREE.TorusGeometry(5.2, 1.8, 8, 24));
  const bubbleRing = new THREE.Mesh(bubbleRingGeo, shieldSolidMat);
  bubbleRing.rotation.x = Math.PI / 2;
  bubbleRing.position.set(0, BH + 1.5, 48);
  root.add(bubbleRing);

  const bubbleLensGeo = keep(new THREE.CircleGeometry(5, 24));
  const bubbleLens = new THREE.Mesh(bubbleLensGeo, glassBubble);
  bubbleLens.rotation.x = -Math.PI / 2;
  bubbleLens.position.set(0, BH + 3, 48);
  bubbleLens.renderOrder = 1;
  root.add(bubbleLens);

  /* punto de la burbuja (ligeramente descentrado para simular nivel) */
  const bDotMat = keep(new THREE.MeshBasicMaterial({
    color: 0x22222e, transparent: true, opacity: 0.55,
  }));
  const bDotGeo = keep(new THREE.CircleGeometry(1.3, 16));
  const bubbleDot = new THREE.Mesh(bDotGeo, bDotMat);
  bubbleDot.rotation.x = -Math.PI / 2;
  bubbleDot.position.set(0.4, BH + 3.1, 48.2);
  bubbleDot.renderOrder = 2;
  root.add(bubbleDot);

  /* ── collar azul del protector ─────────────────────────────────────────
     Anillo sólido azul donde el protector apoya sobre el cuerpo.         */
  const collarGeo = keep(new THREE.TorusGeometry(SHIELD_R + 2.5, 5, 10, 64));
  const collar = new THREE.Mesh(collarGeo, shieldSolidMat);
  collar.rotation.x = Math.PI / 2;
  collar.position.set(PAN_CX, BH + PAN_SUPPORT_H - 0.5, PAN_CZ);
  root.add(collar);

  /* ── soporte del plato ─────────────────────────────────────────────────── */
  const platformGroup = new THREE.Group();
  platformGroup.position.set(PAN_CX, BH, PAN_CZ);
  root.add(platformGroup);

  const supportRingGeo = keep(new THREE.TorusGeometry(PAN_R * 0.84, 2.5, 10, 48));
  const supportRing = new THREE.Mesh(supportRingGeo, panSupportMat);
  supportRing.rotation.x = Math.PI / 2;
  supportRing.position.y = PAN_SUPPORT_H;
  platformGroup.add(supportRing);

  for (let i = 0; i < 3; i++) {
    const a = (i / 3) * Math.PI * 2 + 0.3;
    const postGeo = keep(new THREE.CylinderGeometry(1.8, 1.8, PAN_SUPPORT_H, 10));
    const post = new THREE.Mesh(postGeo, panSupportMat);
    post.position.set(
      Math.cos(a) * PAN_R * 0.84,
      PAN_SUPPORT_H / 2,
      Math.sin(a) * PAN_R * 0.84,
    );
    platformGroup.add(post);
  }

  /* ── plato de pesaje ─────────────────────────────────────────────────── */
  const panGeo = keep(lathe([
    [0, 0], [PAN_R, 0], [PAN_R + 2, 1.5], [PAN_R + 2, 3], [PAN_R, 3],
    [4, 3], [0, 3],
  ]));
  const panMesh = new THREE.Mesh(panGeo, panMat);
  panMesh.position.set(PAN_CX, BH + PAN_SUPPORT_H, PAN_CZ);
  panMesh.castShadow = true;
  panMesh.receiveShadow = true;
  root.add(panMesh);

  /* ── muestra: barquillo blanco con semillas en montón ─────────────────── */
  const sampleGroup = new THREE.Group();
  sampleGroup.position.set(PAN_CX, BH + PAN_SUPPORT_H + 3, PAN_CZ);
  sampleGroup.visible = false;
  root.add(sampleGroup);

  /* barquillo de pesaje (blanco, Ø 60 mm) */
  const boatMat = keep(new THREE.MeshPhysicalMaterial({
    color: 0xfafbfc, roughness: 0.45, clearcoat: 0.35,
  }));
  const boatGeo = keep(lathe([
    [0, 0], [28, 0], [30, 2.5], [30, 7.5], [28, 7.5], [2, 7.5], [0, 7.5],
  ], 48));
  sampleGroup.add(new THREE.Mesh(boatGeo, boatMat));

  /* semillas — esferas ocre en montón piramidal */
  const seedGeo = keep(new THREE.SphereGeometry(2.2, 8, 6));
  const SEED_COUNT = 100;
  const seeds = new THREE.InstancedMesh(seedGeo, seedMat, SEED_COUNT);
  seeds.castShadow = true;
  const dummy = new THREE.Object3D();
  const rng = (n: number) => { const x = Math.sin(n * 9301 + 49297) * 233280; return x - Math.floor(x); };
  for (let i = 0; i < SEED_COUNT; i++) {
    const r = rng(i * 3) * 20;
    const a = rng(i * 3 + 1) * Math.PI * 2;
    /* semillas más altas hacia el centro → montón convexo */
    const heap = 2.5 + Math.max(0, 1 - r / 20) * 10 + rng(i * 3 + 2) * 3;
    dummy.position.set(Math.cos(a) * r, heap, Math.sin(a) * r);
    dummy.scale.setScalar(0.65 + rng(i * 7) * 0.70);
    dummy.rotation.set(rng(i * 11) * Math.PI, rng(i * 5) * Math.PI * 2, rng(i * 13) * Math.PI);
    dummy.updateMatrix();
    seeds.setMatrixAt(i, dummy.matrix);
  }
  seeds.instanceMatrix.needsUpdate = true;
  sampleGroup.add(seeds);

  /* ── protector (breeze shield) ───────────────────────────────────────── */
  const shieldGroup = new THREE.Group();
  shieldGroup.position.set(PAN_CX, BH + PAN_SUPPORT_H - 2, PAN_CZ);
  root.add(shieldGroup);

  /* pared cilíndrica lateral */
  const shieldWallGeo = keep(new THREE.CylinderGeometry(SHIELD_R, SHIELD_R, SHIELD_H, 64, 1, true));
  const shieldWall = new THREE.Mesh(shieldWallGeo, shieldMat);
  shieldWall.position.y = SHIELD_H / 2;
  shieldWall.renderOrder = 2;
  shieldGroup.add(shieldWall);

  /* anillo sólido inferior del protector */
  const shieldBaseGeo = keep(new THREE.TorusGeometry(SHIELD_R, 4, 8, 64));
  const shieldBase = new THREE.Mesh(shieldBaseGeo, shieldSolidMat);
  shieldBase.rotation.x = Math.PI / 2;
  shieldBase.position.y = 1;
  shieldGroup.add(shieldBase);

  /* anillo sólido superior (borde de la tapa) */
  const lidRimGeo = keep(new THREE.TorusGeometry(SHIELD_R, 3.5, 8, 64));
  const lidRim = new THREE.Mesh(lidRimGeo, shieldSolidMat);
  lidRim.rotation.x = Math.PI / 2;
  lidRim.position.y = SHIELD_H;
  shieldGroup.add(lidRim);

  /* disco de la tapa con orificio central y anillo de orificios de ventilación */
  const { texture: lidTex } = canvasTexture(512, 512, (g, cw) => {
    g.clearRect(0, 0, cw, cw);
    const cx = cw / 2, cy = cw / 2;
    const R = cw * 0.465;
    /* disco azul */
    g.fillStyle = 'rgba(30,136,229,0.72)';
    g.beginPath(); g.arc(cx, cy, R, 0, Math.PI * 2); g.fill();
    g.strokeStyle = 'rgba(21,101,192,0.85)';
    g.lineWidth = 5; g.stroke();
    /* orificio central */
    g.fillStyle = 'rgba(0,0,0,0.60)';
    g.beginPath(); g.arc(cx, cy, R * 0.21, 0, Math.PI * 2); g.fill();
    /* anillo de orificios de ventilación (10 orificios) */
    for (let i = 0; i < 10; i++) {
      const a = (i / 10) * Math.PI * 2 - Math.PI / 2;
      g.beginPath();
      g.arc(cx + Math.cos(a) * R * 0.55, cy + Math.sin(a) * R * 0.55,
            R * 0.08, 0, Math.PI * 2);
      g.fill();
    }
  });
  keep(lidTex);

  const lidMat = keep(new THREE.MeshPhysicalMaterial({
    map: lidTex,
    alphaMap: lidTex,
    transparent: true, opacity: 0.75,
    roughness: 0.05, clearcoat: 1.0, clearcoatRoughness: 0.03,
    depthWrite: false, envMapIntensity: 2.5, side: THREE.DoubleSide,
  }));
  const lidGeo = keep(new THREE.CircleGeometry(SHIELD_R - 3, 64));
  const lidMesh = new THREE.Mesh(lidGeo, lidMat);
  lidMesh.rotation.x = -Math.PI / 2;
  lidMesh.position.y = SHIELD_H + 0.5;
  lidMesh.renderOrder = 3;
  shieldGroup.add(lidMesh);

  /* ── anclas ──────────────────────────────────────────────────────────────
     Las cuatro anclas deben estar bien separadas en la vista general.
     • screen: cara frontal de la consola (en consoleGroup, se mueve con la inclinación)
     • pan:    superficie del plato, para la vista "plato"
     • shield: borde delantero de la tapa del protector
     • top:    lóbulo del asa trasera-izquierda (conectividad y interfaces)   */

  /* screen — frontal de la consola */
  const screenAnchor = new THREE.Object3D();
  screenAnchor.position.set(0, 6, 5);   /* centrado sobre el LCD (6 mm arriba del centro del panel) */
  consoleGroup.add(screenAnchor);

  /* pan — sobre el plato, ligeramente elevado */
  const panAnchor = new THREE.Object3D();
  panAnchor.position.set(PAN_CX, BH + PAN_SUPPORT_H + 20, PAN_CZ);
  root.add(panAnchor);

  /* shield — borde delantero de la tapa del protector
     En shieldGroup: (0, SHIELD_H, SHIELD_R-5) → mundo aprox (0, 174, PAN_CZ+SHIELD_R) */
  const shieldAnchor = new THREE.Object3D();
  shieldAnchor.position.set(0, SHIELD_H + 2, SHIELD_R - 5);
  shieldGroup.add(shieldAnchor);

  /* top → lóbulo del asa trasera-izquierda
     En el plan: X ≈ −(W+10) = −97, Y_plan ≈ D*0.72 = 91
     En mundo: X ≈ −97, Z ≈ −91, Y ≈ BH/2 = 40           */
  const handleAnchor = new THREE.Object3D();
  handleAnchor.position.set(-90, BH / 2, -(D * 0.72));
  root.add(handleAnchor);

  /* ── animación ───────────────────────────────────────────────────────── */
  let powered = false;
  let lastRedraw = 0;
  let shieldT = 0;
  let sampleVisible = false;

  return {
    root,
    anchors: {
      screen: screenAnchor,
      pan: panAnchor,
      shield: shieldAnchor,
      top: handleAnchor,
    },
    views: {
      /* Pantalla: cámara directamente delante de la consola inclinada.
         La dirección (0, 0.16, 1) coincide con la normal del panel tras
         aplicar la rotación .x=-0.16; worldDir() añade el yaw del stage. */
      pantalla: {
        target: screenAnchor,
        direction: new THREE.Vector3(0.0, 0.16, 1.0),
        distance: 175,
      },
      /* Plato: desde delante y ligeramente arriba para ver el plato y el
         protector sin mirar en picado. */
      plato: {
        target: panAnchor,
        direction: new THREE.Vector3(-0.05, 0.52, 0.85),
        distance: 270,
      },
      /* Lateral: desde la izquierda (lóbulo del asa) para ver el perfil. */
      lateral: {
        target: handleAnchor,
        direction: new THREE.Vector3(-1.0, 0.35, 0.5),
        distance: 380,
      },
    },
    frameTop: BH + SHIELD_H + 14,
    yaw: 0.42,

    setPower(on) {
      powered = on;
      if (on) {
        screenOn = true;
        shieldT = 0;
        readTarget = 0.0;
        readValue = 0.0;
      }
    },

    update(dt, _t, level) {
      /* avanzar temporizador del protector */
      if (powered) shieldT = Math.min(shieldT + dt, 6);
      else          shieldT = Math.max(shieldT - dt * 2, 0);

      /* ciclo de levantamiento: 0–1 s quieto, 1–2 s sube, 2–3 s arriba,
         3–4 s baja, luego oscilación residual                             */
      const LIFT = 78;
      const shieldLift = (() => {
        if (shieldT < 1.0) return 0;
        if (shieldT < 2.0) return (shieldT - 1.0) * LIFT;
        if (shieldT < 3.0) return LIFT;
        if (shieldT < 4.0) return (4.0 - shieldT) * LIFT;
        return Math.max(0, Math.sin((shieldT - 4) * 0.5) * 5);
      })();

      const finalLift = level < 0.01 ? 0 : shieldLift;
      shieldGroup.position.y = (BH + PAN_SUPPORT_H - 2) + finalLift;

      /* muestra visible cuando el protector está suficientemente arriba */
      const showSample = finalLift > 40 && level > 0.4;
      if (showSample !== sampleVisible) {
        sampleVisible = showSample;
        sampleGroup.visible = sampleVisible;
      }

      /* lectura: sube cuando la muestra es visible */
      readTarget = sampleVisible && shieldT > 2.5 ? 15.24 : 0.0;
      readValue += (readTarget - readValue) * Math.min(1, dt * 1.8);

      /* apagar pantalla cuando level cae a cero */
      if (screenOn && !powered && level < 0.02) {
        screenOn = false;
        readValue = 0;
      }

      /* redibujar pantalla a 20 fps */
      if (screenOn && _t - lastRedraw > 1 / 20) {
        lastRedraw = _t;
        redrawFront();
      }

      /* emisión LCD */
      frontMat.emissiveIntensity = 0.58 * level;
    },

    dispose,
  };
}
