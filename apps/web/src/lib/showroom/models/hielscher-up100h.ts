/**
 * Hielscher UP100H, modelado a partir de la fotografía del equipo real
 * (hielscher.com, página del UP100H) y de las medidas de su ficha.
 *
 * Medidas (todo en milímetros; la escena lo reduce a su escala):
 *
 *   carcasa      185 de largo × 130 de ancho × 85 de grueso (L×A×A de la
 *                ficha, 1,1 kg). El largo va en el eje del sonotrodo; el ancho,
 *                de lado a lado del panel; el grueso, del panel a la espalda.
 *                Amarilla, con un mango cilíndrico estriado por el borde
 *                izquierdo (el transductor va dentro) que asoma sobre el panel.
 *   panel        gris claro, rodeado de amarillo: «UP100H ultrasonic
 *                processor», dos perillas de escala («Cycle» y «Amplitude %»),
 *                un pulsador de encendido arriba a la derecha y la placa
 *                «hielscher» abajo a la izquierda. Sin pantalla: el ciclo de
 *                pulso y la amplitud se ajustan a mano.
 *   sonotrodo    sale del extremo corto, en el eje del mango, tras un collarín
 *                metálico: base de Ø 12 y punta de titanio de Ø 7 (la MS7 de la
 *                ficha, unos 80 mm).
 *
 * Estimado de la fotografía (la ficha no lo da): la posición de cada pieza del
 * panel, el diámetro del mango (40) y de las perillas (24), el collarín (Ø 34)
 * y el largo de la base del sonotrodo (40). En la fotografía las perillas son
 * discos claros moleteados con la escala impresa, y el cable sale por el
 * costado derecho, cerca del extremo del sonotrodo.
 *
 * Puesta en escena, como la fotografía de catálogo con soporte: el equipo
 * inclinado 20°, sujeto por una pinza a la varilla de un soporte ST1-16
 * (base de 300 × 150 de la ficha), con la punta sumergida en un vaso de
 * precipitado. La pinza, el vaso y el líquido no salen de ninguna medida.
 */
import * as THREE from 'three';
import type { BuildOptions, BuiltModel } from '../types';
import { SANS, canvasTexture, labGlass, lathe, roundedBox } from './kit';

/* ── carcasa, en el marco del equipo: el sonotrodo sale hacia −y desde el origen ── */
const L = 185;
const WID = 130;
const THICK = 85;
/** Mango: cilindro a lo largo de y, en el borde izquierdo. */
const BARREL_R = 20;
const BARREL_Z = 12;
/** La caja va de x = −20 a 110 y de z = −53 a 28 (cara del panel); el mango asoma 4 mm. */
const X0 = -BARREL_R;
const X1 = X0 + WID;
const FACE_Z = BARREL_Z + BARREL_R - 4;
const BACK_Z = FACE_Z + 4 - THICK;
const R = 6;
/** Sonotrodo: collarín, base y punta MS7, desde la boca del mango. */
const COLLAR = 8;
const BASE_LEN = 40;
const TIP_LEN = 80;
const HORN = COLLAR + BASE_LEN + TIP_LEN;
/** Perillas y pulsador (centro, en la cara del panel). */
const KNOB_R = 12;
const CYCLE = { x: 40, y: 134 };
const AMP = { x: 40, y: 100 };
const BUTTON = { x: 92, y: 150 };

/* ── escena, en el marco del modelo ── */
const TILT = THREE.MathUtils.degToRad(20);
const BASE_T = 8;
const BEAKER = { x: 34, z: 0, r: 35, h: 95 };
const LIQUID = 58;
/** La punta queda 26 mm bajo la superficie, algo corrida a la izquierda del eje del vaso. */
const TIP_WORLD = new THREE.Vector3(BEAKER.x - 6, BASE_T + LIQUID - 26, BEAKER.z);

export function build(options: BuildOptions = {}): BuiltModel {
  const root = new THREE.Group();
  const disposables: { dispose(): void }[] = [];
  const keep = <T extends { dispose(): void }>(item: T) => {
    disposables.push(item);
    return item;
  };
  // `roundedBox` ensancha el contorno por el radio: se descuenta para que la caja mida lo pedido.
  const rbox = (w: number, h: number, d: number, r: number) => keep(roundedBox(w - 2 * r, h - 2 * r, d, r));

  /* ── materiales ── */
  // Más hondo que el de la fotografía: el tono del estudio lo aclara.
  const YELLOW = '#d69c00';
  const housingMat = keep(
    new THREE.MeshPhysicalMaterial({ color: YELLOW, roughness: 0.45, metalness: 0, clearcoat: 0.25, clearcoatRoughness: 0.35, envMapIntensity: 0.7 }),
  );
  const steel = keep(new THREE.MeshPhysicalMaterial({ color: 0xc9cdd1, metalness: 1, roughness: 0.22, envMapIntensity: 1.1 }));
  const titanium = keep(new THREE.MeshPhysicalMaterial({ color: 0xa9aeb2, metalness: 0.95, roughness: 0.3, envMapIntensity: 1 }));
  const black = keep(new THREE.MeshStandardMaterial({ color: 0x17191c, roughness: 0.55 }));
  const rubber = keep(new THREE.MeshStandardMaterial({ color: 0x101113, roughness: 0.85 }));
  const alu = keep(new THREE.MeshPhysicalMaterial({ color: 0xdfe2e4, metalness: 0.9, roughness: 0.34 }));
  const dialMat = keep(new THREE.MeshPhysicalMaterial({ color: 0xe9ebec, roughness: 0.35, clearcoat: 0.5, flatShading: true }));

  /* ── soporte ST1-16: base ── */
  const base = new THREE.Mesh(rbox(300, BASE_T, 150, 3), steel);
  base.position.set(70, BASE_T / 2, -20);
  base.receiveShadow = true;
  base.castShadow = true;
  root.add(base);

  /* ── vaso de precipitado con la muestra ── */
  const beaker = new THREE.Mesh(
    keep(
      lathe([
        [0, 0], [BEAKER.r - 2, 0], [BEAKER.r, 2], [BEAKER.r, BEAKER.h], [BEAKER.r + 1.6, BEAKER.h + 1.4],
        [BEAKER.r - 0.4, BEAKER.h + 1.4], [BEAKER.r - 1.8, BEAKER.h - 0.5], [BEAKER.r - 1.8, 2.6], [0, 2.6],
      ]),
    ),
    keep(labGlass()),
  );
  beaker.position.set(BEAKER.x, BASE_T, BEAKER.z);
  beaker.renderOrder = 3;
  root.add(beaker);
  // Marcas de graduación en el vidrio.
  {
    const markGeo = keep(new THREE.BoxGeometry(9, 0.6, 0.4));
    const markMat = keep(new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.55 }));
    for (let i = 0; i < 4; i++) {
      const m = new THREE.Mesh(markGeo, markMat);
      m.position.set(BEAKER.x - 14, BASE_T + 22 + i * 18, BEAKER.z + BEAKER.r + 0.3);
      m.scale.x = i % 2 ? 0.6 : 1;
      m.renderOrder = 4;
      root.add(m);
    }
  }
  const IN = BEAKER.r - 2;
  const liquid = new THREE.Mesh(
    keep(lathe([[0, 2.7], [IN, 2.7], [IN, LIQUID - 0.2]])),
    keep(
      new THREE.MeshPhysicalMaterial({
        color: 0xbfe3f2,
        roughness: 0.12,
        clearcoat: 0.6,
        emissive: 0x5fb4dc,
        emissiveIntensity: 0.12,
        transparent: true,
        opacity: 0.7,
        depthWrite: false,
        side: THREE.DoubleSide,
      }),
    ),
  );
  liquid.position.set(BEAKER.x, BASE_T, BEAKER.z);
  liquid.renderOrder = 1;
  root.add(liquid);
  const surfaceGeo = keep(new THREE.CircleGeometry(IN, 72));
  surfaceGeo.rotateX(-Math.PI / 2);
  const surfaceBase = Float32Array.from(surfaceGeo.attributes.position.array as ArrayLike<number>);
  const surface = new THREE.Mesh(
    surfaceGeo,
    keep(
      new THREE.MeshPhysicalMaterial({
        color: 0xd6eef8,
        roughness: 0.05,
        clearcoat: 1,
        emissive: 0x5fb4dc,
        emissiveIntensity: 0.1,
        transparent: true,
        opacity: 0.8,
        depthWrite: false,
      }),
    ),
  );
  surface.position.set(BEAKER.x, BASE_T + LIQUID, BEAKER.z);
  surface.renderOrder = 2;
  root.add(surface);

  /* ── el equipo: un grupo inclinado; su origen es la boca del mango ── */
  const unit = new THREE.Group();
  unit.rotation.z = -TILT;
  unit.position.copy(TIP_WORLD).sub(new THREE.Vector3(0, -HORN, BARREL_Z).applyEuler(unit.rotation));
  root.add(unit);
  unit.updateMatrixWorld(true);

  const housing = new THREE.Mesh(rbox(WID, L, FACE_Z - BACK_Z, R), housingMat);
  housing.position.set((X0 + X1) / 2, L / 2, (FACE_Z + BACK_Z) / 2);
  housing.castShadow = true;
  housing.receiveShadow = true;
  unit.add(housing);

  // Mango del transductor, con la boca redondeada hacia el sonotrodo.
  const barrel = new THREE.Mesh(
    keep(lathe([[0, -3], [14, -3], [18, -1.5], [BARREL_R, 2], [BARREL_R, L - 4], [BARREL_R - 3, L], [0, L]], 64)),
    housingMat,
  );
  barrel.position.set(0, 0, BARREL_Z);
  barrel.castShadow = true;
  unit.add(barrel);
  // Estrías del agarre: arcos sobre la cara de afuera y la de arriba del mango.
  {
    const ARC = THREE.MathUtils.degToRad(150);
    const ribGeo = keep(new THREE.TorusGeometry(BARREL_R + 0.4, 2.4, 8, 28, ARC));
    ribGeo.rotateX(Math.PI / 2);
    // Centra el arco en la diagonal de afuera arriba (−x, +z).
    ribGeo.rotateY(-(THREE.MathUtils.degToRad(135) - ARC / 2));
    for (const y of [64, 84, 104, 124]) {
      const rib = new THREE.Mesh(ribGeo, housingMat);
      rib.position.set(0, y, BARREL_Z);
      rib.castShadow = true;
      unit.add(rib);
    }
  }

  /* ── cara del panel: un lienzo que cubre la cara visible, con el amarillo del borde ── */
  const FX0 = BARREL_R;
  const FX1 = X1 - R;
  const FY0 = R;
  const FY1 = L - R;
  const FW = FX1 - FX0;
  const FH = FY1 - FY0;
  const face = canvasTexture(768, Math.round((768 * FH) / FW), (g, w, h) => {
    const s = w / FW;
    const px = (x: number) => (x - FX0) * s;
    const py = (y: number) => (FY1 - y) * s;
    const rr = (x0: number, y0: number, x1: number, y1: number, r: number) => {
      g.beginPath();
      g.roundRect(px(x0), py(y1), (x1 - x0) * s, (y1 - y0) * s, r * s);
    };
    g.fillStyle = YELLOW;
    g.fillRect(0, 0, w, h);
    // Panel gris, con un filo oscuro: va un poco hundido en el amarillo.
    rr(25, 12, 105, 176, 3);
    g.fillStyle = '#d5d8d9';
    g.fill();
    g.lineWidth = 0.6 * s;
    g.strokeStyle = 'rgba(80,70,20,.45)';
    g.stroke();
    // El amarillo entra en el panel alrededor de las perillas.
    g.beginPath();
    g.moveTo(px(25), py(80));
    g.lineTo(px(44), py(80));
    g.bezierCurveTo(px(62), py(80), px(62), py(154), px(44), py(154));
    g.lineTo(px(25), py(154));
    g.closePath();
    g.fillStyle = YELLOW;
    g.fill();
    g.strokeStyle = 'rgba(80,70,20,.35)';
    g.stroke();
    // Tornillos en las esquinas del panel.
    for (const [x, y] of [[22, 180], [102, 180], [22, 8], [102, 8]]) {
      g.beginPath();
      g.arc(px(x), py(y), 2.1 * s, 0, Math.PI * 2);
      g.fillStyle = '#5b5a52';
      g.fill();
      g.beginPath();
      g.arc(px(x), py(y), 1.2 * s, 0, Math.PI * 2);
      g.fillStyle = '#2a2a26';
      g.fill();
    }
    g.textBaseline = 'middle';
    g.textAlign = 'left';
    // Rótulo del modelo.
    g.fillStyle = '#1f2328';
    g.font = `700 ${9 * s}px ${SANS}`;
    g.fillText('UP100H', px(62), py(166));
    g.font = `500 ${3 * s}px ${SANS}`;
    g.fillStyle = '#4a5058';
    g.fillText('ultrasonic processor', px(62), py(159));
    // Pulsador: el símbolo de encendido y «0 / I».
    g.strokeStyle = '#2a2f36';
    g.lineWidth = 0.55 * s;
    g.beginPath();
    g.arc(px(BUTTON.x - 11), py(BUTTON.y), 2.2 * s, -Math.PI / 2 + 0.6, -Math.PI / 2 - 0.6 + Math.PI * 2);
    g.stroke();
    g.beginPath();
    g.moveTo(px(BUTTON.x - 11), py(BUTTON.y));
    g.lineTo(px(BUTTON.x - 11), py(BUTTON.y + 3));
    g.stroke();
    g.fillStyle = '#2a2f36';
    g.font = `500 ${2.6 * s}px ${SANS}`;
    g.textAlign = 'center';
    g.fillText('0 / I', px(BUTTON.x), py(BUTTON.y - 8));
    // Rótulos de las perillas, con su flecha.
    g.textAlign = 'left';
    const label = (text: string, y: number) => {
      g.beginPath();
      g.moveTo(px(62), py(y));
      g.lineTo(px(65.5), py(y + 2));
      g.lineTo(px(65.5), py(y - 2));
      g.closePath();
      g.fillStyle = '#2a2f36';
      g.fill();
      g.font = `600 ${5 * s}px ${SANS}`;
      g.fillText(text, px(69), py(y));
    };
    label('Cycle', CYCLE.y);
    label('Amplitude %', AMP.y);
    // Placa «hielscher»: marco azul y nombre.
    rr(31, 26, 71, 44, 2);
    g.fillStyle = '#e8ecf3';
    g.fill();
    g.strokeStyle = '#3d5fa8';
    g.lineWidth = 0.7 * s;
    g.stroke();
    g.fillStyle = '#2f4f9a';
    g.textAlign = 'center';
    g.font = `600 ${7 * s}px Georgia, "Times New Roman", serif`;
    g.fillText('hielscher', px(51), py(37));
    g.font = `500 ${2.3 * s}px ${SANS}`;
    g.fillText('ultrasound technology', px(51), py(30));
  });
  keep(face.texture);
  const faceMesh = new THREE.Mesh(
    keep(new THREE.PlaneGeometry(FW, FH)),
    keep(new THREE.MeshPhysicalMaterial({ map: face.texture, roughness: 0.45, clearcoat: 0.25, clearcoatRoughness: 0.35, envMapIntensity: 0.7 })),
  );
  faceMesh.position.set((FX0 + FX1) / 2, (FY0 + FY1) / 2, FACE_Z + 0.12);
  faceMesh.receiveShadow = true;
  unit.add(faceMesh);

  /* ── perillas: disco claro moleteado con su escala impresa; giran sobre +z ── */
  const dialTex = canvasTexture(256, 256, (g, w) => {
    const c = w / 2;
    g.fillStyle = '#eef0f1';
    g.fillRect(0, 0, w, w);
    g.strokeStyle = '#2b3036';
    g.fillStyle = '#2b3036';
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.font = `600 24px ${SANS}`;
    for (let i = 0; i <= 10; i++) {
      const a = (-135 + i * 27) * (Math.PI / 180);
      const inner = i % 2 ? 90 : 82;
      g.lineWidth = i % 2 ? 2.5 : 4.5;
      g.beginPath();
      g.moveTo(c + Math.sin(a) * 104, c - Math.cos(a) * 104);
      g.lineTo(c + Math.sin(a) * inner, c - Math.cos(a) * inner);
      g.stroke();
      if (i % 2 === 0) g.fillText(String(i), c + Math.sin(a) * 62, c - Math.cos(a) * 62);
    }
  });
  keep(dialTex.texture);
  const dialTop = keep(new THREE.MeshPhysicalMaterial({ map: dialTex.texture, roughness: 0.35, clearcoat: 0.5 }));
  const knobSide = keep(new THREE.CylinderGeometry(KNOB_R, KNOB_R + 0.4, 8, 36, 1, true));
  knobSide.rotateX(Math.PI / 2);
  const knobTop = keep(new THREE.CircleGeometry(KNOB_R - 0.2, 48));
  const knobSkirt = keep(new THREE.CylinderGeometry(KNOB_R + 1.6, KNOB_R + 1.8, 1.4, 48));
  knobSkirt.rotateX(Math.PI / 2);
  const makeKnob = (at: { x: number; y: number }) => {
    const knob = new THREE.Group();
    knob.position.set(at.x, at.y, FACE_Z);
    unit.add(knob);
    const skirt = new THREE.Mesh(knobSkirt, black);
    skirt.position.z = 0.7;
    knob.add(skirt);
    const side = new THREE.Mesh(knobSide, dialMat);
    side.position.z = 1.4 + 4;
    side.castShadow = true;
    knob.add(side);
    const top = new THREE.Mesh(knobTop, dialTop);
    top.position.z = 1.4 + 8 + 0.01;
    knob.add(top);
    return knob;
  };
  const cycleKnob = makeKnob(CYCLE);
  const ampKnob = makeKnob(AMP);

  // Pulsador de encendido.
  const button = new THREE.Mesh(keep(new THREE.CylinderGeometry(4.8, 5.2, 3.4, 32)), black);
  button.rotation.x = Math.PI / 2;
  button.position.set(BUTTON.x, BUTTON.y, FACE_Z + 1.7);
  unit.add(button);
  const buttonRing = new THREE.Mesh(keep(new THREE.TorusGeometry(5.6, 0.6, 8, 32)), black);
  buttonRing.position.set(BUTTON.x, BUTTON.y, FACE_Z + 0.3);
  unit.add(buttonRing);

  /* ── frente (hacia el sonotrodo): placa de características con CE ── */
  const plate = canvasTexture(512, 256, (g, w, h) => {
    g.fillStyle = '#f1f2f0';
    g.fillRect(0, 0, w, h);
    g.strokeStyle = '#9aa0a6';
    g.lineWidth = 4;
    g.strokeRect(6, 6, w - 12, h - 12);
    g.fillStyle = '#4a5058';
    for (let i = 0; i < 6; i++) g.fillRect(28, 34 + i * 30, 260 - (i % 3) * 50, 9);
    g.textBaseline = 'middle';
    g.font = `700 64px ${SANS}`;
    g.fillText('CE', 360, 170);
    g.font = `700 30px ${SANS}`;
    g.fillText('UP100H', 340, 60);
  });
  keep(plate.texture);
  const plateMesh = new THREE.Mesh(
    keep(new THREE.PlaneGeometry(64, 32)),
    keep(new THREE.MeshStandardMaterial({ map: plate.texture, roughness: 0.6 })),
  );
  plateMesh.rotation.x = Math.PI / 2;
  plateMesh.position.set(62, -0.15, (FACE_Z + BACK_Z) / 2 - 2);
  unit.add(plateMesh);

  /* ── collarín y sonotrodo, hacia −y ── */
  const horn = new THREE.Group();
  horn.position.set(0, 0, BARREL_Z);
  horn.rotation.x = Math.PI; // el torneado (+y de `lathe`) mira hacia −y
  unit.add(horn);
  const collar = new THREE.Mesh(
    keep(lathe([[0, 2], [17, 2], [17, COLLAR - 1], [15.5, COLLAR], [7, COLLAR], [7, COLLAR + 2], [0, COLLAR + 2]], 64)),
    steel,
  );
  collar.castShadow = true;
  horn.add(collar);
  {
    const holeGeo = keep(new THREE.CylinderGeometry(1.4, 1.4, 0.6, 12));
    for (let i = 0; i < 6; i++) {
      const a = (i / 6) * Math.PI * 2;
      const hole = new THREE.Mesh(holeGeo, black);
      hole.position.set(Math.cos(a) * 11.5, COLLAR + 0.05, Math.sin(a) * 11.5);
      horn.add(hole);
    }
  }
  // La punta vibra: el temblor se aplica al grupo.
  const tips = new THREE.Group();
  horn.add(tips);
  const sono = new THREE.Mesh(
    keep(
      lathe(
        [
          [0, COLLAR], [6, COLLAR], [6, COLLAR + BASE_LEN - 2], [5, COLLAR + BASE_LEN], [3.5, COLLAR + BASE_LEN + 1.5],
          [3.5, HORN - 0.6], [3, HORN], [0, HORN],
        ],
        48,
      ),
    ),
    titanium,
  );
  sono.castShadow = true;
  tips.add(sono);

  /* ── cable: sale por el costado derecho, cerca del frente, y cae a la mesa ── */
  const gland = new THREE.Mesh(keep(new THREE.CylinderGeometry(5, 6.5, 12, 24)), black);
  gland.rotation.z = Math.PI / 2;
  gland.position.set(X1 + 5, 26, BACK_Z + 22);
  unit.add(gland);
  const cableStart = unit.localToWorld(new THREE.Vector3(X1 + 10, 26, BACK_Z + 22));
  const cableDir = new THREE.Vector3(1, 0, 0).applyEuler(unit.rotation);
  const cable = new THREE.Mesh(
    keep(
      new THREE.TubeGeometry(
        new THREE.CatmullRomCurve3(
          [
            cableStart,
            cableStart.clone().addScaledVector(cableDir, 22),
            new THREE.Vector3(cableStart.x + 34, cableStart.y - 34, cableStart.z - 8),
            new THREE.Vector3(cableStart.x + 40, BASE_T + 10, cableStart.z - 22),
            new THREE.Vector3(cableStart.x + 44, BASE_T + 3.6, cableStart.z - 44),
            new THREE.Vector3(cableStart.x + 46, BASE_T + 3.6, -88),
            new THREE.Vector3(cableStart.x + 48, BASE_T - 2, -98),
            new THREE.Vector3(cableStart.x + 50, 3.4, -112),
            new THREE.Vector3(cableStart.x + 56, 3.4, -170),
          ],
          false,
          'centripetal',
        ),
        72,
        3.4,
        12,
      ),
    ),
    rubber,
  );
  cable.castShadow = true;
  root.add(cable);

  /* ── pinza: dos mordazas sobre el mango, un puente y el brazo a la varilla ── */
  const CLAMP_Y = 36;
  const JAW_TOP = BARREL_Z + BARREL_R + 3;
  const JAW_BACK = BACK_Z - 3;
  {
    const jaw = rbox(28, 26, 6, 2);
    const link = rbox(16, 10, 6, 1.5);
    for (const z of [JAW_TOP, JAW_BACK]) {
      const pad = new THREE.Mesh(jaw, rubber);
      pad.position.set(-8, CLAMP_Y, z);
      pad.castShadow = true;
      unit.add(pad);
      const arm = new THREE.Mesh(link, alu);
      arm.position.set(X0 - 2, CLAMP_Y, z);
      unit.add(arm);
    }
    const bridge = new THREE.Mesh(rbox(6, 26, JAW_TOP - JAW_BACK + 6, 2), alu);
    bridge.position.set(X0 - 8, CLAMP_Y, (JAW_TOP + JAW_BACK) / 2);
    bridge.castShadow = true;
    unit.add(bridge);
  }
  unit.updateMatrixWorld(true);
  const armFrom = unit.localToWorld(new THREE.Vector3(X0 - 10, CLAMP_Y, (JAW_TOP + JAW_BACK) / 2));
  const ROD = new THREE.Vector3(armFrom.x - 46, 0, armFrom.z - 46);
  const ROD_H = 430;
  const rod = new THREE.Mesh(keep(new THREE.CylinderGeometry(8, 8, ROD_H, 24)), steel);
  rod.position.set(ROD.x, BASE_T + ROD_H / 2, ROD.z);
  rod.castShadow = true;
  root.add(rod);
  const rodFoot = new THREE.Mesh(keep(new THREE.CylinderGeometry(13, 14, 10, 24)), steel);
  rodFoot.position.set(ROD.x, BASE_T + 5, ROD.z);
  root.add(rodFoot);
  {
    const to = new THREE.Vector3(ROD.x, armFrom.y, ROD.z);
    const len = armFrom.distanceTo(to);
    const arm = new THREE.Mesh(keep(new THREE.CylinderGeometry(5, 5, len, 18)), alu);
    arm.position.copy(armFrom).add(to).multiplyScalar(0.5);
    arm.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), to.clone().sub(armFrom).normalize());
    arm.castShadow = true;
    root.add(arm);
    const boss = new THREE.Mesh(rbox(26, 30, 26, 4), black);
    boss.position.copy(to);
    root.add(boss);
    const screw = new THREE.Mesh(keep(new THREE.CylinderGeometry(6, 6, 10, 20)), black);
    screw.rotation.x = Math.PI / 2;
    screw.position.set(to.x, to.y, to.z + 17);
    root.add(screw);
  }

  /* ── cavitación: halo y burbujas desde la punta ── */
  const tipWorld = TIP_WORLD.clone();
  const hornDir = new THREE.Vector3(0, -1, 0).applyEuler(unit.rotation);
  const glowTex = canvasTexture(128, 128, (g) => {
    const r = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    r.addColorStop(0, 'rgba(255,255,255,.95)');
    r.addColorStop(0.35, 'rgba(186,230,253,.45)');
    r.addColorStop(1, 'rgba(186,230,253,0)');
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
  glow.scale.set(44, 34, 1);
  glow.position.copy(tipWorld).addScaledVector(hornDir, 5);
  glow.renderOrder = 2;
  root.add(glow);

  const COUNT = options.lite ? 110 : 300;
  const bubbles = new THREE.InstancedMesh(
    keep(new THREE.SphereGeometry(1, 10, 8)),
    keep(
      new THREE.MeshPhysicalMaterial({
        color: 0xffffff,
        roughness: 0.05,
        clearcoat: 1,
        transparent: true,
        opacity: 0.9,
        emissive: 0xe0f4ff,
        emissiveIntensity: 0.35,
      }),
    ),
    COUNT,
  );
  bubbles.renderOrder = 2;
  bubbles.frustumCulled = false;
  root.add(bubbles);
  const dummy = new THREE.Object3D();
  const state = Array.from({ length: COUNT }, () => ({ x: 0, y: 0, z: 0, vx: 0, vy: 0, vz: 0, s: 0, life: 0, max: 1 }));
  dummy.scale.setScalar(0);
  dummy.updateMatrix();
  for (let i = 0; i < COUNT; i++) bubbles.setMatrixAt(i, dummy.matrix);
  // Dirección de la cara de la punta perpendicular al eje, en el plano de la inclinación.
  const across = new THREE.Vector3().crossVectors(hornDir, new THREE.Vector3(0, 0, 1));
  const spawn = (b: (typeof state)[number]) => {
    const a = Math.random() * Math.PI * 2;
    const r = Math.random() * 3.3;
    const ox = Math.cos(a) * r;
    const d = Math.random() * 2;
    b.x = tipWorld.x + across.x * ox + hornDir.x * d;
    b.y = tipWorld.y + across.y * ox + hornDir.y * d;
    b.z = tipWorld.z + Math.sin(a) * r;
    // Chorro en el eje del sonotrodo, que se abre.
    const v = 1.4 + Math.random() * 2;
    b.vx = hornDir.x * v + (Math.random() - 0.5) * 1.6;
    b.vy = hornDir.y * v + (Math.random() - 0.5) * 0.6;
    b.vz = (Math.random() - 0.5) * 1.6;
    b.s = 0.3 + Math.random() * 0.9;
    b.life = 0;
    b.max = 0.8 + Math.random() * 1.6;
  };
  // Donde el sonotrodo cruza la superficie: el centro de las ondas.
  const rippleX = tipWorld.x - BEAKER.x + (hornDir.x / hornDir.y) * -26;

  /* ── anclas y vistas ── */
  const at = (x: number, y: number, z: number) => {
    const o = new THREE.Object3D();
    o.position.set(x, y, z);
    unit.add(o);
    return o;
  };
  const housingAnchor = at(84, 120, FACE_Z);
  const panelAnchor = at(62, 118, FACE_Z);
  const knobsAnchor = at(54, 117, FACE_Z + 8);
  const cycleAnchor = at(CYCLE.x, CYCLE.y, FACE_Z + 9.5);
  const ampAnchor = at(AMP.x, AMP.y, FACE_Z + 9.5);
  const buttonAnchor = at(BUTTON.x, BUTTON.y, FACE_Z + 3.4);
  const bodyAnchor = at(45, 70, FACE_Z);
  const gripAnchor = at(0, 104, BARREL_Z + BARREL_R + 2.6);
  const tip = new THREE.Object3D();
  tip.position.copy(tipWorld).addScaledVector(hornDir, -8);
  root.add(tip);
  const cableAnchor = new THREE.Object3D();
  cableAnchor.position.copy(cableStart).addScaledVector(cableDir, 14);
  root.add(cableAnchor);

  let powered = false;
  let readout: number | null = null;
  let ampPct = 20;
  let cyclePct = 0;
  /**
   * Giro de una perilla para un valor 0–100: la escala impresa va de −135° a
   * +135° (horario desde arriba) y la flecha del panel está a la derecha, en
   * el rótulo; el giro lleva el valor pedido hacia +x.
   */
  const dial = (pct: number) => -Math.PI / 2 + THREE.MathUtils.degToRad(-135 + 2.7 * pct);

  return {
    root,
    anchors: {
      housing: housingAnchor,
      cycle: cycleAnchor,
      amplitude: ampAnchor,
      button: buttonAnchor,
      tip,
      grip: gripAnchor,
      cable: cableAnchor,
    },
    views: {
      perillas: { target: knobsAnchor, direction: new THREE.Vector3(0.05, 0.1, 1), distance: 380 },
      frontal: { target: panelAnchor, direction: new THREE.Vector3(0.02, 0.08, 1), distance: 360 },
      sonotrodo: { target: tip, direction: new THREE.Vector3(0.35, 0.22, 1), distance: 380 },
      // El equipo entero, de frente y algo a la derecha: la pinza queda detrás del mango.
      agarre: { target: bodyAnchor, direction: new THREE.Vector3(0.45, 0.2, 1), distance: 660 },
    },
    frameTop: 372,
    frameMargin: 1.5,
    yaw: 0.78,
    setPower(on) {
      powered = on;
    },
    setReadout(percent) {
      readout = percent;
    },
    update(dt, t, level) {
      // Perillas: en marcha, ciclo al máximo y la amplitud que marque el scroll (o cerca del 70 %).
      const ampTarget = powered ? (readout ?? 70 + 4 * Math.sin(t * 0.7)) : 20;
      const k = Math.min(1, dt * 6);
      ampPct += (ampTarget - ampPct) * k;
      cyclePct += ((powered ? 100 : 0) - cyclePct) * k;
      ampKnob.rotation.z = dial(ampPct);
      cycleKnob.rotation.z = dial(cyclePct);

      const amp = level * (0.35 + 0.65 * Math.min(1, Math.max(0, (ampPct - 20) / 80)));
      tips.position.x = (Math.random() - 0.5) * 0.25 * level;
      glowMat.opacity = 0.7 * amp * (0.8 + Math.random() * 0.2);

      const floor = BASE_T + 5;
      const top = BASE_T + LIQUID - 1;
      const limit = IN - 2;
      for (let i = 0; i < COUNT; i++) {
        const b = state[i];
        if (b.life >= b.max || b.s === 0) {
          if (level > 0.2 && Math.random() < 0.3 * amp) spawn(b);
          else {
            if (b.s !== 0) {
              b.s = 0;
              dummy.position.set(0, 0, 0);
              dummy.scale.setScalar(0);
              dummy.updateMatrix();
              bubbles.setMatrixAt(i, dummy.matrix);
            }
            continue;
          }
        }
        b.life += dt;
        b.vy += dt * 7;
        b.x += b.vx * dt * 14;
        b.y += b.vy * dt * 10;
        b.z += b.vz * dt * 14;
        const dx = b.x - BEAKER.x;
        const dz = b.z - BEAKER.z;
        const radial = Math.hypot(dx, dz);
        if (radial > limit) {
          b.vx *= -0.4;
          b.vz *= -0.4;
          b.x = BEAKER.x + (dx * limit) / radial;
          b.z = BEAKER.z + (dz * limit) / radial;
        }
        if (b.y < floor) {
          b.y = floor;
          b.vy = Math.abs(b.vy);
        }
        if (b.y > top) b.life = b.max;
        const f = Math.min(1, b.life * 6) * (1 - b.life / b.max) * level;
        dummy.position.set(b.x, b.y, b.z);
        dummy.scale.setScalar(b.s * (0.6 + f));
        dummy.updateMatrix();
        bubbles.setMatrixAt(i, dummy.matrix);
      }
      bubbles.instanceMatrix.needsUpdate = true;

      const p = surfaceGeo.attributes.position;
      if (level > 0.001) {
        for (let i = 0; i < p.count; i++) {
          const x = surfaceBase[i * 3];
          const z = surfaceBase[i * 3 + 2];
          const r = Math.hypot(x - rippleX, z);
          p.setY(i, amp * (Math.sin(r * 0.6 - t * 18) * 0.7 * Math.exp(-r * 0.04) + Math.sin(x * 0.4 + t * 7) * 0.2));
        }
        p.needsUpdate = true;
        surfaceGeo.computeVertexNormals();
      }
    },
    dispose() {
      for (const item of disposables) item.dispose();
      disposables.length = 0;
      bubbles.dispose();
    },
  };
}
