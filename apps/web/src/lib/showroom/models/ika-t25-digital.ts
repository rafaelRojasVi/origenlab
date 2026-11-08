/**
 * IKA T 25 digital ULTRA-TURRAX — dispersor de laboratorio, sobre su soporte.
 *
 * Construido desde la fotografía verificada del equipo
 * (`public/products/ika/t-25-digital.png`, recorte del folleto oficial de
 * dispersores de IKA) y sus medidas publicadas:
 *
 *   Accionamiento:  87 × 271 × 106 mm (ancho × alto × fondo), 2,5 kg
 *                   — hoja de datos IKA «T 25 digital ULTRA-TURRAX»
 *   Elemento dispersor S 25 N-18 G: eje 194 mm, estátor Ø 18 mm,
 *                   rotor Ø 12,7 mm — hoja del elemento (IKA 0593400)
 *   Revoluciones:   3.000 a 25.000 rpm (ficha con fuente); la pantalla LED
 *                   las muestra en miles («13.8» = 13.800 rpm), como en la foto.
 *
 * Proporciones leídas en la foto (0,68 mm por píxel, escalada por los 271 mm
 * de alto y comprobada con el estátor de 18 mm):
 *   caja de mando arriba (≈ 107 mm de alto) con su panel gris azulado, rótulo
 *   blanco, pantalla LED roja, dial grande y conmutador basculante; detrás y
 *   debajo, el motor cilíndrico azul (≈ Ø 74 mm, estimado) que se estrecha en un
 *   anillo gris y entra en el collarín gris oscuro facetado (≈ 50 mm de alto,
 *   Ø 58 → 48 mm, estimado) con la perilla estriada de fijación del elemento
 *   dispersor (≈ Ø 30 mm, estimada) a un costado. Del collarín sale el tubo de
 *   acero del elemento: ≈ 176 mm a la vista hasta el estátor dentado, con el
 *   agujero de purga cerca de la punta.
 *
 * El motor se sujeta a la varilla con un brazo horizontal y una nuez; el soporte
 * y el vaso son los de siempre.
 *
 * Todo en milímetros; la escena lo reduce a escala. La pantalla sigue el nivel
 * de marcha, o `root.userData.rpmFraction` (0 → 3.000 rpm … 1 → 25.000 rpm) si
 * la historia la fija.
 */
import * as THREE from 'three';
import type { BuildOptions, BuiltModel } from '../types';
import { SANS, canvasTexture, lathe, labGlass, disposer, roundedBox } from './kit';

/* ── accionamiento (origen: base del collarín, y hacia arriba) ── */
const UNIT_H = 271;           // alto oficial
const BOX_W = 68;             // caja de mando (estimado en la foto)
const BOX_H = 107;
const BOX_D = 76;
const BOX_Z = 33;             // centro de la caja: frente en z = 71, motor detrás
const BOX_FRONT = BOX_Z + BOX_D / 2;
const MOTOR_R = 35;           // fondo: 35 detrás del eje + 71 delante = 106 mm (oficial)
const MOTOR_TOP = UNIT_H - 18;
const COLLAR_H = 50;
const RING_H = 7;

/* ── elemento dispersor S 25 N-18 G ── */
const TUBE_R = 9;             // Ø 18 mm
const ROTOR_R = 6.35;         // Ø 12,7 mm
const SHAFT_SHOWN = 176;      // del collarín a la punta (de 194 mm de eje)
const TEETH_H = 8;

/* ── rpm (ficha: 3.000 a 25.000 rpm) ── */
const RPM_MIN = 3000;
const RPM_MAX = 25000;
const RPM_IDLE = 13800;       // la cifra de la fotografía, a plena marcha libre

export function build(options: BuildOptions = {}): BuiltModel {
  const { keep, dispose } = disposer();
  const root = new THREE.Group();
  root.userData.rpmFraction = null;

  /* ── materiales ── */
  const ikaBlue = keep(new THREE.MeshPhysicalMaterial({
    color: 0x2559a3, roughness: 0.34, metalness: 0.02,
    clearcoat: 0.7, clearcoatRoughness: 0.25,
  }));
  const boxBlue = keep(new THREE.MeshPhysicalMaterial({
    color: 0x5778ba, roughness: 0.42, clearcoat: 0.5, clearcoatRoughness: 0.3,
  }));
  const darkGrey = keep(new THREE.MeshPhysicalMaterial({
    color: 0x4a4b4d, roughness: 0.55, clearcoat: 0.25, clearcoatRoughness: 0.5, flatShading: true,
  }));
  const ringGrey = keep(new THREE.MeshPhysicalMaterial({ color: 0x4f5764, roughness: 0.5, clearcoat: 0.3 }));
  const knobGrey = keep(new THREE.MeshPhysicalMaterial({ color: 0x48494b, roughness: 0.5, clearcoat: 0.3 }));
  const dialGrey = keep(new THREE.MeshPhysicalMaterial({
    color: 0x55565a, roughness: 0.45, clearcoat: 0.6, clearcoatRoughness: 0.2,
  }));
  const steel = keep(new THREE.MeshPhysicalMaterial({ color: 0xd4d8dc, metalness: 1, roughness: 0.22 }));
  const steelBack = keep(new THREE.MeshPhysicalMaterial({ color: 0xb8bcc0, metalness: 1, roughness: 0.3, side: THREE.BackSide }));
  const polished = keep(new THREE.MeshPhysicalMaterial({ color: 0xe2e5e8, metalness: 1, roughness: 0.12 }));
  const black = keep(new THREE.MeshStandardMaterial({ color: 0x141518, roughness: 0.6 }));
  const hole = keep(new THREE.MeshStandardMaterial({ color: 0x0b0c0e, roughness: 0.9 }));
  const alu = keep(new THREE.MeshPhysicalMaterial({ color: 0xdfe2e4, metalness: 0.9, roughness: 0.34 }));
  const rubber = keep(new THREE.MeshStandardMaterial({ color: 0x101113, roughness: 0.9 }));
  const white = keep(new THREE.MeshStandardMaterial({ color: 0xf2f2f2, roughness: 0.4 }));

  /* ══════════════════════════════════════════════════════════════
     ACCIONAMIENTO. X = ancho, Y = alto (base del collarín en 0),
     Z = fondo (el panel mira a +Z).
  ══════════════════════════════════════════════════════════════ */
  const unit = new THREE.Group();
  root.add(unit);

  /* ── motor: cilindro azul con la tapa redondeada; abajo se recoge hacia el anillo ── */
  const motorBottom = COLLAR_H + RING_H;
  {
    const motor = new THREE.Mesh(keep(lathe([
      [0, MOTOR_TOP], [MOTOR_R - 8, MOTOR_TOP], [MOTOR_R - 3, MOTOR_TOP - 1.2],
      [MOTOR_R - 0.6, MOTOR_TOP - 4.5], [MOTOR_R, MOTOR_TOP - 9],
      [MOTOR_R, motorBottom + 12], [MOTOR_R - 1, motorBottom + 6],
      [MOTOR_R - 3.5, motorBottom + 2], [MOTOR_R - 6, motorBottom],
    ], 72)), ikaBlue);
    motor.castShadow = true;
    unit.add(motor);
  }

  /* ── anillo gris de transición y collarín facetado ── */
  {
    const ring = new THREE.Mesh(keep(lathe([
      [MOTOR_R - 6, motorBottom + 0.01], [MOTOR_R - 7, motorBottom - 3],
      [30, COLLAR_H + 1], [29, COLLAR_H],
    ], 72)), ringGrey);
    unit.add(ring);

    // 14 caras verticales, como el collarín de la foto (sombreado plano).
    const collar = new THREE.Mesh(keep(lathe([
      [0, COLLAR_H], [29, COLLAR_H], [29, COLLAR_H - 3],
      [25, 6], [24, 3], [21, 0], [12, -1], [11, -4], [0, -4],
    ], 14)), darkGrey);
    collar.castShadow = true;
    unit.add(collar);

    // Las dos marcas blancas de la cara superior.
    const markGeo = keep(new THREE.BoxGeometry(1.2, 2.6, 0.8));
    for (const dx of [-1.6, 1.6]) {
      const mark = new THREE.Mesh(markGeo, white);
      const a = 0.18;
      mark.position.set(Math.sin(a) * 28.6 + dx * Math.cos(a), COLLAR_H - 6, Math.cos(a) * 28.6 - dx * Math.sin(a) - 0.6);
      mark.rotation.y = a;
      mark.rotation.x = -0.08;
      unit.add(mark);
    }
  }

  /* ── perilla estriada de fijación del elemento dispersor ── */
  const KNOB_Y = 21;
  const KNOB_A = 0.42; // del costado derecho hacia el frente
  const clampKnob = new THREE.Group();
  {
    const geo = keep(lathe([
      [0, 0], [11, 0], [11, 5], [14.5, 6], [16.5, 8.5],
      [16.5, 24], [15.5, 26.5], [12, 28], [0, 28.5],
    ], 72));
    // Estrías: 16 canales a lo largo del cuerpo de la perilla.
    const p = geo.attributes.position;
    for (let i = 0; i < p.count; i++) {
      const x = p.getX(i), y = p.getY(i), z = p.getZ(i);
      const r = Math.hypot(x, z);
      if (y < 7.5 || r < 9) continue;
      const k = 1 - 0.075 * Math.pow(0.5 + 0.5 * Math.cos(Math.atan2(z, x) * 16), 3);
      p.setX(i, x * k);
      p.setZ(i, z * k);
    }
    geo.computeVertexNormals();
    const knob = new THREE.Mesh(geo, knobGrey);
    knob.rotation.z = -Math.PI / 2; // el eje local Y apunta a +X
    knob.castShadow = true;
    clampKnob.add(knob);
    clampKnob.position.set(Math.cos(KNOB_A) * 22, KNOB_Y, Math.sin(KNOB_A) * 22);
    clampKnob.rotation.y = -KNOB_A;
    unit.add(clampKnob);
  }

  /* ── caja de mando ── */
  const BOX_Y0 = UNIT_H - BOX_H;
  {
    const box = new THREE.Mesh(keep(roundedBox(BOX_W, BOX_H, BOX_D, 5)), boxBlue);
    box.position.set(0, BOX_Y0 + BOX_H / 2, BOX_Z);
    box.castShadow = true;
    unit.add(box);
  }

  /* ══════════════════════════════════════════════════════════════
     PANEL EN CANVAS (frente de la caja)
     Como en la foto: franja blanca «IKA® T25 digital» arriba, pantalla
     LED roja debajo a la izquierda con «min⁻¹ x 1000», «ULTRA-TURRAX®»,
     el dial con su arco azul, el triángulo de aviso y el conmutador.
  ══════════════════════════════════════════════════════════════ */
  const PANEL_W = BOX_W - 6;
  const PANEL_H = BOX_H - 7;
  const PW = 512;
  const PH = Math.round((PW * PANEL_H) / PANEL_W);
  /** Panel (fracciones del lienzo) → coordenadas en la unidad. */
  const panelY = (f: number) => BOX_Y0 + 3 + PANEL_H * (1 - f);
  const panelX = (f: number) => (f - 0.5) * PANEL_W;

  let displayOn = false;
  let displayRpm = 0;

  // Siete segmentos: a b c d e f g.
  const DIGITS: Record<string, string> = {
    '0': 'abcdef', '1': 'bc', '2': 'abdeg', '3': 'abcdg', '4': 'bcfg',
    '5': 'acdfg', '6': 'acdefg', '7': 'abc', '8': 'abcdefg', '9': 'abcdfg', ' ': '',
  };
  const poly = (g: CanvasRenderingContext2D, pts: [number, number][]) => {
    g.beginPath();
    pts.forEach(([a, b], i) => (i ? g.lineTo(a, b) : g.moveTo(a, b)));
    g.closePath();
    g.fill();
  };
  const segment = (g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, t: number, s: string) => {
    const k = 0.12 * w; // inclinación cursiva
    const sk = (px: number, py: number): [number, number] => [px + k * (1 - (py - y) / h), py];
    const hSeg = (yy: number) =>
      poly(g, [sk(x + t, yy), sk(x + t * 1.5, yy - t / 2), sk(x + w - t * 1.5, yy - t / 2), sk(x + w - t, yy), sk(x + w - t * 1.5, yy + t / 2), sk(x + t * 1.5, yy + t / 2)]);
    const vSeg = (xx: number, y0: number, y1: number) =>
      poly(g, [sk(xx, y0 + t * 0.6), sk(xx + t / 2, y0 + t * 1.1), sk(xx + t / 2, y1 - t * 1.1), sk(xx, y1 - t * 0.6), sk(xx - t / 2, y1 - t * 1.1), sk(xx - t / 2, y0 + t * 1.1)]);
    if (s === 'a') hSeg(y + t / 2);
    if (s === 'g') hSeg(y + h / 2);
    if (s === 'd') hSeg(y + h - t / 2);
    if (s === 'b') vSeg(x + w - t / 2, y, y + h / 2);
    if (s === 'c') vSeg(x + w - t / 2, y + h / 2, y + h);
    if (s === 'f') vSeg(x + t / 2, y, y + h / 2);
    if (s === 'e') vSeg(x + t / 2, y + h / 2, y + h);
  };

  const { texture: panelTex, redraw: panelRedraw } = canvasTexture(PW, PH, (g, w, h) => {
    /* lámina gris azulada, con un leve degradado de luz */
    const bg = g.createLinearGradient(0, 0, w, h);
    bg.addColorStop(0, '#53638f');
    bg.addColorStop(1, '#45557f');
    g.fillStyle = bg;
    g.fillRect(0, 0, w, h);

    /* franja blanca del rótulo */
    g.fillStyle = '#eef0f6';
    g.fillRect(0, h * 0.05, w, h * 0.12);
    g.textBaseline = 'middle';
    g.textAlign = 'left';
    g.fillStyle = '#1d2230';
    g.font = `800 ${h * 0.085}px ${SANS}`;
    g.fillText('IKA', w * 0.05, h * 0.112);
    const ikaW = g.measureText('IKA').width;
    g.font = `700 ${h * 0.03}px ${SANS}`;
    g.fillText('®', w * 0.05 + ikaW + 2, h * 0.085);
    g.fillStyle = '#5d6684';
    g.font = `500 ${h * 0.074}px ${SANS}`;
    g.fillText('T25 digital', w * 0.05 + ikaW + w * 0.06, h * 0.116);

    /* ventana LED */
    const lx = w * 0.1, ly = h * 0.2, lw = w * 0.46, lh = h * 0.135;
    g.fillStyle = '#140708';
    g.fillRect(lx, ly, lw, lh);
    const shown = displayOn ? (displayRpm / 1000).toFixed(1).padStart(4, ' ') : '88.8';
    const chars = shown.replace('.', '');
    const dotAfter = shown.indexOf('.') - 1;
    const dw = lw * 0.21, dh = lh * 0.72, gap = lw * 0.06, st = dw * 0.19;
    const x0 = lx + lw - 3 * dw - 2 * gap - lw * 0.07;
    g.fillStyle = displayOn ? '#ff2a18' : '#2c1214';
    if (displayOn) { g.shadowColor = '#ff4630'; g.shadowBlur = 14; }
    for (let i = 0; i < 3; i++) {
      for (const s of DIGITS[chars[i] ?? ' '] ?? '') segment(g, x0 + i * (dw + gap), ly + lh * 0.14, dw, dh, st, s);
      if (i === dotAfter) {
        g.beginPath();
        g.arc(x0 + i * (dw + gap) + dw + gap * 0.4, ly + lh * 0.14 + dh - st * 0.45, st * 0.55, 0, Math.PI * 2);
        g.fill();
      }
    }
    g.shadowBlur = 0;
    g.fillStyle = '#e8ecf6';
    g.font = `600 ${h * 0.028}px ${SANS}`;
    g.fillText('min⁻¹ x 1000', lx + lw + w * 0.03, ly + lh * 0.8);

    /* ULTRA-TURRAX® */
    g.fillStyle = '#f2f4fa';
    g.font = `700 ${h * 0.036}px ${SANS}`;
    g.fillText('ULTRA-TURRAX®', w * 0.1, h * 0.38);

    /* arco azul del dial */
    const cx = w * 0.42, cy = h * 0.56, cr = w * 0.215;
    g.strokeStyle = '#3a77d0';
    g.lineWidth = w * 0.03;
    g.lineCap = 'round';
    g.beginPath();
    g.arc(cx, cy, cr, Math.PI * 1.2, Math.PI * 2.3);
    g.stroke();
    g.strokeStyle = '#eef1f8';
    g.lineWidth = 2;
    g.beginPath();
    g.arc(cx, cy, cr + w * 0.026, Math.PI * 1.35, Math.PI * 2.2);
    g.stroke();

    /* triángulo de aviso blanco */
    g.save();
    g.translate(w * 0.24, h * 0.885);
    const ts = w * 0.05;
    g.fillStyle = '#f6f7fb';
    poly(g, [[0, -ts], [ts * 0.95, ts * 0.65], [-ts * 0.95, ts * 0.65]]);
    g.fillStyle = '#4a5878';
    g.font = `800 ${ts * 0.9}px ${SANS}`;
    g.textAlign = 'center';
    g.fillText('!', 0, ts * 0.2);
    g.restore();

    /* marco del conmutador */
    g.fillStyle = '#1a1b1f';
    g.fillRect(w * 0.6, h * 0.79, w * 0.2, h * 0.17);
  });
  keep(panelTex);

  const panelMat = keep(new THREE.MeshPhysicalMaterial({
    map: panelTex, emissive: 0xffffff, emissiveMap: panelTex, emissiveIntensity: 0,
    roughness: 0.5, clearcoat: 0.3, clearcoatRoughness: 0.3,
  }));
  const panel = new THREE.Mesh(keep(new THREE.PlaneGeometry(PANEL_W, PANEL_H)), panelMat);
  panel.position.set(0, BOX_Y0 + 3 + PANEL_H / 2, BOX_FRONT + 0.3);
  unit.add(panel);

  /* ── dial 3D (centro del arco del lienzo) ── */
  const DIAL_X = panelX(0.42);
  const DIAL_Y = panelY(0.56);
  const dial = new THREE.Group();
  dial.position.set(DIAL_X, DIAL_Y, BOX_FRONT + 0.3);
  unit.add(dial);
  {
    const body = new THREE.Mesh(keep(lathe([
      [0, 0], [11.8, 0], [12.2, 1], [12.2, 8], [11.4, 9.6], [9.8, 10.4], [0, 10.6],
    ], 64)), dialGrey);
    body.rotation.x = Math.PI / 2;
    body.castShadow = true;
    dial.add(body);
    const tick = new THREE.Mesh(keep(new THREE.BoxGeometry(7, 1.4, 0.8)), black);
    tick.position.set(-6.5, -2.2, 10.6);
    tick.rotation.z = 0.3;
    dial.add(tick);
  }

  /* ── conmutador basculante ── */
  {
    const rocker = new THREE.Mesh(keep(roundedBox(PANEL_W * 0.16, PANEL_H * 0.14, 4, 0.8)), black);
    rocker.position.set(panelX(0.7), panelY(0.875), BOX_FRONT + 1.6);
    rocker.rotation.x = 0.12;
    unit.add(rocker);
  }

  /* ══════════════════════════════════════════════════════════════
     ELEMENTO DISPERSOR S 25 N-18 G
  ══════════════════════════════════════════════════════════════ */
  const tool = new THREE.Group();
  unit.add(tool);

  const TUBE_END = -(SHAFT_SHOWN - TEETH_H);
  {
    const tube = new THREE.Mesh(keep(lathe([
      [0, -2], [TUBE_R, -2], [TUBE_R, TUBE_END + 0.6], [TUBE_R - 0.5, TUBE_END],
      [TUBE_R - 1.4, TUBE_END], [TUBE_R - 1.4, TUBE_END + 3],
    ], 48)), steel);
    tube.castShadow = true;
    tool.add(tube);

    // Agujero de purga, cerca de la punta (en la foto, unos 17 mm sobre ella).
    const vent = new THREE.Mesh(keep(new THREE.CircleGeometry(1.9, 20)), hole);
    vent.position.set(0, -SHAFT_SHOWN + 17, TUBE_R + 0.05);
    tool.add(vent);
  }

  /* estátor dentado: corona de dientes con ventanas entre ellos */
  const head = new THREE.Group();
  head.position.y = TUBE_END;
  tool.add(head);
  {
    const TEETH = 10;
    const arc = ((Math.PI * 2) / TEETH) * 0.55;
    const toothGeo = keep(new THREE.CylinderGeometry(TUBE_R - 0.3, TUBE_R - 0.6, TEETH_H, 4, 1, true, 0, arc));
    toothGeo.translate(0, -TEETH_H / 2, 0);
    const innerGeo = keep(new THREE.CylinderGeometry(TUBE_R - 1.5, TUBE_R - 1.6, TEETH_H, 4, 1, true, 0, arc));
    innerGeo.translate(0, -TEETH_H / 2, 0);
    for (let i = 0; i < TEETH; i++) {
      const tooth = new THREE.Mesh(toothGeo, steel);
      tooth.rotation.y = (i / TEETH) * Math.PI * 2;
      head.add(tooth);
      const inner = new THREE.Mesh(innerGeo, steelBack);
      inner.rotation.y = tooth.rotation.y;
      head.add(inner);
    }
  }

  /* rotor (gira dentro del estátor) y su eje */
  const rotor = new THREE.Group();
  head.add(rotor);
  {
    const shaft = new THREE.Mesh(keep(new THREE.CylinderGeometry(2.4, 2.4, 20, 16)), polished);
    shaft.position.y = 6;
    rotor.add(shaft);
    const bladeGeo = keep(new THREE.BoxGeometry(ROTOR_R * 2, TEETH_H - 1.2, 1.6));
    for (let i = 0; i < 2; i++) {
      const blade = new THREE.Mesh(bladeGeo, polished);
      blade.position.y = -TEETH_H / 2 - 0.2;
      blade.rotation.y = (i * Math.PI) / 2;
      rotor.add(blade);
    }
  }

  /* ── posición: la punta queda dentro del líquido ── */
  const TIP_Y = 30;
  const unitBase = TIP_Y + SHAFT_SHOWN; // base del collarín
  unit.position.y = unitBase;
  const unitTop = unitBase + UNIT_H;

  /* ══════════════════════════════════════════════════════════════
     SOPORTE DE LABORATORIO
  ══════════════════════════════════════════════════════════════ */
  const ROD_X = 150;
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

  /* brazo del motor a la varilla, con su nuez */
  const ARM_Y = 140; // en la unidad: a media altura del motor, bajo la caja
  const CLAMP_Y = unitBase + ARM_Y;
  {
    const armX0 = MOTOR_R - 4;
    const armLen = ROD_X - armX0;
    const arm = new THREE.Mesh(keep(new THREE.CylinderGeometry(6.5, 6.5, armLen, 24)), steel);
    arm.rotation.z = Math.PI / 2;
    arm.position.set(armX0 + armLen / 2, CLAMP_Y, 0);
    arm.castShadow = true;
    root.add(arm);
    const boss = new THREE.Mesh(keep(new THREE.CylinderGeometry(10, 10, 8, 32)), ringGrey);
    boss.rotation.z = Math.PI / 2;
    boss.position.set(MOTOR_R + 1, CLAMP_Y, 0);
    root.add(boss);
    const nut = new THREE.Mesh(keep(roundedBox(26, 30, 26, 3)), black);
    nut.position.set(ROD_X, CLAMP_Y, 0);
    root.add(nut);
    const screw = new THREE.Mesh(keep(new THREE.CylinderGeometry(7, 7, 12, 20)), black);
    screw.rotation.x = Math.PI / 2;
    screw.position.set(ROD_X, CLAMP_Y, 18);
    root.add(screw);
  }

  /* ══════════════════════════════════════════════════════════════
     VASO DE PRECIPITADOS
  ══════════════════════════════════════════════════════════════ */
  const BEAKER_R0 = 50, BEAKER_R1 = 56, BEAKER_H = 130, LIQ_H = 92;

  const beaker = new THREE.Mesh(keep(lathe([
    [0, 0], [BEAKER_R0, 0], [BEAKER_R0 + 1.2, 2],
    [BEAKER_R1, BEAKER_H - 12], [BEAKER_R1 + 2, BEAKER_H - 5],
    [BEAKER_R1 + 4, BEAKER_H], [BEAKER_R1 + 1.5, BEAKER_H],
    [BEAKER_R1, BEAKER_H - 4], [BEAKER_R0 + 0.8, BEAKER_H * 0.14],
    [BEAKER_R0 - 1, 2], [0, 2],
  ])), keep(labGlass()));
  beaker.renderOrder = 3;
  beaker.position.y = PLATE_T;
  root.add(beaker);

  // Sin tapa: la superficie (que se hunde en vórtice) cierra el líquido.
  const surfR = BEAKER_R0 - 1 + (BEAKER_R1 - BEAKER_R0) * ((LIQ_H - 6) / (BEAKER_H - 12)) - 1.5;
  const liquid = new THREE.Mesh(keep(lathe([
    [0, 2], [BEAKER_R0 - 1.5, 2], [BEAKER_R0 - 0.5, 6], [surfR, LIQ_H],
  ])), keep(new THREE.MeshPhysicalMaterial({
    color: 0x0a7b60, roughness: 0.22, clearcoat: 0.5,
    emissive: 0x054838, emissiveIntensity: 0.12,
    transparent: true, opacity: 0.8, depthWrite: false, side: THREE.DoubleSide,
  })));
  liquid.renderOrder = 1;
  liquid.position.y = PLATE_T;
  root.add(liquid);

  const surfGeo = keep(new THREE.CircleGeometry(surfR, 96));
  surfGeo.rotateX(-Math.PI / 2);
  const surfBase = Float32Array.from(surfGeo.attributes.position.array as ArrayLike<number>);
  const surface = new THREE.Mesh(surfGeo, keep(new THREE.MeshPhysicalMaterial({
    color: 0x10a07a, roughness: 0.12, clearcoat: 1,
    emissive: 0x085c45, emissiveIntensity: 0.06,
    transparent: true, opacity: 0.95, side: THREE.DoubleSide,
  })));
  surface.position.y = PLATE_T + LIQ_H;
  surface.renderOrder = 2;
  root.add(surface);

  /* ── cizalla: gotas de la segunda fase que el cabezal expulsa y el vórtice arrastra ── */
  const DROPS = options.lite ? 160 : 420;
  const HEAD_MID = TIP_Y + TEETH_H / 2;
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

  // Sale por las ventanas del estátor: radial y con giro; sube por la pared y vuelve al eje.
  const spawnDrop = (b: DS) => {
    const a = Math.random() * Math.PI * 2;
    b.x = Math.cos(a) * TUBE_R;
    b.z = Math.sin(a) * TUBE_R;
    b.y = HEAD_MID + (Math.random() - 0.5) * TEETH_H;
    const out = 1.4 + Math.random() * 1.6;
    b.vx = Math.cos(a) * out - Math.sin(a) * 1.2;
    b.vz = Math.sin(a) * out + Math.cos(a) * 1.2;
    b.vy = -0.3 + Math.random() * 0.6;
    b.s = 0.35 + Math.random() * 0.75;
    b.life = 0;
    b.max = 0.8 + Math.random() * 2.6;
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
  // La geometría del sprite es la que three comparte entre todos; liberarla sólo vacía su copia en la GPU.
  keep(glow.geometry);
  glow.scale.set(46, 30, 1);
  glow.position.set(0, HEAD_MID, 0);
  root.add(glow);

  /* ══════════════════════════════════════════════════════════════
     ANCLAS Y VISTAS
  ══════════════════════════════════════════════════════════════ */
  const displayAnchor = new THREE.Object3D();
  displayAnchor.position.set(panelX(0.33), panelY(0.27), BOX_FRONT + 1);
  unit.add(displayAnchor);

  const knobAnchor = new THREE.Object3D();
  knobAnchor.position.set(DIAL_X, DIAL_Y, BOX_FRONT + 11);
  unit.add(knobAnchor);

  const collarAnchor = new THREE.Object3D();
  collarAnchor.position.set(Math.cos(KNOB_A) * 48, KNOB_Y, Math.sin(KNOB_A) * 48);
  unit.add(collarAnchor);

  const mountAnchor = new THREE.Object3D();
  mountAnchor.position.set(MOTOR_R + 6, ARM_Y, 4);
  unit.add(mountAnchor);

  const statorAnchor = new THREE.Object3D();
  statorAnchor.position.set(0, -TEETH_H / 2, TUBE_R);
  head.add(statorAnchor);

  const at = (y: number) => {
    const o = new THREE.Object3D();
    o.position.set(0, y, 0);
    root.add(o);
    return o;
  };

  let powered = false;
  let lastRedraw = 0;

  return {
    root,
    anchors: {
      display: displayAnchor,
      knob: knobAnchor,
      collar: collarAnchor,
      mount: mountAnchor,
      herramienta: statorAnchor,
    },
    views: {
      panel:       { target: at(unitBase + UNIT_H - BOX_H / 2), direction: new THREE.Vector3(0.15, 0.1, 1), distance: 470 },
      acople:      { target: at(unitBase + 70),                 direction: new THREE.Vector3(0.8, 0.2, 1), distance: 600 },
      herramienta: { target: at(TIP_Y + 14),                    direction: new THREE.Vector3(0.35, 0.22, 1), distance: 200 },
      muestra:     { target: at(PLATE_T + LIQ_H * 0.6),         direction: new THREE.Vector3(0.3, 0.42, 1), distance: 360 },
    },
    frameTop: unitTop + 40,
    frameMargin: 1.4,
    yaw: 0.1,
    setPower(on: boolean) {
      powered = on;
      if (on) displayOn = true;
    },
    update(dt: number, t: number, level: number) {
      /* pantalla LED: miles de rpm, una décima */
      const fixed = root.userData.rpmFraction as number | null;
      if (displayOn && t - lastRedraw > 1 / 12) {
        lastRedraw = t;
        const pinned = typeof fixed === 'number';
        const target = !powered
          ? 0
          : pinned
            ? RPM_MIN + (RPM_MAX - RPM_MIN) * Math.min(1, Math.max(0, fixed))
            : RPM_MIN + (RPM_IDLE - RPM_MIN) * level;
        displayRpm += (target - displayRpm) * (pinned ? 0.5 : 0.2);
        if (powered) displayRpm = Math.max(RPM_MIN, displayRpm);
        if (!powered && level < 0.02) displayOn = false;
        panelRedraw();
      }
      panelMat.emissiveIntensity = 0.35 * level;

      /* rotor + vibración del cabezal */
      rotor.rotation.y += dt * 90 * level;
      head.position.x = (Math.random() - 0.5) * 0.2 * level;
      head.position.z = (Math.random() - 0.5) * 0.2 * level;

      glowMat.opacity = 0.55 * level * (0.82 + Math.random() * 0.18);

      /* gotas: chorro radial desde el estátor, arrastradas por el vórtice */
      for (let i = 0; i < DROPS; i++) {
        const b = dropState[i];
        if (b.life >= b.max || b.s === 0) {
          if (level > 0.15 && Math.random() < 0.3 * level) {
            spawnDrop(b);
          } else {
            dummyObj.scale.setScalar(0);
            dummyObj.updateMatrix();
            droplets.setMatrixAt(i, dummyObj.matrix);
            continue;
          }
        }
        b.life += dt;
        b.x += b.vx * dt * 20;
        b.z += b.vz * dt * 20;
        b.y += b.vy * dt * 14;
        const rad = Math.hypot(b.x, b.z);
        const ang = Math.atan2(b.z, b.x) + 2.2 * level * dt;
        b.x = Math.cos(ang) * rad;
        b.z = Math.sin(ang) * rad;
        const maxR = BEAKER_R0 - 3;
        if (rad > maxR) {
          // en la pared sube; arriba vuelve hacia el eje
          b.vx *= -0.3; b.vz *= -0.3; b.vy = 1.2;
          b.x *= maxR / rad; b.z *= maxR / rad;
        }
        if (b.y > PLATE_T + LIQ_H - 8) { b.vy = -0.8; b.vx = -b.x * 0.05; b.vz = -b.z * 0.05; }
        if (b.y < PLATE_T + 3) { b.vy = Math.abs(b.vy); b.y = PLATE_T + 3; }
        const k = Math.min(1, b.life * 5) * (1 - b.life / b.max) * level;
        dummyObj.position.set(b.x, b.y, b.z);
        dummyObj.scale.setScalar(b.s * (0.4 + k));
        dummyObj.updateMatrix();
        droplets.setMatrixAt(i, dummyObj.matrix);
      }
      droplets.instanceMatrix.needsUpdate = true;

      /* superficie: embudo de vórtice alrededor del eje, con brazos en espiral */
      const p = surfGeo.attributes.position;
      if (level > 0.001 || p.getY(0) !== 0) {
        for (let i = 0; i < p.count; i++) {
          const x = surfBase[i * 3], z = surfBase[i * 3 + 2];
          const r = Math.hypot(x, z);
          const rr = r / surfR;
          const funnel = -30 * Math.exp(-(r * r) / (21 * 21)) + 3.5 * rr * rr;
          const spiral = 1.6 * Math.exp(-r * 0.03) * Math.sin(Math.atan2(z, x) * 3 + r * 0.22 - t * 12);
          p.setY(i, level < 0.001 ? 0 : level * (funnel + spiral));
        }
        p.needsUpdate = true;
        surfGeo.computeVertexNormals();
      }
    },
    dispose,
  };
}
