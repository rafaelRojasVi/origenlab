/**
 * Hielscher UP100H: bloque compacto de mano con el generador y el transductor
 * en una sola carcasa, apoyado en un soporte pequeño.
 *
 * Medidas (todo en milímetros; la escena lo reduce a su escala):
 *
 *   carcasa      185 de largo (x) × 130 de fondo (z) × 85 de alto (y), según la
 *                ficha del fabricante (1,1 kg). Sin pantalla: sólo un piloto de
 *                encendido, dos pulsadores y la perilla de amplitud en el costado
 *                que mira al operador (+z).
 *   sonotrodo    sale horizontal por el frente (+x), roscado M6, de unos 80 mm
 *                desde la tapa. Es el mismo diseño que el del UP400St, más fino:
 *                base Ø 15 que se afina hasta la punta. Hay dos puntas, MS2 (Ø 2)
 *                y MS7 (Ø 7, la estándar); la página muestra una y oculta la otra.
 *
 * El color de la carcasa (gris oscuro) es una estimación por comparación con el
 * resto de la gama: el fabricante no lo da por escrito. El soporte de cuna y el
 * cable son puesta en escena, no salen de ninguna medida.
 */
import * as THREE from 'three';
import type { BuildOptions, BuiltModel } from '../types';
import { SANS, canvasTexture, labGlass, lathe, roundedBox } from './kit';

const L = 185;
const WID = 130;
const H = 85;
const R = 8;
/** Altura del eje del sonotrodo sobre el suelo. */
const PLATE_TOP = 10;
const CRADLE_H = 44;
const YC = PLATE_TOP + CRADLE_H + H / 2;
const CX = L / 2;
/** Centro de la placa del soporte: cubre desde detrás de la carcasa hasta el vaso. */
const PLATE_CX = 130;
/** Vaso bajo la punta. */
const BEAKER_X = 251;
const BEAKER_RIM = 88;
const BEAKER_H = 60;
const SONO_LEN = 80;

export function build(options: BuildOptions = {}): BuiltModel {
  void options;
  const root = new THREE.Group();
  const disposables: { dispose(): void }[] = [];
  const keep = <T extends { dispose(): void }>(item: T) => {
    disposables.push(item);
    return item;
  };
  // `roundedBox` ensancha el contorno por el radio: se descuenta para que la
  // caja mida exactamente lo pedido.
  const rbox = (w: number, h: number, d: number, r: number) => keep(roundedBox(w - 2 * r, h - 2 * r, d, r));

  /* ── materiales ── */
  const housingMat = keep(
    new THREE.MeshPhysicalMaterial({ color: 0x2b313b, roughness: 0.5, metalness: 0.2, clearcoat: 0.7, clearcoatRoughness: 0.25 }),
  );
  const steel = keep(new THREE.MeshPhysicalMaterial({ color: 0xd4d8dc, metalness: 1, roughness: 0.24 }));
  // El sonotrodo se ve sobre un fondo claro: titanio más oscuro y con poco reflejo del entorno.
  const titanium = keep(new THREE.MeshPhysicalMaterial({ color: 0x6f757b, metalness: 0.85, roughness: 0.4, envMapIntensity: 0.6 }));
  const hornSteel = keep(new THREE.MeshPhysicalMaterial({ color: 0x858b91, metalness: 0.9, roughness: 0.36, envMapIntensity: 0.7 }));
  const black = keep(new THREE.MeshStandardMaterial({ color: 0x15171a, roughness: 0.6 }));
  const rubber = keep(new THREE.MeshStandardMaterial({ color: 0x101113, roughness: 0.9 }));
  const alu = keep(new THREE.MeshPhysicalMaterial({ color: 0xdfe2e4, metalness: 0.9, roughness: 0.34 }));

  /* ── soporte: placa y cuna de dos apoyos ── */
  const plate = new THREE.Mesh(rbox(300, 10, 190, 4), alu);
  plate.position.set(PLATE_CX, PLATE_TOP / 2, 0);
  plate.castShadow = true;
  plate.receiveShadow = true;
  root.add(plate);
  for (const x of [48, 138]) {
    const post = new THREE.Mesh(rbox(26, CRADLE_H, 104, 4), alu);
    post.position.set(x, PLATE_TOP + CRADLE_H / 2, 0);
    post.castShadow = true;
    root.add(post);
    const pad = new THREE.Mesh(rbox(28, 4, 106, 1.5), rubber);
    pad.position.set(x, PLATE_TOP + CRADLE_H - 1, 0);
    root.add(pad);
  }

  /* ── vaso bajo la punta, sobre un taco: la punta queda sobre su boca ── */
  const blockH = BEAKER_RIM - BEAKER_H - PLATE_TOP;
  const block = new THREE.Mesh(rbox(58, blockH, 58, 4), alu);
  block.position.set(BEAKER_X, PLATE_TOP + blockH / 2, 0);
  block.castShadow = true;
  root.add(block);
  const beakerBase = BEAKER_RIM - BEAKER_H;
  const LIQUID = 38;
  const beaker = new THREE.Mesh(
    keep(lathe([[0, 0], [24, 0], [25.5, 1.5], [25.5, BEAKER_H], [26.8, BEAKER_H + 1.2], [25.2, BEAKER_H + 1.2], [24, BEAKER_H - 0.4], [24, 2.5], [0, 2.5]])),
    keep(labGlass()),
  );
  beaker.position.set(BEAKER_X, beakerBase, 0);
  beaker.renderOrder = 3;
  root.add(beaker);
  const liquid = new THREE.Mesh(
    keep(lathe([[0, 2.6], [23.8, 2.6], [23.8, LIQUID], [0, LIQUID]])),
    keep(
      new THREE.MeshPhysicalMaterial({
        color: 0x9fd8f5,
        roughness: 0.15,
        clearcoat: 0.6,
        emissive: 0x4aa8d8,
        emissiveIntensity: 0.15,
        transparent: true,
        opacity: 0.85,
        depthWrite: false,
        side: THREE.DoubleSide,
      }),
    ),
  );
  liquid.position.set(BEAKER_X, beakerBase, 0);
  liquid.renderOrder = 1;
  root.add(liquid);

  /* ── carcasa ── */
  const body = new THREE.Group();
  body.position.set(CX, YC, 0);
  root.add(body);
  const housing = new THREE.Mesh(rbox(L, H, WID, R), housingMat);
  housing.castShadow = true;
  housing.receiveShadow = true;
  body.add(housing);

  // Rejilla de ventilación en la cara de arriba.
  {
    const slotGeo = keep(new THREE.BoxGeometry(2.4, 1.2, 74));
    for (let i = 0; i < 8; i++) {
      const slot = new THREE.Mesh(slotGeo, black);
      slot.position.set(-62 + i * 7, H / 2 - 0.2, 0);
      body.add(slot);
    }
  }

  /* ── costado del operador: rótulo, escala, piloto, pulsadores y perilla ── */
  const PW = 160;
  const PH = 60;
  const KNOB = { x: 30, y: -2 };
  const panel = canvasTexture(1280, 480, (g, w, h) => {
    const px = (x: number) => ((x + PW / 2) / PW) * w;
    const py = (y: number) => ((PH / 2 - y) / PH) * h;
    const s = w / PW;
    const bg = g.createLinearGradient(0, 0, 0, h);
    bg.addColorStop(0, '#262c35');
    bg.addColorStop(1, '#1d222a');
    g.fillStyle = bg;
    g.fillRect(0, 0, w, h);
    g.strokeStyle = '#3d6fb6';
    g.lineWidth = 3;
    g.strokeRect(10, 10, w - 20, h - 20);
    g.textBaseline = 'middle';
    g.fillStyle = '#e8edf5';
    g.font = `600 ${7 * s}px ${SANS}`;
    g.fillText('hielscher', px(-72), py(18));
    g.fillStyle = '#fff';
    g.font = `700 ${11 * s}px ${SANS}`;
    g.fillText('UP100H', px(-72), py(-8));
    g.fillStyle = '#9aa3b0';
    g.font = `400 ${4.2 * s}px ${SANS}`;
    g.fillText('ultrasonic processor', px(-72), py(-19));
    // Escala de amplitud alrededor de la perilla.
    g.strokeStyle = '#c9ced6';
    g.lineWidth = 0.5 * s;
    for (let i = 0; i <= 10; i++) {
      const a = (-135 + i * 27) * (Math.PI / 180);
      const x0 = KNOB.x + Math.sin(a) * 17;
      const y0 = KNOB.y + Math.cos(a) * 17;
      const x1 = KNOB.x + Math.sin(a) * 20.5;
      const y1 = KNOB.y + Math.cos(a) * 20.5;
      g.beginPath();
      g.moveTo(px(x0), py(y0));
      g.lineTo(px(x1), py(y1));
      g.stroke();
    }
    g.fillStyle = '#9aa3b0';
    g.font = `500 ${3.4 * s}px ${SANS}`;
    g.textAlign = 'center';
    g.fillText('AMPLITUDE', px(KNOB.x), py(KNOB.y - 25));
    g.fillText('POWER', px(65), py(21));
    g.fillStyle = '#e8edf5';
    g.font = `600 ${5 * s}px ${SANS}`;
    g.fillText('−', px(58), py(-19));
    g.fillText('+', px(72), py(-19));
    g.textAlign = 'left';
  });
  keep(panel.texture);
  const panelMesh = new THREE.Mesh(
    keep(new THREE.PlaneGeometry(PW, PH)),
    keep(new THREE.MeshPhysicalMaterial({ map: panel.texture, roughness: 0.4, clearcoat: 0.5, clearcoatRoughness: 0.3 })),
  );
  panelMesh.position.set(0, 0, WID / 2 + 0.15);
  body.add(panelMesh);

  const FACE = WID / 2 + 0.3;
  // Piloto de encendido.
  const ledMat = keep(new THREE.MeshStandardMaterial({ color: 0x1c3a26, emissive: 0x4ade80, emissiveIntensity: 0, roughness: 0.3 }));
  const led = new THREE.Mesh(keep(new THREE.CylinderGeometry(2.6, 2.6, 2, 24)), ledMat);
  led.rotation.x = Math.PI / 2;
  led.position.set(65, 12, FACE + 1);
  body.add(led);
  const ledRing = new THREE.Mesh(keep(new THREE.TorusGeometry(3, 0.7, 10, 24)), steel);
  ledRing.position.set(65, 12, FACE + 0.8);
  body.add(ledRing);
  // Pulsadores de amplitud.
  const buttonGeo = keep(new THREE.CylinderGeometry(3.6, 3.8, 2.4, 28));
  for (const x of [58, 72]) {
    const button = new THREE.Mesh(buttonGeo, black);
    button.rotation.x = Math.PI / 2;
    button.position.set(x, -10, FACE + 1.1);
    body.add(button);
  }
  // Perilla de amplitud: el grupo gira sobre su eje (+z).
  const knob = new THREE.Group();
  knob.position.set(KNOB.x, KNOB.y, FACE);
  body.add(knob);
  {
    const base = new THREE.Mesh(keep(new THREE.CylinderGeometry(14.5, 15, 2.5, 48)), black);
    base.rotation.x = Math.PI / 2;
    base.position.z = 1.25;
    knob.add(base);
    const cap = new THREE.Mesh(keep(new THREE.CylinderGeometry(11.5, 12.8, 11, 48)), rubber);
    cap.rotation.x = Math.PI / 2;
    cap.position.z = 2.5 + 5.5;
    cap.castShadow = true;
    knob.add(cap);
    const top = new THREE.Mesh(keep(new THREE.CylinderGeometry(8.5, 8.5, 0.8, 40)), steel);
    top.rotation.x = Math.PI / 2;
    top.position.z = 13.6;
    knob.add(top);
    const mark = new THREE.Mesh(
      keep(new THREE.BoxGeometry(1.8, 8, 0.8)),
      keep(new THREE.MeshStandardMaterial({ color: 0xffffff, emissive: 0xffffff, emissiveIntensity: 0.25 })),
    );
    mark.position.set(0, 5, 14.2);
    knob.add(mark);
  }
  let knobAngle = -1.1;

  /* ── atrás: salida del cable ── */
  {
    const gland = new THREE.Mesh(keep(new THREE.CylinderGeometry(6.5, 7.5, 8, 24)), black);
    gland.rotation.z = Math.PI / 2;
    gland.position.set(-L / 2 - 2, -14, -22);
    body.add(gland);
  }
  const cableStart = new THREE.Vector3(CX - L / 2 - 6, YC - 14, -22);
  const cable = new THREE.Mesh(
    keep(
      new THREE.TubeGeometry(
        new THREE.CatmullRomCurve3([
          cableStart,
          cableStart.clone().add(new THREE.Vector3(-8, -1, -2)),
          new THREE.Vector3(-14, YC - 40, -34),
          new THREE.Vector3(-10, PLATE_TOP + 8, -56),
          new THREE.Vector3(2, PLATE_TOP + 3.2, -76),
          new THREE.Vector3(24, PLATE_TOP + 3.2, -88),
          new THREE.Vector3(50, PLATE_TOP + 3.2, -92),
        ], false, 'centripetal'),
        64,
        3,
        12,
      ),
    ),
    rubber,
  );
  cable.castShadow = true;
  root.add(cable);

  /* ── sonotrodo horizontal: el eje del torneado (+y) apunta a +x ── */
  const horn = new THREE.Group();
  horn.position.set(L, YC, 0);
  horn.rotation.z = -Math.PI / 2;
  root.add(horn);
  const collar = new THREE.Mesh(
    keep(lathe([[0, -4], [23, -4], [23, 3], [16, 4], [16, 11], [12.5, 12], [12.5, 18], [0, 18]])),
    hornSteel,
  );
  collar.castShadow = true;
  horn.add(collar);
  const oring = new THREE.Mesh(keep(new THREE.TorusGeometry(12.5, 1.6, 12, 40)), rubber);
  oring.rotation.x = Math.PI / 2;
  oring.position.y = 15;
  horn.add(oring);
  // Las puntas vibran juntas: el temblor se aplica al grupo.
  const tips = new THREE.Group();
  horn.add(tips);
  const variant = (r: number, taperAt: number) =>
    new THREE.Mesh(
      keep(lathe([[0, 18], [7.5, 18], [7.5, 30], [6, 30 + taperAt * 0.4], [r, 30 + taperAt], [r, SONO_LEN], [0, SONO_LEN]], 48)),
      titanium,
    );
  const sonotrodes = [
    { id: 'MS2', r: 1, mesh: variant(1, 22) },
    { id: 'MS7', r: 3.5, mesh: variant(3.5, 12) },
  ];
  for (const s of sonotrodes) {
    s.mesh.castShadow = true;
    s.mesh.visible = s.id === 'MS7';
    tips.add(s.mesh);
  }

  /* ── anillo de emisión en la punta (sólo en marcha) ── */
  const glowTex = canvasTexture(128, 128, (g) => {
    const r = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    r.addColorStop(0, 'rgba(255,255,255,.9)');
    r.addColorStop(0.35, 'rgba(186,230,253,.4)');
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
  glow.position.set(L + SONO_LEN + 2, YC, 0);
  root.add(glow);
  const ringGeo = keep(new THREE.TorusGeometry(1, 0.07, 10, 64));
  const rings = [0, 1].map(() => {
    const mat = keep(
      new THREE.MeshBasicMaterial({ color: 0x8fd8ff, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false }),
    );
    const ring = new THREE.Mesh(ringGeo, mat);
    ring.rotation.y = Math.PI / 2;
    ring.position.set(L + SONO_LEN + 2, YC, 0);
    ring.visible = false;
    root.add(ring);
    return { ring, mat };
  });

  /* ── anclas y vistas ── */
  const at = (x: number, y: number, z: number) => {
    const o = new THREE.Object3D();
    o.position.set(x, y, z);
    root.add(o);
    return o;
  };
  const housingAnchor = at(CX + 25, YC + H / 2, 18);
  const faceAnchor = at(CX, YC, WID / 2);
  const knobAnchor = at(CX + KNOB.x, YC + KNOB.y, WID / 2 + 14);
  const tip = at(L + 62, YC, 0);
  const cableAnchor = at(cableStart.x - 12, cableStart.y - 2, cableStart.z - 3);

  let powered = false;
  let shownLed = false;
  const activeTipRadius = () => {
    for (const s of sonotrodes) if (s.mesh.visible) return s.r;
    return sonotrodes[1].r;
  };

  return {
    root,
    anchors: {
      housing: housingAnchor,
      knob: knobAnchor,
      tip,
      cable: cableAnchor,
      ...Object.fromEntries(sonotrodes.map((s) => [s.id, s.mesh])),
    },
    views: {
      frontal: { target: faceAnchor, direction: new THREE.Vector3(0.05, 0.12, 1), distance: 430 },
      sonotrodo: { target: tip, direction: new THREE.Vector3(0.55, 0.2, 1), distance: 260 },
    },
    setPower(on) {
      powered = on;
    },
    update(dt, t, level) {
      if (shownLed !== powered) {
        shownLed = powered;
        ledMat.color.setHex(powered ? 0x4ade80 : 0x1c3a26);
      }
      ledMat.emissiveIntensity = powered ? 1.6 + 0.3 * Math.sin(t * 3) : 0;
      const target = powered ? 0.9 + 0.1 * Math.sin(t * 0.8) : -1.1;
      knobAngle += (target - knobAngle) * Math.min(1, dt * 5);
      knob.rotation.z = -knobAngle;

      const tipR = activeTipRadius();
      tips.position.x = (Math.random() - 0.5) * 0.2 * level;
      glowMat.opacity = 0.6 * level * (0.8 + Math.random() * 0.2);
      glow.scale.setScalar(26 + tipR * 4);
      // Dos ondas desfasadas que salen de la punta, crecen y se apagan.
      for (let i = 0; i < rings.length; i++) {
        const { ring, mat } = rings[i];
        const phase = (t * 0.9 + i * 0.5) % 1;
        ring.visible = level > 0.02;
        ring.position.set(L + SONO_LEN + 2 + phase * 26, YC, 0);
        ring.scale.setScalar(tipR + 2 + phase * 11);
        mat.opacity = 0.85 * level * (1 - phase) * Math.min(1, phase * 8);
      }
    },
    dispose() {
      for (const item of disposables) item.dispose();
      disposables.length = 0;
    },
  };
}
