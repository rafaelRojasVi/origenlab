/**
 * Hielscher UP200St: sistema de dos piezas, generador plano + transductor.
 *
 * Fuente de las medidas: fichas de Faust (generador UP200St-G y transductor
 * UP200St-T) y la página del producto en hielscher.com; resumidas en
 * `3d_assets.md`. Todo está en milímetros; la escena lo reduce al final.
 *
 *   generador    343 de ancho × 170 de fondo × 75 de alto, plateado, con la
 *                pantalla táctil en un plano inclinado al frente
 *   transductor  Ø 45 × 230, titanio, con el cable al generador y dos luces
 *                de muestra en la base
 *   sonotrodo    Ø 14 × 80 hacia abajo (el S26d14 de la lista), a 25 mm del
 *                fondo del vaso
 *   soporte      ST1-16: base de 300 × 150 y varilla Ø 16 de 500 de alto
 *
 * El vaso y su muestra no salen de las fichas: son la puesta en escena, a una
 * escala verosímil. El color plateado sale del nombre de la imagen oficial
 * (`UP200St_silver_cut.png`); la pantalla repite el tipo de la del UP400St.
 *
 * Nada de este archivo decide qué se muestra en la página: sólo construye el
 * objeto y expone las piezas que la escena necesita animar o señalar.
 */
import * as THREE from 'three';
import type { BuildOptions, BuiltModel } from '../types';
import { SANS, canvasTexture, disposer, labGlass, lathe, roundedBox } from './kit';

// Generador.
const W = 343;
const D = 170;
const H = 75;
const BEVEL = 4;
const FEET = 4;
// Transductor y soporte (el eje del transductor es el origen del grupo).
const HORN_LEN = 80;
const TRANSDUCER_LEN = 230;
const TIP_Y = 42;
const TRANSDUCER_Y = TIP_Y + HORN_LEN;
const POST_X = 120;
const BASE_H = 14;

export function build(_options: BuildOptions = {}): BuiltModel {
  const root = new THREE.Group();
  const { keep, dispose: disposeAll } = disposer();

  /* ── materiales ── */
  const silver = keep(
    new THREE.MeshPhysicalMaterial({ color: 0xcdd1d5, metalness: 0.85, roughness: 0.36, clearcoat: 0.3, clearcoatRoughness: 0.35 }),
  );
  const steel = keep(new THREE.MeshPhysicalMaterial({ color: 0xd4d8dc, metalness: 1, roughness: 0.24 }));
  const titanium = keep(new THREE.MeshPhysicalMaterial({ color: 0xb9bdc0, metalness: 1, roughness: 0.34, envMapIntensity: 1.2 }));
  const alu = keep(new THREE.MeshPhysicalMaterial({ color: 0xdfe2e4, metalness: 0.9, roughness: 0.34 }));
  const black = keep(new THREE.MeshStandardMaterial({ color: 0x15171a, roughness: 0.6 }));
  const rubber = keep(new THREE.MeshStandardMaterial({ color: 0x101113, roughness: 0.9 }));
  const ledMat = keep(new THREE.MeshStandardMaterial({ color: 0xdfe8ee, emissive: 0xcfe8ff, emissiveIntensity: 0, roughness: 0.3 }));

  /* ═════════ generador ═════════ */
  const generatorGroup = new THREE.Group();
  generatorGroup.name = 'generatorGroup';
  root.add(generatorGroup);
  const body = new THREE.Group();
  body.position.y = FEET;
  generatorGroup.add(body);

  // Perfil lateral (x = fondo desde el frente, y = alto), extruido al ancho. El
  // contorno crece BEVEL hacia afuera en los costados, de modo que las medidas
  // finales son 343 × 170 × 75.
  const profile = new THREE.Shape();
  profile.moveTo(9, 4);
  profile.lineTo(161, 4);
  profile.quadraticCurveTo(166, 4, 166, 9);
  profile.lineTo(166, 66);
  profile.quadraticCurveTo(166, 71, 161, 71);
  profile.lineTo(125, 71);
  profile.quadraticCurveTo(118, 71, 111.4, 68.6);
  profile.lineTo(10.6, 32.4);
  profile.quadraticCurveTo(4, 30, 4, 25);
  profile.lineTo(4, 9);
  profile.quadraticCurveTo(4, 4, 9, 4);
  const housingGeo = keep(
    new THREE.ExtrudeGeometry(profile, {
      depth: W - BEVEL * 2,
      bevelEnabled: true,
      bevelSize: BEVEL,
      bevelThickness: BEVEL,
      bevelSegments: 5,
      curveSegments: 16,
    }),
  );
  housingGeo.translate(0, 0, -(W - BEVEL * 2) / 2);
  // x del perfil -> z del mundo (el frente mira a +z).
  housingGeo.rotateY(Math.PI / 2);
  housingGeo.translate(0, 0, D / 2);
  const housing = new THREE.Mesh(housingGeo, silver);
  housing.castShadow = true;
  housing.receiveShadow = true;
  body.add(housing);

  // Cuatro patas de goma.
  {
    const footGeo = keep(new THREE.CylinderGeometry(7, 8, FEET, 20));
    for (const [x, z] of [[-148, 62], [148, 62], [-148, -62], [148, -62]] as const) {
      const foot = new THREE.Mesh(footGeo, rubber);
      foot.position.set(x, FEET / 2, z);
      generatorGroup.add(foot);
    }
  }

  /* pantalla táctil, en el plano inclinado del frente */
  const slopeA = new THREE.Vector2(4, 30);
  const slopeB = new THREE.Vector2(118, 71);
  const slopeLen = slopeA.distanceTo(slopeB);
  const slopeDir = slopeB.clone().sub(slopeA).normalize();
  // Del perfil al mundo: z = D/2 - x.
  const along = new THREE.Vector3(0, slopeDir.y, -slopeDir.x);
  const outward = new THREE.Vector3(0, slopeDir.x, slopeDir.y); // (-y, x) del perfil -> (y: x, z: +y)
  const slopeMid = new THREE.Vector3(0, (slopeA.y + slopeB.y) / 2, D / 2 - (slopeA.x + slopeB.x) / 2).addScaledVector(outward, BEVEL + 0.25);

  let screenOn = false;
  let amplitude = 0;
  let bars = 0;
  const SW = 1280;
  const SH = 424;
  const screen = canvasTexture(SW, SH, (g, w, h) => {
    g.fillStyle = '#c4c9cf';
    g.fillRect(0, 0, w, h);
    g.strokeStyle = '#3d6fb6';
    g.lineWidth = 6;
    g.beginPath();
    g.roundRect(14, 14, w - 28, h - 28, 36);
    g.stroke();
    const dx = 60;
    const dy = 36;
    const dw = 740;
    const dh = 352;
    g.fillStyle = '#0b0e12';
    g.beginPath();
    g.roundRect(dx - 12, dy - 12, dw + 24, dh + 24, 16);
    g.fill();
    if (screenOn) {
      g.fillStyle = '#0a2342';
      g.fillRect(dx, dy, dw, dh);
      g.fillStyle = '#7fb2ff';
      g.font = `600 28px ${SANS}`;
      g.fillText('UP200St', dx + 24, dy + 44);
      g.textAlign = 'right';
      g.fillText('26 kHz', dx + dw - 24, dy + 44);
      g.textAlign = 'left';
      g.fillStyle = '#fff';
      g.font = `700 140px ${SANS}`;
      g.fillText(`${Math.round(amplitude)}%`, dx + 22, dy + 190);
      g.fillStyle = '#7fb2ff';
      g.font = `500 26px ${SANS}`;
      g.fillText('Amplitud', dx + 28, dy + 228);
      // Dos cursores, de amplitud y de pulso.
      const slider = (y: number, label: string, v: number) => {
        g.fillStyle = '#9ec1f7';
        g.font = `500 22px ${SANS}`;
        g.fillText(label, dx + 400, y - 16);
        g.fillStyle = '#1c3b66';
        g.beginPath();
        g.roundRect(dx + 400, y, 300, 14, 7);
        g.fill();
        g.fillStyle = '#34d399';
        g.beginPath();
        g.roundRect(dx + 400, y, 300 * v, 14, 7);
        g.fill();
        g.fillStyle = '#fff';
        g.beginPath();
        g.arc(dx + 400 + 300 * v, y + 7, 15, 0, Math.PI * 2);
        g.fill();
      };
      slider(dy + 110, 'Amplitud', Math.max(0, Math.min(1, amplitude / 100)));
      slider(dy + 185, 'Pulso', 1);
      for (let i = 0; i < 28; i++) {
        const v = 0.45 + 0.4 * Math.sin(bars * 6 + i * 0.7) * Math.sin(bars * 2.3 + i * 0.3);
        g.fillStyle = i < 20 ? '#34d399' : '#fbbf24';
        g.fillRect(dx + 28 + i * 25, dy + dh - 20 - v * 70, 17, v * 70);
      }
    } else {
      g.fillStyle = '#11161c';
      g.fillRect(dx, dy, dw, dh);
    }
    // Rótulo y botón de encendido, a la derecha de la pantalla.
    g.fillStyle = '#fff';
    g.beginPath();
    g.roundRect(900, 56, 300, 74, 12);
    g.fill();
    g.strokeStyle = '#3d6fb6';
    g.lineWidth = 4;
    g.stroke();
    g.fillStyle = '#2c4f8f';
    g.font = `600 44px ${SANS}`;
    g.textAlign = 'center';
    g.fillText('hielscher', 1050, 107);
    g.textAlign = 'left';
    g.fillStyle = '#3d6fb6';
    g.beginPath();
    g.arc(1050, 272, 58, 0, Math.PI * 2);
    g.fill();
    g.strokeStyle = '#fff';
    g.lineWidth = 8;
    g.lineCap = 'round';
    g.beginPath();
    g.arc(1050, 272, 24, -Math.PI * 0.35, Math.PI * 1.35 + Math.PI * 0.0);
    g.stroke();
    g.beginPath();
    g.moveTo(1050, 238);
    g.lineTo(1050, 268);
    g.stroke();
  });
  keep(screen.texture);
  const bezelMat = keep(
    new THREE.MeshPhysicalMaterial({
      map: screen.texture,
      emissive: 0xffffff,
      emissiveMap: screen.texture,
      emissiveIntensity: 0,
      roughness: 0.25,
      clearcoat: 1,
      clearcoatRoughness: 0.08,
    }),
  );
  const bezel = new THREE.Mesh(keep(new THREE.PlaneGeometry(W - 20, slopeLen - 14)), bezelMat);
  bezel.position.copy(slopeMid);
  bezel.up.copy(along);
  bezel.lookAt(slopeMid.clone().add(outward));
  body.add(bezel);

  // Rótulo sobre la tapa plana, atrás.
  {
    const label = canvasTexture(560, 120, (g, w, h) => {
      g.clearRect(0, 0, w, h);
      g.fillStyle = '#2f3541';
      g.font = `700 52px ${SANS}`;
      g.fillText('UP200St', 12, 56);
      g.fillStyle = '#5d6571';
      g.font = `400 24px ${SANS}`;
      g.fillText('ultrasonic processor · generator', 14, 98);
      g.fillStyle = '#2c4f8f';
      g.font = `600 30px ${SANS}`;
      g.textAlign = 'right';
      g.fillText('hielscher', w - 12, 50);
    });
    keep(label.texture);
    const plate = new THREE.Mesh(
      keep(new THREE.PlaneGeometry(140, 30)),
      keep(new THREE.MeshStandardMaterial({ map: label.texture, transparent: true, roughness: 0.5, metalness: 0.2, polygonOffset: true, polygonOffsetFactor: -2 })),
    );
    plate.rotation.x = -Math.PI / 2;
    plate.position.set(-92, H + 0.2, D / 2 - 148);
    body.add(plate);
  }

  // Ranura de la tarjeta SD, al frente.
  {
    const slot = new THREE.Mesh(keep(new THREE.BoxGeometry(26, 2.6, 1.2)), black);
    slot.position.set(-130, 13, D / 2 + 0.2);
    body.add(slot);
  }
  // Atrás: entrada de red y puerto LAN.
  {
    const iec = new THREE.Mesh(keep(roundedBox(32, 22, 3, 1)), black);
    iec.position.set(110, 36, -D / 2 - 0.4);
    body.add(iec);
    const lan = new THREE.Mesh(keep(new THREE.BoxGeometry(15, 12, 2)), black);
    lan.position.set(40, 30, -D / 2 - 0.4);
    body.add(lan);
  }
  // A la derecha: toma del transductor.
  const socket = new THREE.Group();
  socket.position.set(W / 2 + 0.5, 38, 46);
  body.add(socket);
  {
    const ring = new THREE.Mesh(keep(new THREE.CylinderGeometry(10, 10, 3, 32)), steel);
    ring.rotation.z = Math.PI / 2;
    socket.add(ring);
    const plug = new THREE.Mesh(keep(new THREE.CylinderGeometry(7, 7, 14, 24)), black);
    plug.rotation.z = Math.PI / 2;
    plug.position.x = 7;
    socket.add(plug);
  }

  /* ═════════ transductor sobre el soporte ST1-16 ═════════ */
  const transducerGroup = new THREE.Group();
  transducerGroup.name = 'transducerGroup';
  transducerGroup.position.set(345, 0, 0);
  root.add(transducerGroup);

  // Soporte: base, varilla, brazo y mordaza.
  const stand = new THREE.Group();
  transducerGroup.add(stand);
  {
    const base = new THREE.Mesh(keep(roundedBox(294, BASE_H - 6, 150, 3)), alu);
    base.position.set(10, BASE_H / 2, 0);
    base.castShadow = true;
    base.receiveShadow = true;
    stand.add(base);
    const post = new THREE.Mesh(keep(new THREE.CylinderGeometry(8, 8, 500 - BASE_H, 32)), steel);
    post.position.set(POST_X, BASE_H + (500 - BASE_H) / 2, 0);
    post.castShadow = true;
    stand.add(post);
    const ARM_Y = 270;
    const collar = new THREE.Mesh(keep(roundedBox(18, 22, 26, 4)), black);
    collar.position.set(POST_X, ARM_Y, 0);
    stand.add(collar);
    const arm = new THREE.Mesh(keep(new THREE.CylinderGeometry(5, 5, POST_X - 20, 20)), steel);
    arm.rotation.z = Math.PI / 2;
    arm.position.set(POST_X / 2 + 10, ARM_Y, 0);
    stand.add(arm);
    const clamp = new THREE.Mesh(keep(new THREE.CylinderGeometry(30, 30, 34, 40)), black);
    clamp.position.set(0, ARM_Y, 0);
    stand.add(clamp);
    const band = new THREE.Mesh(keep(new THREE.CylinderGeometry(31, 31, 4, 40)), alu);
    band.position.set(0, ARM_Y + 12, 0);
    stand.add(band);
  }

  // Transductor y sonotrodo torneados.
  const transducer = new THREE.Group();
  transducer.position.y = TRANSDUCER_Y;
  transducerGroup.add(transducer);
  const bodyMesh = new THREE.Mesh(
    keep(
      lathe([
        [0, 0], [11, 0], [11, 6], [20, 10], [22.5, 14], [22.5, 100], [21.4, 101], [21.4, 105], [22.5, 106], [22.5, 214],
        [20, 222], [14, 228], [14, TRANSDUCER_LEN], [0, TRANSDUCER_LEN],
      ]),
    ),
    titanium,
  );
  bodyMesh.castShadow = true;
  transducer.add(bodyMesh);
  {
    // Conector del cable, arriba.
    const nut = new THREE.Mesh(keep(new THREE.CylinderGeometry(9, 9, 10, 24)), steel);
    nut.position.y = TRANSDUCER_LEN + 5;
    transducer.add(nut);
    const sleeve = new THREE.Mesh(keep(new THREE.CylinderGeometry(6.5, 7.5, 14, 24)), black);
    sleeve.position.y = TRANSDUCER_LEN + 17;
    transducer.add(sleeve);
    // Banda de goma bajo el collarín de la mordaza.
    const grip = new THREE.Mesh(keep(new THREE.CylinderGeometry(23, 23, 3, 40)), rubber);
    grip.position.y = 150;
    transducer.add(grip);
    // Dos luces de muestra en la base.
    const ledGeo = keep(new THREE.CylinderGeometry(2.6, 2.6, 1.2, 16));
    for (const a of [Math.PI * 0.5, Math.PI * 1.5]) {
      const led = new THREE.Mesh(ledGeo, ledMat);
      led.position.set(Math.cos(a) * 16, 8, Math.sin(a) * 16);
      led.rotation.set(Math.sin(a) * 0.5, 0, -Math.cos(a) * 0.5);
      transducer.add(led);
    }
  }

  // Las puntas vibran juntas: el temblor se aplica al grupo del sonotrodo.
  const horn = new THREE.Group();
  horn.position.y = TRANSDUCER_Y;
  transducerGroup.add(horn);
  const hornMesh = new THREE.Mesh(
    keep(lathe([[0, 0], [10, 0], [10, -6], [8.5, -7], [8.5, -30], [7, -34], [7, -HORN_LEN], [0, -HORN_LEN]], 48)),
    titanium,
  );
  hornMesh.castShadow = true;
  horn.add(hornMesh);

  /* ── vaso y muestra ── */
  const BEAKER_BOTTOM = BASE_H + 2.5;
  const LIQUID_TOP = BASE_H + 70;
  const beaker = new THREE.Mesh(
    keep(lathe([[0, 0], [38, 0], [40, 2], [40, 96], [41.5, 98], [41.5, 99], [39.5, 99], [39.5, 3], [38, 2.5], [0, 2.5]])),
    keep(labGlass()),
  );
  beaker.position.y = BASE_H;
  beaker.renderOrder = 3;
  transducerGroup.add(beaker);
  const liquid = new THREE.Mesh(
    keep(lathe([[0, 2.6], [37.8, 2.6], [39.3, 4], [39.3, 70], [0, 70]])),
    keep(
      new THREE.MeshPhysicalMaterial({
        color: 0xbfe3ee,
        roughness: 0.15,
        clearcoat: 0.6,
        emissive: 0x6fb3c9,
        emissiveIntensity: 0.12,
        transparent: true,
        opacity: 0.7,
        depthWrite: false,
        side: THREE.DoubleSide,
      }),
    ),
  );
  liquid.position.y = BASE_H;
  liquid.renderOrder = 1;
  transducerGroup.add(liquid);
  const surface = new THREE.Mesh(
    keep(new THREE.CircleGeometry(38.9, 64).rotateX(-Math.PI / 2)),
    keep(new THREE.MeshPhysicalMaterial({ color: 0xd6eef5, roughness: 0.05, clearcoat: 1, transparent: true, opacity: 0.55, depthWrite: false })),
  );
  surface.position.y = LIQUID_TOP + 0.05;
  surface.renderOrder = 1;
  transducerGroup.add(surface);

  /* ── anillos de emisión bajo la punta ── */
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
  glow.scale.set(46, 34, 1);
  glow.position.set(0, TIP_Y - 5, 0);
  glow.renderOrder = 2;
  transducerGroup.add(glow);
  const ringGeo = keep(new THREE.RingGeometry(6.2, 7.6, 72).rotateX(-Math.PI / 2));
  const rings = [0, 0.5].map(() => {
    const mat = keep(
      new THREE.MeshBasicMaterial({ color: 0xb6e4ff, blending: THREE.AdditiveBlending, transparent: true, opacity: 0, depthWrite: false, side: THREE.DoubleSide }),
    );
    const mesh = new THREE.Mesh(ringGeo, mat);
    mesh.position.set(0, TIP_Y - 1, 0);
    mesh.renderOrder = 2;
    transducerGroup.add(mesh);
    return mesh;
  });

  /* ── cable del transductor al generador ── */
  {
    const curve = new THREE.CatmullRomCurve3([
      new THREE.Vector3(0, TRANSDUCER_Y + TRANSDUCER_LEN + 24, 0),
      new THREE.Vector3(0, TRANSDUCER_Y + TRANSDUCER_LEN + 46, 4),
      new THREE.Vector3(-30, TRANSDUCER_Y + TRANSDUCER_LEN + 58, 14),
      new THREE.Vector3(-80, 370, 34),
      new THREE.Vector3(-125, 220, 50),
      new THREE.Vector3(-152, 100, 54),
      new THREE.Vector3(-168, 56, 50),
      // La toma del generador: x del mundo = W/2 + 14, que aquí es x - 345.
      new THREE.Vector3(W / 2 + 14 - 345, FEET + 38, 46),
    ]);
    const cable = new THREE.Mesh(keep(new THREE.TubeGeometry(curve, 72, 3, 10, false)), rubber);
    cable.castShadow = true;
    transducerGroup.add(cable);
  }

  /* ── anclas y vistas ── */
  const screenAnchor = new THREE.Object3D();
  screenAnchor.position.copy(slopeMid);
  body.add(screenAnchor);
  const generatorAnchor = new THREE.Object3D();
  generatorAnchor.position.set(0, H / 2, 0);
  body.add(generatorAnchor);
  const tip = new THREE.Object3D();
  tip.position.set(0, TIP_Y + 16, 7);
  transducerGroup.add(tip);
  const transducerAnchor = new THREE.Object3D();
  transducerAnchor.position.set(0, TRANSDUCER_Y + 175, 22.5);
  transducerGroup.add(transducerAnchor);
  const standAnchor = new THREE.Object3D();
  standAnchor.position.set(POST_X, 340, 8);
  transducerGroup.add(standAnchor);

  let powered = false;
  let lastScreen = 0;
  let readout: number | null = null;

  return {
    root,
    anchors: {
      screen: screenAnchor,
      generator: generatorAnchor,
      tip,
      transducer: transducerAnchor,
      stand: standAnchor,
    },
    views: {
      pantalla: { target: screenAnchor, direction: outward.clone().add(new THREE.Vector3(0, 0.2, 0.15)), distance: 430 },
      sonotrodo: { target: tip, direction: new THREE.Vector3(0.45, 0.2, 1), distance: 280 },
      transductor: { target: transducerAnchor, direction: new THREE.Vector3(0.3, 0.1, 1), distance: 620 },
    },
    frameTop: 400,
    yaw: 0.45,
    setPower(on) {
      powered = on;
      if (on) screenOn = true;
    },
    setReadout(percent) {
      readout = percent;
    },
    update(_dt, t, level) {
      if (screenOn && t - lastScreen > 1 / 20) {
        lastScreen = t;
        bars = t;
        if (readout !== null && powered) amplitude = readout;
        else amplitude += ((powered ? 70 + 6 * Math.sin(t * 0.8) : 0) - amplitude) * 0.08;
        if (!powered && level < 0.02) screenOn = false;
        screen.redraw();
      }
      // La pantalla encendida tiene luz propia aunque el sonotrodo esté quieto.
      bezelMat.emissiveIntensity = screenOn ? 0.3 + 0.25 * level : 0;
      ledMat.emissiveIntensity = powered ? 2.2 : 0;

      horn.position.x = (Math.random() - 0.5) * 0.25 * level;
      glowMat.opacity = 0.55 * level * (0.8 + Math.random() * 0.2);
      for (let i = 0; i < rings.length; i++) {
        const p = (t * 0.8 + i * 0.5) % 1;
        const mat = rings[i].material as THREE.MeshBasicMaterial;
        mat.opacity = 0.45 * level * (1 - p) * Math.min(1, p * 8);
        rings[i].scale.setScalar(1 + p * 3.6);
        rings[i].visible = level > 0.01;
      }
    },
    dispose() {
      disposeAll();
    },
  };
}
