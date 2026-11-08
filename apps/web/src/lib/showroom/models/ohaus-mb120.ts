/**
 * OHAUS MB120, analizador de humedad con lámpara halógena.
 *
 * Fuente de las medidas: midsci.com y novatech-usa.com (ver `3d_assets.md`).
 * Todo en milímetros; la escena lo reduce a su escala al final. El frente
 * mira a +z; el ancho corre por x y la altura por y.
 *
 *   cuerpo cerrado   211 de ancho × 355 de fondo × 180 de alto (base + tapa)
 *   plato            Ø 90, acero inoxidable
 *   pantalla         táctil de 4,3″ (95 × 54 mm), inclinada 20° hacia atrás
 *
 * No salen de la fuente, son estimaciones de forma: el reparto de alturas
 * (base 86, tapa 94), la pendiente frontal, el anillo de la lámpara y los
 * colores (OHAUS no publica RAL).
 *
 * La tapa gira sobre una bisagra trasera y la gobierna la marcha: con el equipo
 * encendido (`setPower(true)`) se abre y se queda abierta; al apagarlo se cierra.
 * La lámpara sólo brilla con la tapa abierta y el equipo encendido. Una historia
 * puede tomar el mando:
 *
 *   model.setLidAngle(1.2);  // radianes, 0 … LID_MAX; `null` vuelve al modo automático
 *   model.root.userData.lidOpen = 1;   // lo mismo en fracción 0 … 1; `undefined` = automático
 *   model.root.userData.lidAngle;      // ángulo actual en radianes (sólo lectura)
 *
 * La vista `tapa` mira al interior con la tapa abierta.
 */
import * as THREE from 'three';
import type { BuiltModel } from '../types';
import { SANS, canvasTexture, lathe, roundedBox, type Draw } from './kit';

const W = 211;
const D = 355;
const BASE_H = 86;
const H = 180;
const BEVEL = 4;
const LID_H = H - BASE_H;
const LID_D = 232.5;
const SKIRT = 32;
const LID_MAX = 1.7; // ~97°
const HINGE_Z = -180;
const PAN_Z = -62;
const TILT = THREE.MathUtils.degToRad(20);

type Keep = <T extends { dispose(): void }>(item: T) => T;

/** Caja de `w × h × d` en total: `roundedBox` suma 2·r al ancho y al alto. */
function box(w: number, h: number, d: number, r: number) {
  return roundedBox(w - 2 * r, h - 2 * r, d, r);
}

export function build(): BuiltModel & { setLidAngle(angle: number | null): void } {
  const root = new THREE.Group();
  const disposables: { dispose(): void }[] = [];
  const keep: Keep = (item) => {
    disposables.push(item);
    return item;
  };

  /* ── materiales ── */
  // Carcasa de ABS gris azulado, más oscura que el blanco de la página para que
  // el equipo se despegue del fondo; laca mate para que la luz no la lave.
  const bodyMat = keep(
    new THREE.MeshPhysicalMaterial({ color: 0xaeb6bf, roughness: 0.5, metalness: 0.05, clearcoat: 0.3, clearcoatRoughness: 0.4 }),
  );
  const metal = keep(new THREE.MeshPhysicalMaterial({ color: 0x484e55, roughness: 0.4, metalness: 0.85 }));
  const panMat = keep(
    new THREE.MeshPhysicalMaterial({ color: 0xcfd3d6, metalness: 1, roughness: 0.24, envMapIntensity: 1.2, side: THREE.DoubleSide }),
  );
  const dark = keep(new THREE.MeshStandardMaterial({ color: 0x1a1c1f, roughness: 0.55 }));
  const alu = keep(new THREE.MeshPhysicalMaterial({ color: 0xdfe2e4, metalness: 0.9, roughness: 0.32, side: THREE.DoubleSide }));
  const reflectorMat = keep(
    new THREE.MeshPhysicalMaterial({ color: 0xe6dcc2, metalness: 1, roughness: 0.18, emissive: 0xff7a1a, emissiveIntensity: 0, side: THREE.DoubleSide }),
  );
  const lampMat = keep(
    new THREE.MeshStandardMaterial({ color: 0xfff2d8, roughness: 0.2, emissive: 0xff8a1f, emissiveIntensity: 0 }),
  );

  /* ── base: perfil lateral extruido al ancho ── */
  const top = BASE_H - BEVEL;
  const slopeFootV = 12;
  const slopeRun = (top - slopeFootV) * Math.tan(TILT);
  const profile = new THREE.Shape();
  profile.moveTo(BEVEL, BEVEL);
  profile.lineTo(D - BEVEL, BEVEL);
  profile.lineTo(D - BEVEL, slopeFootV);
  profile.lineTo(D - BEVEL - slopeRun, top);
  profile.lineTo(BEVEL, top);
  profile.lineTo(BEVEL, BEVEL);
  const extrude = W - BEVEL * 2;
  const housingGeo = keep(
    new THREE.ExtrudeGeometry(profile, { depth: extrude, bevelEnabled: true, bevelSize: BEVEL, bevelThickness: BEVEL, bevelSegments: 5 }),
  );
  // Perfil (u, v, extrusión) → mundo (z, y, x): u hacia el frente, extrusión al ancho.
  housingGeo.translate(0, 0, -extrude / 2);
  housingGeo.rotateY(-Math.PI / 2);
  housingGeo.translate(0, 0, -D / 2);
  const housing = new THREE.Mesh(housingGeo, bodyMat);
  housing.castShadow = true;
  housing.receiveShadow = true;
  root.add(housing);

  // Zócalo metálico, apenas más ancho que la carcasa.
  const plinth = new THREE.Mesh(keep(box(W + 0.6, 12, D + 0.6, 3)), metal);
  plinth.position.set(0, 6, 0);
  plinth.castShadow = true;
  root.add(plinth);

  /* ── pendiente frontal: placa con la pantalla táctil ── */
  const sz0 = D / 2 - BEVEL;
  const slopeA = new THREE.Vector3(0, slopeFootV, sz0);
  const slopeB = new THREE.Vector3(0, top, sz0 - slopeRun);
  const along = slopeB.clone().sub(slopeA).normalize();
  const outward = new THREE.Vector3(0, Math.sin(TILT), Math.cos(TILT));
  const slopeLen = slopeA.distanceTo(slopeB);
  const plateW = W - 14;
  const plateH = slopeLen - 6;
  const PX = 6;
  const SCREEN_W = 95 * PX;
  const SCREEN_H = 54 * PX;
  const START_MC = 12.4;
  const FLOOR_MC = 0.8;
  const START_G = 5;
  let screenOn = false;
  let mc = START_MC;
  let temp = 25;
  let clock = 0;
  let finished = false;
  let history: number[] = [];
  const drawPlate: Draw = (g, w, h) => {
    g.fillStyle = '#b4bcc5';
    g.fillRect(0, 0, w, h);
    g.textBaseline = 'middle';
    g.fillStyle = '#00539f';
    g.font = `800 52px ${SANS}`;
    g.fillText('OHAUS', 40, h / 2 - 10);
    g.fillStyle = '#6a7078';
    g.font = `500 22px ${SANS}`;
    g.fillText('Moisture Analyzer', 42, h / 2 + 30);
    g.textAlign = 'right';
    g.fillStyle = '#2b2f35';
    g.font = `700 44px ${SANS}`;
    g.fillText('MB120', w - 40, h / 2 - 10);
    g.fillStyle = '#6a7078';
    g.font = `500 22px ${SANS}`;
    g.fillText('Halogen', w - 42, h / 2 + 30);
    g.textAlign = 'left';

    const bx = (w - SCREEN_W) / 2;
    const by = (h - SCREEN_H) / 2;
    g.fillStyle = '#0c0e11';
    g.beginPath();
    g.roundRect(bx - 28, by - 24, SCREEN_W + 56, SCREEN_H + 48, 22);
    g.fill();
    if (!screenOn) {
      g.fillStyle = '#12161b';
      g.fillRect(bx, by, SCREEN_W, SCREEN_H);
      return;
    }
    g.save();
    g.beginPath();
    g.rect(bx, by, SCREEN_W, SCREEN_H);
    g.clip();
    g.fillStyle = '#f2f5f8';
    g.fillRect(bx, by, SCREEN_W, SCREEN_H);
    g.fillStyle = '#00539f';
    g.fillRect(bx, by, SCREEN_W, 50);
    g.fillStyle = '#fff';
    g.font = `600 27px ${SANS}`;
    g.fillText(finished ? 'Listo' : 'Secando', bx + 20, by + 26);
    g.textAlign = 'right';
    const mm = Math.floor(clock / 60);
    const ss = Math.floor(clock % 60);
    g.fillText(`${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}`, bx + SCREEN_W - 20, by + 26);
    g.textAlign = 'left';
    // Humedad restante, la cifra que baja.
    const value = mc.toFixed(2);
    g.fillStyle = '#10151b';
    g.font = `800 112px ${SANS}`;
    g.fillText(value, bx + 22, by + 138);
    const valueW = g.measureText(value).width;
    g.fillStyle = '#00539f';
    g.font = `700 44px ${SANS}`;
    g.fillText('%', bx + 22 + valueW + 10, by + 146);
    g.fillStyle = '#5b6470';
    g.font = `500 24px ${SANS}`;
    g.fillText('Humedad', bx + 24, by + 186);
    // Masa y temperatura.
    const grams = (START_G * (1 - START_MC / 100)) / (1 - mc / 100);
    g.textAlign = 'right';
    g.fillStyle = '#10151b';
    g.font = `700 36px ${SANS}`;
    g.fillText(`${grams.toFixed(3)} g`, bx + SCREEN_W - 22, by + 96);
    g.fillStyle = '#c2410c';
    g.fillText(`${Math.round(temp)} °C`, bx + SCREEN_W - 22, by + 146);
    g.textAlign = 'left';
    // Curva de secado.
    const px = bx + 24;
    const py = by + 206;
    const pw = SCREEN_W - 48;
    const ph = 92;
    g.fillStyle = '#e1e7ee';
    g.fillRect(px, py, pw, ph);
    if (history.length > 1) {
      g.strokeStyle = '#00539f';
      g.lineWidth = 4;
      g.beginPath();
      history.forEach((v, i) => {
        const x = px + (i / 59) * pw;
        const y = py + ph - (v / START_MC) * (ph - 8) - 4;
        if (i === 0) g.moveTo(x, y);
        else g.lineTo(x, y);
      });
      g.stroke();
    }
    g.restore();
  };
  const screen = canvasTexture(Math.round(plateW * PX), Math.round(plateH * PX), drawPlate);
  keep(screen.texture);
  const plateMat = keep(
    new THREE.MeshPhysicalMaterial({
      map: screen.texture,
      emissive: 0xffffff,
      emissiveMap: screen.texture,
      emissiveIntensity: 0,
      roughness: 0.55,
      clearcoat: 0.15,
      clearcoatRoughness: 0.5,
      envMapIntensity: 0.4,
    }),
  );
  const plate = new THREE.Mesh(keep(new THREE.PlaneGeometry(plateW, plateH)), plateMat);
  // El perfil redondeado queda BEVEL fuera de la recta: la placa se apoya ahí.
  const mid = slopeA.clone().add(slopeB).multiplyScalar(0.5).addScaledVector(outward, BEVEL + 0.4);
  plate.position.copy(mid);
  plate.up.copy(along);
  plate.lookAt(mid.clone().add(outward));
  root.add(plate);

  /* ── cámara de secado: aro, soporte y plato ── */
  const BASE_TOP = BASE_H;
  const ring = new THREE.Mesh(
    keep(lathe([[66, 0], [74, 0], [74, 8], [72.5, 9.5], [66, 9.5], [66, 0]], 96)),
    alu,
  );
  ring.position.set(0, BASE_TOP, PAN_Z);
  root.add(ring);
  const floor = new THREE.Mesh(keep(new THREE.CircleGeometry(66, 64)), dark);
  floor.rotation.x = -Math.PI / 2;
  floor.position.set(0, BASE_TOP + 0.3, PAN_Z);
  root.add(floor);
  const post = new THREE.Mesh(keep(new THREE.CylinderGeometry(6, 7, 6, 24)), metal);
  post.position.set(0, BASE_TOP + 3, PAN_Z);
  root.add(post);
  const support = new THREE.Mesh(keep(new THREE.CylinderGeometry(30, 30, 1.6, 48)), metal);
  support.position.set(0, BASE_TOP + 6.8, PAN_Z);
  root.add(support);
  const PAN_Y = BASE_TOP + 7.6;
  const pan = new THREE.Mesh(
    keep(lathe([[0, 0], [40, 0], [43.2, 1.6], [45, 8.4], [46, 9.8], [45, 10.2], [43.9, 9], [42.2, 2.4], [0, 2.4]], 96)),
    panMat,
  );
  pan.castShadow = true;
  pan.receiveShadow = true;
  pan.position.set(0, PAN_Y, PAN_Z);
  root.add(pan);
  // Muestra: polvo que se aclara al secar.
  const damp = new THREE.Color(0x9a7a50);
  const dry = new THREE.Color(0xe6d8ae);
  const sampleMat = keep(new THREE.MeshStandardMaterial({ color: damp.clone(), roughness: 0.95, side: THREE.DoubleSide }));
  const sample = new THREE.Mesh(
    keep(lathe([[0, 6.4], [12, 6], [24, 4.6], [32, 3.2], [33, 2.4], [0, 2.4]], 48)),
    sampleMat,
  );
  sample.position.set(0, PAN_Y, PAN_Z);
  root.add(sample);
  const plateTop = new THREE.Object3D();
  plateTop.position.set(0, PAN_Y + 9, PAN_Z);
  root.add(plateTop);

  /* ── tapa: casco, faldón, reflector y lámpara, en un grupo que gira ── */
  const lidPivot = new THREE.Group();
  lidPivot.position.set(0, BASE_H, HINGE_Z);
  root.add(lidPivot);
  const lid = new THREE.Group();
  lidPivot.add(lid);
  const lidZ0 = 2;
  const lidZc = lidZ0 + LID_D / 2;
  const cap = new THREE.Mesh(keep(box(W, LID_H - SKIRT, LID_D, 9)), bodyMat);
  cap.position.set(0, SKIRT + (LID_H - SKIRT) / 2, lidZc);
  cap.castShadow = true;
  cap.receiveShadow = true;
  lid.add(cap);
  const wall = 7;
  const front = new THREE.Mesh(keep(new THREE.BoxGeometry(W, SKIRT, wall)), bodyMat);
  front.position.set(0, SKIRT / 2, lidZ0 + LID_D - wall / 2);
  const rear = new THREE.Mesh(front.geometry, bodyMat);
  rear.position.set(0, SKIRT / 2, lidZ0 + wall / 2);
  const sideGeo = keep(new THREE.BoxGeometry(wall, SKIRT, LID_D));
  const left = new THREE.Mesh(sideGeo, bodyMat);
  left.position.set(-(W / 2 - wall / 2), SKIRT / 2, lidZc);
  const right = new THREE.Mesh(sideGeo, bodyMat);
  right.position.set(W / 2 - wall / 2, SKIRT / 2, lidZc);
  for (const piece of [front, rear, left, right]) {
    piece.castShadow = true;
    lid.add(piece);
  }
  // Asa oscura en el frente de la tapa.
  const handle = new THREE.Mesh(keep(box(110, 10, 7, 3)), dark);
  handle.position.set(0, SKIRT + 18, lidZ0 + LID_D + 1);
  lid.add(handle);

  const reflector = new THREE.Mesh(
    keep(lathe([[0, SKIRT - 1], [58, SKIRT - 1.6], [78, SKIRT - 0.6], [79, SKIRT]], 72)),
    reflectorMat,
  );
  reflector.position.set(0, 0, lidZc);
  lid.add(reflector);
  const lampGeo = keep(new THREE.TorusGeometry(40, 3.6, 16, 72));
  lampGeo.rotateX(Math.PI / 2);
  const lamp = new THREE.Mesh(lampGeo, lampMat);
  lamp.position.set(0, SKIRT - 7, lidZc);
  lid.add(lamp);
  // Los dos casquillos de la lámpara, en el borde trasero del aro.
  for (const sx of [-1, 1]) {
    const cup = new THREE.Mesh(keep(new THREE.CylinderGeometry(3.8, 3.8, 9, 16)), metal);
    cup.position.set(sx * 11, SKIRT - 4, lidZc - 40);
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
  glow.position.set(0, SKIRT - 14, lidZc);
  lid.add(glow);

  // Bisagras traseras.
  for (const sx of [-1, 1]) {
    const hinge = new THREE.Mesh(keep(new THREE.CylinderGeometry(5.5, 5.5, 34, 24)), metal);
    hinge.rotation.z = Math.PI / 2;
    hinge.position.set(sx * 70, BASE_H + 1, HINGE_Z + 7);
    root.add(hinge);
  }

  /* ── anclas y vistas ── */
  const screenAnchor = new THREE.Object3D();
  screenAnchor.position.copy(mid);
  root.add(screenAnchor);
  const lidAnchor = new THREE.Object3D();
  lidAnchor.position.set(0, LID_H + 2, lidZc);
  lid.add(lidAnchor);
  const lampAnchor = new THREE.Object3D();
  lampAnchor.position.copy(lamp.position);
  lid.add(lampAnchor);
  // Punto entre la lámpara abierta y el plato: lo que mira la vista `tapa`.
  const chamber = new THREE.Object3D();
  chamber.position.set(0, 150, -140);
  root.add(chamber);

  root.userData.lidAngle = 0;
  let lidOverride: number | null = null;

  let powered = false;
  let readout: number | null = null;
  let lastScreen = 0;
  let angle = 0;
  let lampK = 0;
  let hold = 0;
  let historyClock = 0;
  let wasOn = false;

  return {
    root,
    anchors: { pantalla: screenAnchor, tapa: lidAnchor, lampara: lampAnchor, plato: plateTop },
    views: {
      pantalla: { target: screenAnchor, direction: outward.clone().add(new THREE.Vector3(0, 0.12, 0)), distance: 250 },
      tapa: { target: chamber, direction: new THREE.Vector3(0.3, 0.4, 1), distance: 560 },
    },
    setPower(on) {
      powered = on;
      if (on) screenOn = true;
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

      // Secado: la humedad baja mientras hay marcha, hasta el piso; luego reinicia.
      if (screenOn) {
        if (powered && readout !== null) {
          mc = Math.min(99, Math.max(0, readout));
          finished = false;
        } else if (powered && level > 0.05) {
          if (finished) {
            hold += dt;
            if (hold > 2.5) {
              mc = START_MC;
              clock = 0;
              hold = 0;
              finished = false;
              history = [];
            }
          } else {
            mc = FLOOR_MC + (mc - FLOOR_MC) * Math.exp(-dt * 0.3 * level);
            clock += dt * level;
            if (mc < FLOOR_MC + 0.12) finished = true;
          }
        }
        temp += ((powered ? 105 : 25) - temp) * Math.min(1, dt * 0.5);
        historyClock += dt;
        if (historyClock > 0.4) {
          historyClock = 0;
          if (powered && !finished) history.push(mc);
          if (history.length > 60) history.shift();
        }
        sampleMat.color.copy(dry).lerp(damp, Math.min(1, Math.max(0, mc / START_MC)));
      }

      // Pantalla: se apaga al soltar la marcha y vuelve al inicio.
      if (screenOn && !powered && level < 0.02) {
        screenOn = false;
        mc = START_MC;
        temp = 25;
        clock = 0;
        finished = false;
        hold = 0;
        history = [];
        sampleMat.color.copy(damp);
        screen.redraw();
        wasOn = false;
      } else if (screenOn && (t - lastScreen > 1 / 20 || !wasOn)) {
        lastScreen = t;
        wasOn = true;
        screen.redraw();
      }
      plateMat.emissiveIntensity = screenOn ? 0.5 : 0;

      // Lámpara: brillo cálido con un parpadeo mínimo de filamento.
      const flick = 0.96 + 0.04 * Math.sin(t * 47) * Math.sin(t * 13);
      // Sólo con la tapa abierta y el equipo encendido.
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
