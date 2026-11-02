/**
 * IKA ULTRA-TURRAX T 25 digital — dispersor/homogeneizador de sobremesa.
 *
 * Fuentes de las medidas:
 *   IKA data sheet «T 25 digital ULTRA-TURRAX» (cdn.abicart.com, 2018-11-21)
 *   Mahidol University equipment record (science.mahidol.ac.th, 2026-04)
 *
 * Carcasa del accionamiento:  87 × 106 × 271 mm (W × D × H)  — oficial
 * Brazo de extensión (boom):  Ø 13 × 160 mm                   — oficial
 * Cabezal dispersor S25N-18G: Ø 18 mm nominal                 — oficial
 *   Longitud del cabezal ~45 mm: estimada a partir de la fotografía oficial.
 * Profundidad de inmersión:   40–165 mm (rango oficial)
 *
 * Todo en milímetros; la escena lo reduce a escala.
 */
import * as THREE from 'three';
import type { BuiltModel } from '../types';
import { SANS, canvasTexture, lathe, labGlass, disposer, roundedBox } from './kit';

/* ── dimensiones del accionamiento ── */
const HW = 87;    // ancho
const HD = 106;   // profundidad
const HH = 271;   // altura
const BEVEL = 5;

const GRIP_H = 58;  // sección oscura inferior
const NOSE_L = 28;  // nariz de acoplamiento (sobresale por la base)

/* ── boom y cabezal ── */
const BOOM_R = 6.5;
const BOOM_L = 160;
const HEAD_R = 9;
const HEAD_L = 45;

export function build(): BuiltModel {
  const { keep, dispose } = disposer();
  const root = new THREE.Group();

  /* ── materiales ── */
  const ikaBlue = keep(new THREE.MeshPhysicalMaterial({
    color: 0x2d7ec4, roughness: 0.42, metalness: 0.04,
    clearcoat: 0.40, clearcoatRoughness: 0.45,
  }));
  const ikaDark = keep(new THREE.MeshPhysicalMaterial({
    color: 0x1a1d22, roughness: 0.55, metalness: 0.08,
    clearcoat: 0.20, clearcoatRoughness: 0.6,
  }));
  const steel = keep(new THREE.MeshPhysicalMaterial({
    color: 0xd4d8dc, metalness: 1, roughness: 0.22,
  }));
  const brushed = keep(new THREE.MeshPhysicalMaterial({
    color: 0xc3c8cc, metalness: 1, roughness: 0.38,
  }));
  const black = keep(new THREE.MeshStandardMaterial({ color: 0x111316, roughness: 0.65 }));
  const alu = keep(new THREE.MeshPhysicalMaterial({ color: 0xdfe2e4, metalness: 0.9, roughness: 0.34 }));
  const rubber = keep(new THREE.MeshStandardMaterial({ color: 0x101113, roughness: 0.9 }));

  /* ══════════════════════════════════════════════════════════════
     CARCASA: perfil extruido, dos secciones visuales.
     X = ancho, Y = alto (base en y=0), Z = profundidad (frente en +HD/2).
  ══════════════════════════════════════════════════════════════ */
  const housing = new THREE.Group();
  root.add(housing);

  const R = 8;  // radio de esquinas del perfil XY

  /* ── sección azul (y = GRIP_H … HH) ── */
  {
    const s = new THREE.Shape();
    s.moveTo(-HW / 2 + R, GRIP_H);
    s.lineTo(HW / 2 - R, GRIP_H);
    s.quadraticCurveTo(HW / 2, GRIP_H, HW / 2, GRIP_H + R);
    s.lineTo(HW / 2, HH - R);
    s.quadraticCurveTo(HW / 2, HH, HW / 2 - R, HH);
    s.lineTo(-HW / 2 + R, HH);
    s.quadraticCurveTo(-HW / 2, HH, -HW / 2, HH - R);
    s.lineTo(-HW / 2, GRIP_H + R);
    s.quadraticCurveTo(-HW / 2, GRIP_H, -HW / 2 + R, GRIP_H);
    const geo = keep(new THREE.ExtrudeGeometry(s, {
      depth: HD - BEVEL * 2,
      bevelEnabled: true, bevelSize: BEVEL, bevelThickness: BEVEL,
      bevelSegments: 5, curveSegments: 24,
    }));
    geo.translate(0, 0, -(HD - BEVEL * 2) / 2);
    const m = new THREE.Mesh(geo, ikaBlue);
    m.castShadow = true;
    housing.add(m);
  }

  /* ── sección oscura (y = 0 … GRIP_H+8, ligeramente más estrecha) ── */
  const gW = HW - 3;
  const gD = HD - 4;
  const gR = 5;
  {
    const s = new THREE.Shape();
    s.moveTo(-gW / 2 + gR, 0);
    s.lineTo(gW / 2 - gR, 0);
    s.quadraticCurveTo(gW / 2, 0, gW / 2, gR);
    s.lineTo(gW / 2, GRIP_H + 8);
    s.quadraticCurveTo(gW / 2, GRIP_H + 12, gW / 2 - gR, GRIP_H + 12);
    s.lineTo(-gW / 2 + gR, GRIP_H + 12);
    s.quadraticCurveTo(-gW / 2, GRIP_H + 12, -gW / 2, GRIP_H + 8);
    s.lineTo(-gW / 2, gR);
    s.quadraticCurveTo(-gW / 2, 0, -gW / 2 + gR, 0);
    const geo = keep(new THREE.ExtrudeGeometry(s, {
      depth: gD - 8,
      bevelEnabled: true, bevelSize: 4, bevelThickness: 4,
      bevelSegments: 4, curveSegments: 16,
    }));
    geo.translate(0, 0, -(gD - 8) / 2);
    const m = new THREE.Mesh(geo, ikaDark);
    m.castShadow = true;
    housing.add(m);
  }

  /* ── nariz del acoplamiento ── */
  {
    const nose = new THREE.Mesh(keep(new THREE.CylinderGeometry(14, 16, NOSE_L, 32)), ikaDark);
    nose.position.y = -NOSE_L / 2;
    nose.castShadow = true;
    housing.add(nose);
    const ring = new THREE.Mesh(keep(new THREE.TorusGeometry(14, 2, 8, 32)), brushed);
    ring.rotation.x = Math.PI / 2;
    ring.position.y = -4;
    housing.add(ring);
  }

  /* ══════════════════════════════════════════════════════════════
     PANEL FRONTAL EN CANVAS
     Mapeo: canvas y=0 → housing y=264mm (top panel)
            canvas y=h → housing y=7mm (bottom panel)
            canvas y=f → housing y = 264 − f × 257mm

     Layout (sigue la foto: logo → display → dial → switch):
       0–18%   cabecera oscura (IKA® + T 25 digital)
      18–27%   ULTRA-TURRAX®
      30–50%   recuadro LED  (centro f=0.40 → housing y ≈ 161mm)
      52–70%   zona del dial (centro f=0.61 → housing y ≈ 107mm)
      74–80%   interruptor I/O
      81–100%  degradado oscuro → se funde con la sección de acoplamiento
  ══════════════════════════════════════════════════════════════ */
  let displayOn = false;
  let displayRpm = 0;

  const PW = 512;
  const PH = Math.round(PW * (HH - 14) / (HW - 12)); // ≈ 1760 px

  const { texture: panelTex, redraw: panelRedraw } = canvasTexture(PW, PH, (g, w, h) => {
    /* fondo azul */
    g.fillStyle = '#2d7ec4';
    g.fillRect(0, 0, w, h);

    /* cabecera oscura 0–18% */
    const hdrH = h * 0.18;
    g.fillStyle = '#0d1a28';
    g.fillRect(0, 0, w, hdrH);
    g.fillStyle = '#ffffff';
    g.font = `900 ${hdrH * 0.40}px ${SANS}`;
    g.textBaseline = 'middle';
    g.textAlign = 'left';
    g.fillText('IKA®', w * 0.07, hdrH * 0.35);
    g.fillStyle = '#8ec8f0';
    g.font = `600 ${hdrH * 0.24}px ${SANS}`;
    g.fillText('T 25 digital', w * 0.07, hdrH * 0.70);
    g.fillStyle = '#3a6a8a';
    g.font = `400 ${hdrH * 0.14}px ${SANS}`;
    g.textAlign = 'right';
    // (Sin cifras de especificación en el rótulo: las cifras sólo salen de los datos con fuente.)

    /* ULTRA-TURRAX® 18–27% */
    g.fillStyle = '#d8eeff';
    g.font = `700 ${h * 0.030}px ${SANS}`;
    g.textAlign = 'left';
    g.textBaseline = 'middle';
    g.fillText('ULTRA-TURRAX®', w * 0.07, h * 0.225);

    /* recuadro LED 30–50% */
    const lx = w * 0.05, ly = h * 0.305;
    const lw = w * 0.90, lh = h * 0.190;
    g.fillStyle = '#080b0e';
    g.beginPath();
    g.roundRect(lx, ly, lw, lh, 8);
    g.fill();
    g.strokeStyle = '#1a3650';
    g.lineWidth = 2;
    g.stroke();

    if (displayOn) {
      const val = Math.round(displayRpm / 100);
      g.fillStyle = '#ff3820';
      g.shadowColor = '#ff5535';
      g.shadowBlur = 22;
      g.font = `900 ${lh * 0.66}px ${SANS}`;
      g.textAlign = 'right';
      g.textBaseline = 'middle';
      g.fillText(val.toString().padStart(3, ' '), lx + lw * 0.92, ly + lh * 0.45);
      g.shadowBlur = 0;
      g.fillStyle = '#cc2a0a';
      g.font = `500 ${lh * 0.19}px ${SANS}`;
      g.textAlign = 'left';
      g.fillText('× 100 min⁻¹', lx + lw * 0.06, ly + lh * 0.82);
      g.fillStyle = '#22cc44';
      g.shadowColor = '#22ff44';
      g.shadowBlur = 12;
      g.beginPath();
      g.arc(lx + lw * 0.92 - 24, ly + lh * 0.22, 5, 0, Math.PI * 2);
      g.fill();
      g.shadowBlur = 0;
    } else {
      g.fillStyle = '#181e26';
      g.font = `900 ${lh * 0.66}px ${SANS}`;
      g.textAlign = 'right';
      g.textBaseline = 'middle';
      g.fillText('888', lx + lw * 0.92, ly + lh * 0.45);
    }

    /* arco de marcas del dial 52–70% (centro f=0.61) */
    const cx = w * 0.50, cy = h * 0.610, cr = w * 0.195;
    g.strokeStyle = '#1a5080';
    g.lineWidth = 2;
    g.beginPath();
    g.arc(cx, cy, cr * 1.44, Math.PI * 0.72, Math.PI * 2.28);
    g.stroke();
    for (let i = 0; i <= 10; i++) {
      const a = Math.PI * 0.72 + (i * Math.PI * 1.56) / 10;
      const major = i === 0 || i === 5 || i === 10;
      g.lineWidth = major ? 2 : 1;
      g.beginPath();
      g.moveTo(cx + cr * (major ? 1.14 : 1.24) * Math.cos(a),
               cy + cr * (major ? 1.14 : 1.24) * Math.sin(a));
      g.lineTo(cx + cr * 1.44 * Math.cos(a), cy + cr * 1.44 * Math.sin(a));
      g.stroke();
    }
    g.fillStyle = '#4a7aa0';
    g.font = `500 ${h * 0.022}px ${SANS}`;
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText('min', cx + cr * 1.64 * Math.cos(Math.PI * 0.72),
                      cy + cr * 1.64 * Math.sin(Math.PI * 0.72));
    g.fillText('max', cx + cr * 1.64 * Math.cos(Math.PI * 2.28),
                      cy + cr * 1.64 * Math.sin(Math.PI * 2.28));

    /* interruptor 74–80% */
    g.fillStyle = '#0d1a28';
    g.beginPath();
    g.roundRect(w * 0.27, h * 0.755, w * 0.46, h * 0.062, h * 0.015);
    g.fill();
    g.strokeStyle = '#264a66';
    g.lineWidth = 2;
    g.stroke();
    g.fillStyle = '#5a90b8';
    g.font = `600 ${h * 0.026}px ${SANS}`;
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText('I  /  O', w * 0.50, h * 0.786);

    /* triángulo de advertencia (a la derecha del dial) */
    g.save();
    g.translate(w * 0.83, h * 0.730);
    const ts = w * 0.090;
    g.fillStyle = '#f5c518';
    g.beginPath();
    g.moveTo(0, -ts);
    g.lineTo(ts * 0.87, ts * 0.50);
    g.lineTo(-ts * 0.87, ts * 0.50);
    g.closePath();
    g.fill();
    g.strokeStyle = '#111';
    g.lineWidth = 1;
    g.stroke();
    g.fillStyle = '#111';
    g.font = `900 ${ts * 0.85}px ${SANS}`;
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText('!', 0, ts * 0.12);
    g.restore();

    /* sitio web */
    g.fillStyle = '#3a6a8a';
    g.font = `400 ${h * 0.016}px ${SANS}`;
    g.textAlign = 'left';
    g.textBaseline = 'bottom';
    g.fillText('www.ika.com', w * 0.07, h * 0.808);

    /* degradado oscuro inferior 81–100% (se funde con la sección de acoplamiento) */
    const grad = g.createLinearGradient(0, h * 0.810, 0, h);
    grad.addColorStop(0, 'rgba(13,26,40,0)');
    grad.addColorStop(0.35, 'rgba(13,26,40,0.90)');
    grad.addColorStop(1, 'rgba(13,26,40,1)');
    g.fillStyle = grad;
    g.fillRect(0, h * 0.810, w, h * 0.190);
  });
  keep(panelTex);

  const panelMat = keep(new THREE.MeshPhysicalMaterial({
    map: panelTex, emissive: 0xffffff, emissiveMap: panelTex, emissiveIntensity: 0,
    roughness: 0.30, clearcoat: 0.80, clearcoatRoughness: 0.10,
    polygonOffset: true, polygonOffsetFactor: -2,
  }));
  const panelGeo = keep(new THREE.PlaneGeometry(HW - 12, HH - 14));
  const panel = new THREE.Mesh(panelGeo, panelMat);
  panel.position.set(0, HH / 2, HD / 2 + BEVEL + 0.4);
  housing.add(panel);

  /* ── PERILLA 3D
     canvas y=0.61 → housing y = 264 − 0.61×257 ≈ 107mm  ── */
  const KNOB_Y = 107;
  const knobGroup = new THREE.Group();
  knobGroup.position.set(0, KNOB_Y, HD / 2 + BEVEL + 10);
  housing.add(knobGroup);

  const knobRing = new THREE.Mesh(
    keep(new THREE.CylinderGeometry(16, 17, 7, 48)),
    keep(new THREE.MeshPhysicalMaterial({ color: 0x1a2530, roughness: 0.5, metalness: 0.3, clearcoat: 0.5 })),
  );
  knobRing.rotation.x = Math.PI / 2;
  knobGroup.add(knobRing);

  const knobCap = new THREE.Mesh(
    keep(new THREE.CylinderGeometry(13, 14, 10, 48)),
    keep(new THREE.MeshPhysicalMaterial({ color: 0x0f1518, roughness: 0.30, metalness: 0.45, clearcoat: 1, clearcoatRoughness: 0.08 })),
  );
  knobCap.rotation.x = Math.PI / 2;
  knobCap.position.z = 7;
  knobGroup.add(knobCap);

  const tick = new THREE.Mesh(
    keep(new THREE.BoxGeometry(2, 10, 1.5)),
    keep(new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.3 })),
  );
  tick.position.set(0, 12, 11);
  knobGroup.add(tick);

  /* ── interruptor físico (canvas y≈0.786 → housing y ≈ 62mm) ── */
  {
    const SW_Y = 62;
    const swFrame = new THREE.Mesh(
      keep(new THREE.BoxGeometry(22, 14, 4)),
      keep(new THREE.MeshPhysicalMaterial({ color: 0x0d1a28, roughness: 0.4, clearcoat: 0.6 })),
    );
    swFrame.position.set(0, SW_Y, HD / 2 + BEVEL + 2);
    housing.add(swFrame);
    const swRocker = new THREE.Mesh(keep(new THREE.BoxGeometry(14, 8, 5)), black);
    swRocker.position.set(0, SW_Y + 0.5, HD / 2 + BEVEL + 4);
    swRocker.rotation.x = 0.10;
    housing.add(swRocker);
  }

  /* ── tornillo de fijación lateral (visible en vista lateral) ── */
  {
    const SEAT_Y = HH * 0.46;
    const seat = new THREE.Mesh(keep(new THREE.BoxGeometry(18, 32, 14)), ikaDark);
    seat.position.set(HW / 2 + 6, SEAT_Y, 0);
    housing.add(seat);
    const screw = new THREE.Mesh(
      keep(new THREE.CylinderGeometry(9, 10, 16, 24)),
      keep(new THREE.MeshPhysicalMaterial({ color: 0x888a8c, metalness: 0.7, roughness: 0.45 })),
    );
    screw.rotation.z = Math.PI / 2;
    screw.position.set(HW / 2 + 18, SEAT_Y, 0);
    housing.add(screw);
    const grooveMat = keep(new THREE.MeshStandardMaterial({ color: 0x555860, roughness: 0.7 }));
    for (let i = 0; i < 8; i++) {
      const a = (i / 8) * Math.PI * 2;
      const groove = new THREE.Mesh(keep(new THREE.BoxGeometry(16, 1.5, 1.5)), grooveMat);
      groove.rotation.z = Math.PI / 2;
      groove.position.set(HW / 2 + 18, SEAT_Y + Math.cos(a) * 9.5, Math.sin(a) * 9.5);
      housing.add(groove);
    }
  }

  /* ══════════════════════════════════════════════════════════════
     BOOM + CABEZAL DISPERSOR
     toolGroup arranca en y = −NOSE_L (debajo de la nariz).
  ══════════════════════════════════════════════════════════════ */
  const toolGroup = new THREE.Group();
  toolGroup.position.y = -NOSE_L;
  housing.add(toolGroup);

  const boom = new THREE.Mesh(keep(new THREE.CylinderGeometry(BOOM_R, BOOM_R, BOOM_L, 36)), brushed);
  boom.position.y = -BOOM_L / 2;
  boom.castShadow = true;
  toolGroup.add(boom);

  const coupler = new THREE.Mesh(keep(lathe([
    [BOOM_R, 0], [BOOM_R + 1.5, 0], [BOOM_R + 2, -3],
    [BOOM_R + 2.5, -6], [BOOM_R + 2, -9], [BOOM_R, -11],
  ])), brushed);
  coupler.castShadow = true;
  toolGroup.add(coupler);

  const headGroup = new THREE.Group();
  headGroup.position.y = -BOOM_L;
  toolGroup.add(headGroup);

  const stator = new THREE.Mesh(keep(lathe([
    [BOOM_R, 0], [BOOM_R + 1, -2], [HEAD_R - 1, -7],
    [HEAD_R, -10], [HEAD_R, -HEAD_L + 8], [HEAD_R - 1, -HEAD_L + 4],
    [HEAD_R - 2, -HEAD_L], [0, -HEAD_L],
  ])), brushed);
  stator.castShadow = true;
  headGroup.add(stator);

  const slotMat = keep(new THREE.MeshStandardMaterial({ color: 0x28303a, roughness: 0.75 }));
  for (const ys of [-13, -22, -31]) {
    const sl = new THREE.Mesh(keep(new THREE.TorusGeometry(HEAD_R - 0.5, 2, 8, 44)), slotMat);
    sl.rotation.x = Math.PI / 2;
    sl.position.y = ys;
    headGroup.add(sl);
  }

  const rotor = new THREE.Mesh(keep(lathe([
    [0, 0], [HEAD_R - 3, 0], [HEAD_R - 2, -4],
    [HEAD_R - 3, -8], [2, -10], [2, -14], [0, -14],
  ])), keep(new THREE.MeshPhysicalMaterial({ color: 0xb2b6ba, metalness: 1, roughness: 0.26 })));
  rotor.position.y = -HEAD_L;
  headGroup.add(rotor);

  /* ── posición vertical ── */
  /* tip en world y = housingBottom + (−NOSE_L) + (−BOOM_L) + (−HEAD_L) + (−14) */
  const TIP_Y = 30;
  const housingBottom = TIP_Y + NOSE_L + BOOM_L + HEAD_L + 14; // 277
  housing.position.y = housingBottom;

  /* ══════════════════════════════════════════════════════════════
     SOPORTE DE LABORATORIO
  ══════════════════════════════════════════════════════════════ */
  const ROD_X = HW / 2 + 105;
  const ROD_H = 720;

  const rod = new THREE.Mesh(keep(new THREE.CylinderGeometry(6, 6, ROD_H, 32)), steel);
  rod.position.set(ROD_X, ROD_H / 2, 0);
  rod.castShadow = true;
  root.add(rod);

  // La placa base se extiende bajo el vaso: el vaso apoya en ella, no en el borde.
  const PLATE_T = 12;
  const PLATE_X0 = -95;
  const PLATE_X1 = ROD_X + 45;
  const PLATE_W = PLATE_X1 - PLATE_X0;
  const PLATE_CX = (PLATE_X0 + PLATE_X1) / 2;
  const standBase = new THREE.Mesh(keep(roundedBox(PLATE_W, PLATE_T, 170, 4)), alu);
  standBase.position.set(PLATE_CX, PLATE_T / 2, 0);
  standBase.castShadow = true;
  standBase.receiveShadow = true;
  root.add(standBase);

  const footGeo = keep(new THREE.CylinderGeometry(6, 8, 7, 20));
  for (const [dx, dz] of [[-1, 72], [-1, -72], [1, 72], [1, -72]] as const) {
    const foot = new THREE.Mesh(footGeo, rubber);
    foot.position.set(PLATE_CX + dx * (PLATE_W / 2 - 16), 3.5, dz);
    root.add(foot);
  }

  const CLAMP_Y = housingBottom + HH * 0.46;
  const armLen = ROD_X - HW / 2;
  const arm = new THREE.Mesh(keep(new THREE.CylinderGeometry(4, 4, armLen, 20)), steel);
  arm.rotation.z = Math.PI / 2;
  arm.position.set(HW / 2 + armLen / 2, CLAMP_Y, 0);
  root.add(arm);

  const rodCollar = new THREE.Mesh(
    keep(new THREE.CylinderGeometry(12, 12, 34, 24)),
    keep(new THREE.MeshPhysicalMaterial({ color: 0x1a1d22, roughness: 0.5, clearcoat: 0.3 })),
  );
  rodCollar.position.set(ROD_X, CLAMP_Y, 0);
  root.add(rodCollar);

  /* ══════════════════════════════════════════════════════════════
     VASO DE PRECIPITADOS (400 ml)
  ══════════════════════════════════════════════════════════════ */
  const BEAKER_R0 = 50, BEAKER_R1 = 56, BEAKER_H = 130, LIQ_H = 92;

  const beaker = new THREE.Mesh(keep(lathe([
    [0, 0], [BEAKER_R0, 0], [BEAKER_R0 + 1.2, 2],
    [BEAKER_R1, BEAKER_H - 12], [BEAKER_R1 + 2, BEAKER_H - 5],
    [BEAKER_R1 + 4, BEAKER_H], [BEAKER_R1 + 1.5, BEAKER_H],
    [BEAKER_R1, BEAKER_H - 4], [BEAKER_R0 + 0.8, BEAKER_H * 0.14],
    [BEAKER_R0 - 1, 2], [0, 2],
  ])), labGlass());
  beaker.renderOrder = 3;
  beaker.position.y = PLATE_T;
  root.add(beaker);

  const liquid = new THREE.Mesh(keep(lathe([
    [0, 2], [BEAKER_R0 - 1.5, 2], [BEAKER_R0 - 0.5, 6],
    [BEAKER_R1 - 3, LIQ_H - 4], [0, LIQ_H - 4],
  ])), keep(new THREE.MeshPhysicalMaterial({
    color: 0x0a7b60, roughness: 0.22, clearcoat: 0.5,
    emissive: 0x054838, emissiveIntensity: 0.12,
    transparent: true, opacity: 0.85, depthWrite: false, side: THREE.DoubleSide,
  })));
  liquid.renderOrder = 1;
  liquid.position.y = PLATE_T;
  root.add(liquid);

  const surfGeo = keep(new THREE.CircleGeometry(BEAKER_R1 - 5, 96));
  surfGeo.rotateX(-Math.PI / 2);
  const surfBase = Float32Array.from(surfGeo.attributes.position.array as ArrayLike<number>);
  const surface = new THREE.Mesh(surfGeo, keep(new THREE.MeshPhysicalMaterial({
    color: 0x10a07a, roughness: 0.05, clearcoat: 1,
    emissive: 0x085c45, emissiveIntensity: 0.16,
    transparent: true, opacity: 0.92,
  })));
  surface.position.y = PLATE_T + LIQ_H;
  surface.renderOrder = 2;
  root.add(surface);

  /* ── segunda fase: gotas con vórtice centrífugo ── */
  const DROPS = 420;
  const droplets = new THREE.InstancedMesh(
    keep(new THREE.SphereGeometry(0.85, 7, 5)),
    keep(new THREE.MeshPhysicalMaterial({
      color: 0xf0c030, roughness: 0.10, clearcoat: 1,
      transparent: true, opacity: 0.90,
      emissive: 0xc09000, emissiveIntensity: 0.20, depthWrite: false,
    })),
    DROPS,
  );
  droplets.renderOrder = 2;
  root.add(droplets);

  const dummyObj = new THREE.Object3D();
  dummyObj.scale.setScalar(0);
  dummyObj.updateMatrix();
  for (let i = 0; i < DROPS; i++) droplets.setMatrixAt(i, dummyObj.matrix);

  type DS = { x: number; y: number; z: number; vx: number; vy: number; vz: number; s: number; life: number; max: number };
  const dropState: DS[] = Array.from({ length: DROPS }, () => ({
    x: 0, y: 0, z: 0, vx: 0, vy: 0, vz: 0, s: 0, life: 0, max: 1,
  }));

  const spawnDrop = (b: DS) => {
    const a = Math.random() * Math.PI * 2;
    const r = Math.random() * HEAD_R * 0.8;
    b.x = Math.cos(a) * r;
    b.z = Math.sin(a) * r;
    b.y = TIP_Y + 2 + Math.random() * HEAD_L * 0.7;
    const out = 0.6 + Math.random() * 1.8;
    b.vx = Math.cos(a) * out - Math.sin(a) * 0.6;   // radial + tangencial
    b.vz = Math.sin(a) * out + Math.cos(a) * 0.6;
    b.vy = -0.2 + Math.random() * 1.0;
    b.s = 0.35 + Math.random() * 0.75;
    b.life = 0;
    b.max = 0.6 + Math.random() * 2.8;
  };

  const glowTex = canvasTexture(128, 128, (g) => {
    const rg = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    rg.addColorStop(0, 'rgba(16,200,140,.9)');
    rg.addColorStop(0.4, 'rgba(16,200,140,.25)');
    rg.addColorStop(1, 'rgba(16,200,140,0)');
    g.fillStyle = rg;
    g.fillRect(0, 0, 128, 128);
  });
  keep(glowTex.texture);
  const glowMat = keep(new THREE.SpriteMaterial({
    map: glowTex.texture, blending: THREE.AdditiveBlending,
    depthWrite: false, transparent: true, opacity: 0,
  }));
  const glow = new THREE.Sprite(glowMat);
  glow.scale.set(52, 38, 1);
  glow.position.set(0, TIP_Y + 8, 0);
  root.add(glow);

  /* ══════════════════════════════════════════════════════════════
     ANCHORS
     display: canvas y=0.40 → housing y = 264 − 0.40×257 ≈ 161mm
     knob:    canvas y=0.61 → housing y = 264 − 0.61×257 ≈ 107mm
     Separación en world: 54mm — visibles como puntos distintos.
  ══════════════════════════════════════════════════════════════ */
  const displayAnchor = new THREE.Object3D();
  displayAnchor.position.set(-HW * 0.08, 161, HD / 2 + BEVEL + 1);
  housing.add(displayAnchor);

  const knobAnchor = new THREE.Object3D();
  knobAnchor.position.set(0, KNOB_Y, HD / 2 + BEVEL + 1);
  housing.add(knobAnchor);

  const statorAnchor = new THREE.Object3D();
  statorAnchor.position.set(0, -BOOM_L - HEAD_L * 0.45, 0);
  toolGroup.add(statorAnchor);

  const mountAnchor = new THREE.Object3D();
  mountAnchor.position.set(HW / 2 + 8, HH * 0.46, 0);
  housing.add(mountAnchor);

  /* ── vistas ── */
  const panelTarget = new THREE.Object3D();
  panelTarget.position.set(0, housingBottom + 161, 0);
  root.add(panelTarget);

  const herramientaTarget = new THREE.Object3D();
  herramientaTarget.position.set(0, TIP_Y + HEAD_L * 0.5, 0);
  root.add(herramientaTarget);

  const lateralTarget = new THREE.Object3D();
  lateralTarget.position.set(0, housingBottom + HH * 0.50, 0);
  root.add(lateralTarget);

  /* ══════════════════════════════════════════════════════════════
     ESTADO Y ANIMACIÓN
  ══════════════════════════════════════════════════════════════ */
  let powered = false;
  let lastRedraw = 0;

  return {
    root,
    anchors: { display: displayAnchor, knob: knobAnchor, herramienta: statorAnchor, mount: mountAnchor },
    views: {
      panel:       { target: panelTarget,       direction: new THREE.Vector3(0.5, 0.04, 0.87),  distance: 230 },
      herramienta: { target: herramientaTarget,  direction: new THREE.Vector3(0.4, 0.15, 1),    distance: 195 },
      lateral:     { target: lateralTarget,      direction: new THREE.Vector3(1, 0.18, 0.12),   distance: 300 },
    },
    frameTop: housingBottom + HH + 50,
    yaw: 0.52,
    setPower(on: boolean) {
      powered = on;
      if (on) displayOn = true;
    },
    update(dt: number, t: number, level: number) {
      /* pantalla LED */
      if (displayOn && t - lastRedraw > 1 / 15) {
        lastRedraw = t;
        const target = powered ? 13800 + 500 * Math.sin(t * 0.65) : 0;
        displayRpm += (target - displayRpm) * 0.06;
        if (!powered && level < 0.02) displayOn = false;
        panelRedraw();
      }
      panelMat.emissiveIntensity = 0.52 * level;

      /* rotor + vibración */
      rotor.rotation.y = t * 110 * level;
      headGroup.position.x = (Math.random() - 0.5) * 0.28 * level;
      headGroup.position.z = (Math.random() - 0.5) * 0.28 * level;

      /* halo */
      glowMat.opacity = 0.68 * level * (0.82 + Math.random() * 0.18);

      /* gotas con vórtice */
      for (let i = 0; i < DROPS; i++) {
        const b = dropState[i];
        if (b.life >= b.max || b.s === 0) {
          if (level > 0.18 && Math.random() < 0.30 * level) {
            spawnDrop(b);
          } else {
            dummyObj.scale.setScalar(0);
            dummyObj.updateMatrix();
            droplets.setMatrixAt(i, dummyObj.matrix);
            continue;
          }
        }
        b.life += dt;
        b.x += b.vx * dt * 18;
        b.z += b.vz * dt * 18;
        b.y += b.vy * dt * 14;
        /* rotación del vórtice */
        const ang = Math.atan2(b.z, b.x);
        const rad = Math.hypot(b.x, b.z);
        const va = 1.4 * level * dt;
        b.x = Math.cos(ang + va) * rad;
        b.z = Math.sin(ang + va) * rad;
        /* contención */
        const maxR = BEAKER_R0 - 3 - Math.max(0, b.y - PLATE_T - LIQ_H * 0.35) * 0.12;
        const radNow = Math.hypot(b.x, b.z);
        if (radNow > maxR) { b.vx *= -0.45; b.vz *= -0.45; b.x *= maxR / radNow; b.z *= maxR / radNow; }
        if (b.y > PLATE_T + LIQ_H - 2) { b.vy *= -0.3; b.y = PLATE_T + LIQ_H - 2; }
        if (b.y < PLATE_T + 3) { b.vy = Math.abs(b.vy); b.y = PLATE_T + 3; }
        const k = Math.min(1, b.life * 5) * (1 - b.life / b.max) * level;
        dummyObj.position.set(b.x, b.y, b.z);
        dummyObj.scale.setScalar(b.s * (0.4 + k));
        dummyObj.updateMatrix();
        droplets.setMatrixAt(i, dummyObj.matrix);
      }
      droplets.instanceMatrix.needsUpdate = true;

      /* vórtice en la superficie */
      const p = surfGeo.attributes.position;
      if (level > 0.001) {
        for (let i = 0; i < p.count; i++) {
          const x = surfBase[i * 3], z = surfBase[i * 3 + 2];
          const r = Math.hypot(x, z);
          const vortex = -level * 3.0 * Math.exp(-r * 0.04) * Math.sin(Math.atan2(z, x) * 4 - t * 14);
          p.setY(i, level * (Math.sin(r * 0.48 - t * 15) * 0.9 * Math.exp(-r * 0.038) + vortex));
        }
        p.needsUpdate = true;
        surfGeo.computeVertexNormals();
      }
    },
    dispose,
  };
}
