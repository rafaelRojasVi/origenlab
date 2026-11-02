/**
 * Hielscher UP400St, modelado a partir del plano de dimensiones oficial.
 *
 * Fuente de las medidas: el plano de tres vistas de hielscher.com (página del
 * UP400St). Todo está en milímetros; la escena lo reduce a su escala al final.
 *
 *   carcasa      184 de fondo × 183 de alto × 144 de ancho; el frente sube
 *                98 mm y luego cae en pendiente hasta 72 mm atrás, donde va
 *                la pantalla táctil.
 *   sonotrodo    cuelga 242 mm, a 40 mm del frente: Ø 40 → Ø 18 → punta de
 *                Ø 14 y 51,5 mm.
 *
 * El soporte, el matraz y la muestra no salen del plano: son la puesta en
 * escena de la fotografía oficial (soporte de varilla y matraz Erlenmeyer con
 * muestra amarilla), dibujados a una escala verosímil.
 *
 * Nada de este archivo decide qué se muestra en la página: sólo construye el
 * objeto y expone las piezas que la escena necesita animar o señalar.
 */
import * as THREE from 'three';

export interface Up400St {
  /** Todo el conjunto, en milímetros. */
  root: THREE.Group;
  /** Puntos que la escena señala con un marcador y a los que lleva la cámara. */
  anchors: {
    screen: THREE.Object3D;
    tip: THREE.Object3D;
    transducer: THREE.Object3D;
    mount: THREE.Object3D;
    body: THREE.Object3D;
  };
  /** Normales hacia fuera de la pantalla y del costado rotulado, en el marco del conjunto. */
  normals: { screen: THREE.Vector3; side: THREE.Vector3 };
  /** Avanza la animación; `level` va de 0 (apagado) a 1 (sonicando). */
  update(dt: number, t: number, level: number): void;
  setPower(on: boolean): void;
  dispose(): void;
}

const W = 144;
const D = 184;
const H = 183;
const BEVEL = 5;
const SANS = '"Plus Jakarta Sans", ui-sans-serif, system-ui, sans-serif';

type Draw = (g: CanvasRenderingContext2D, w: number, h: number) => void;

function canvasTexture(w: number, h: number, draw: Draw) {
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const g = canvas.getContext('2d')!;
  draw(g, w, h);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 8;
  return {
    texture,
    redraw() {
      draw(g, w, h);
      texture.needsUpdate = true;
    },
  };
}

function roundedBox(w: number, h: number, d: number, r: number) {
  const s = new THREE.Shape();
  const x = -w / 2;
  const y = -h / 2;
  s.moveTo(x + r, y);
  s.lineTo(x + w - r, y);
  s.quadraticCurveTo(x + w, y, x + w, y + r);
  s.lineTo(x + w, y + h - r);
  s.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  s.lineTo(x + r, y + h);
  s.quadraticCurveTo(x, y + h, x, y + h - r);
  s.lineTo(x, y + r);
  s.quadraticCurveTo(x, y, x + r, y);
  const g = new THREE.ExtrudeGeometry(s, {
    depth: d - 2 * r,
    bevelEnabled: true,
    bevelSize: r,
    bevelThickness: r,
    bevelSegments: 4,
  });
  g.translate(0, 0, -(d - 2 * r) / 2);
  return g;
}

function lathe(points: [number, number][], segments = 96) {
  return new THREE.LatheGeometry(
    points.map(([r, y]) => new THREE.Vector2(r, y)),
    segments,
  );
}

export function buildUp400St(): Up400St {
  const root = new THREE.Group();
  const disposables: { dispose(): void }[] = [];
  const keep = <T extends { dispose(): void }>(item: T) => {
    disposables.push(item);
    return item;
  };

  /* ── materiales ── */
  const housingMat = keep(
    new THREE.MeshPhysicalMaterial({ color: 0x3b4352, roughness: 0.55, metalness: 0.05, clearcoat: 0.25, clearcoatRoughness: 0.6 }),
  );
  const steel = keep(new THREE.MeshPhysicalMaterial({ color: 0xd4d8dc, metalness: 1, roughness: 0.22 }));
  const brushed = keep(new THREE.MeshPhysicalMaterial({ color: 0xc3c8cc, metalness: 1, roughness: 0.38 }));
  const titanium = keep(new THREE.MeshPhysicalMaterial({ color: 0xb9bdc0, metalness: 1, roughness: 0.3 }));
  const black = keep(new THREE.MeshStandardMaterial({ color: 0x15171a, roughness: 0.6 }));
  const rubber = keep(new THREE.MeshStandardMaterial({ color: 0x101113, roughness: 0.9 }));
  const alu = keep(new THREE.MeshPhysicalMaterial({ color: 0xdfe2e4, metalness: 0.9, roughness: 0.34 }));

  /* ── carcasa: el perfil lateral del plano, extruido al ancho ── */
  const profile = new THREE.Shape();
  profile.moveTo(6, 0);
  profile.lineTo(D - 6, 0);
  profile.quadraticCurveTo(D, 0, D, 6);
  profile.lineTo(D, 158);
  profile.quadraticCurveTo(D, H, D - 25, H);
  profile.lineTo(78, H);
  profile.quadraticCurveTo(72, H, 68, H - 5);
  profile.lineTo(2, 103);
  profile.quadraticCurveTo(0, 100, 0, 95);
  profile.lineTo(0, 6);
  profile.quadraticCurveTo(0, 0, 6, 0);

  const body = new THREE.Group();
  root.add(body);
  const housingGeo = keep(
    new THREE.ExtrudeGeometry(profile, {
      depth: W - BEVEL * 2,
      bevelEnabled: true,
      bevelSize: BEVEL,
      bevelThickness: BEVEL,
      bevelSegments: 6,
      curveSegments: 24,
    }),
  );
  housingGeo.translate(0, 0, -(W - BEVEL * 2) / 2);
  const housing = new THREE.Mesh(housingGeo, housingMat);
  housing.castShadow = true;
  housing.receiveShadow = true;
  body.add(housing);

  /* ── rótulos de los costados ── */
  let ledOn = false;
  const insetPath = () => {
    const p = new Path2D();
    p.moveTo(14, 12);
    p.lineTo(D - 12, 12);
    p.lineTo(D - 12, 154);
    p.quadraticCurveTo(D - 12, H - 12, D - 32, H - 12);
    p.lineTo(80, H - 12);
    p.lineTo(14, 107);
    p.closePath();
    return p;
  };
  const drawSide = (back: boolean): Draw => (g, w, h) => {
    const sx = w / D;
    const sy = h / H;
    g.clearRect(0, 0, w, h);
    g.save();
    g.scale(sx, -sy);
    g.translate(0, -H);
    const inset = insetPath();
    g.fillStyle = '#2f3541';
    g.fill(inset);
    g.save();
    g.clip(inset);
    g.fillStyle = '#c9ced6';
    g.fillRect(0, 108, D, H);
    g.restore();
    g.lineWidth = 1.2;
    g.strokeStyle = '#3d6fb6';
    g.stroke(inset);
    g.beginPath();
    g.moveTo(16, 108);
    g.lineTo(D - 13, 108);
    g.stroke();
    g.restore();

    const px = (x: number) => x * sx;
    const py = (y: number) => (H - y) * sy;
    g.save();
    // El costado de atrás se ve desde el otro lado: se espeja para que se lea.
    if (back) {
      g.translate(w, 0);
      g.scale(-1, 1);
    }
    const lx = back ? 22 : 110;
    g.fillStyle = '#fff';
    g.beginPath();
    g.roundRect(px(lx), py(160), px(52), sy * 13, 6);
    g.fill();
    g.strokeStyle = '#3d6fb6';
    g.lineWidth = 3;
    g.stroke();
    g.fillStyle = '#2c4f8f';
    g.font = `600 ${sy * 8.6}px ${SANS}`;
    g.textBaseline = 'middle';
    g.fillText('hielscher', px(lx + 5), py(153.5));
    g.fillStyle = '#fff';
    g.font = `700 ${sy * 12}px ${SANS}`;
    g.textAlign = 'right';
    g.fillText('UP400St', px(D - 18), py(52));
    g.fillStyle = '#aab0b8';
    g.font = `400 ${sy * 5.6}px ${SANS}`;
    g.fillText('ultrasonic processor', px(D - 18), py(42));
    g.textAlign = 'left';
    if (!back) {
      g.beginPath();
      g.arc(px(120), py(130), sy * 3.2, 0, Math.PI * 2);
      g.fillStyle = ledOn ? '#4ade80' : '#3f5d43';
      g.shadowColor = '#4ade80';
      g.shadowBlur = ledOn ? 30 : 0;
      g.fill();
      g.shadowBlur = 0;
      g.fillStyle = '#1f2a3a';
      g.font = `500 ${sy * 6}px ${SANS}`;
      g.fillText('Sonicating', px(126), py(130));
      g.fillStyle = '#8b929c';
      g.font = `400 ${sy * 4.6}px ${SANS}`;
      g.fillText('www.hielscher.com', px(18), py(18));
    }
    g.restore();
  };
  const sideFront = canvasTexture(1472, 1464, drawSide(false));
  const sideBack = canvasTexture(1472, 1464, drawSide(true));
  keep(sideFront.texture);
  keep(sideBack.texture);

  const sidePlate = (texture: THREE.Texture, z: number, back: boolean) => {
    const geo = keep(new THREE.ShapeGeometry(profile, 24));
    const uv = geo.attributes.uv;
    const pos = geo.attributes.position;
    for (let i = 0; i < uv.count; i++) uv.setXY(i, pos.getX(i) / D, pos.getY(i) / H);
    const mat = keep(
      new THREE.MeshPhysicalMaterial({
        map: texture,
        transparent: true,
        roughness: 0.42,
        metalness: 0.15,
        clearcoat: 0.5,
        clearcoatRoughness: 0.35,
        emissive: 0xffffff,
        emissiveMap: texture,
        emissiveIntensity: 0,
        polygonOffset: true,
        polygonOffsetFactor: -2,
        side: back ? THREE.BackSide : THREE.FrontSide,
      }),
    );
    const mesh = new THREE.Mesh(geo, mat);
    mesh.position.z = z;
    body.add(mesh);
    return mesh;
  };
  const sideA = sidePlate(sideFront.texture, W / 2 + 0.15, false);
  sidePlate(sideBack.texture, -W / 2 - 0.15, true);

  // Interruptor de encendido.
  {
    const frame = new THREE.Mesh(
      keep(new THREE.BoxGeometry(12, 26, 2.4)),
      keep(new THREE.MeshPhysicalMaterial({ color: 0x2c5fb0, roughness: 0.4, clearcoat: 0.6 })),
    );
    frame.position.set(36, 132, W / 2 + 1.2);
    body.add(frame);
    const rocker = new THREE.Mesh(keep(new THREE.BoxGeometry(7, 18, 3)), black);
    rocker.position.set(36, 134, W / 2 + 2);
    rocker.rotation.x = 0.12;
    body.add(rocker);
    const mark = new THREE.Mesh(
      keep(new THREE.BoxGeometry(5, 4, 0.6)),
      keep(new THREE.MeshStandardMaterial({ color: 0xf26b1d, emissive: 0xf26b1d, emissiveIntensity: 0.4 })),
    );
    mark.position.set(36, 141, W / 2 + 3.6);
    body.add(mark);
  }

  /* ── pendiente frontal: marco, pantalla táctil en color y botón ── */
  const slopeA = new THREE.Vector2(0, 98);
  const slopeB = new THREE.Vector2(72, H);
  const slopeLen = slopeA.distanceTo(slopeB);
  const along = new THREE.Vector3(slopeB.x - slopeA.x, slopeB.y - slopeA.y, 0).normalize();
  const outward = new THREE.Vector3(-along.y, along.x, 0);
  let screenOn = false;
  let amplitude = 0;
  let bars = 0;
  const screen = canvasTexture(1024, 900, (g, w, h) => {
    g.fillStyle = '#c9ced6';
    g.fillRect(0, 0, w, h);
    g.strokeStyle = '#3d6fb6';
    g.lineWidth = 8;
    g.beginPath();
    g.roundRect(30, 30, w - 60, h - 60, 50);
    g.stroke();
    const dx = 190;
    const dy = 70;
    const dw = w - 380;
    const dh = 470;
    g.fillStyle = '#0b0e12';
    g.beginPath();
    g.roundRect(dx - 14, dy - 14, dw + 28, dh + 28, 18);
    g.fill();
    if (screenOn) {
      g.fillStyle = '#0a2342';
      g.fillRect(dx, dy, dw, dh);
      g.fillStyle = '#7fb2ff';
      g.font = `600 30px ${SANS}`;
      g.fillText('UP400St', dx + 26, dy + 50);
      g.fillStyle = '#fff';
      g.font = `700 150px ${SANS}`;
      g.fillText(`${Math.round(amplitude)}%`, dx + 22, dy + 210);
      g.fillStyle = '#7fb2ff';
      g.font = `500 28px ${SANS}`;
      g.fillText('Amplitud', dx + 30, dy + 250);
      for (let i = 0; i < 18; i++) {
        const v = 0.45 + 0.4 * Math.sin(bars * 6 + i * 0.7) * Math.sin(bars * 2.3 + i * 0.3);
        g.fillStyle = i < 12 ? '#34d399' : '#fbbf24';
        g.fillRect(dx + 30 + i * 30, dy + 420 - v * 120, 20, v * 120);
      }
      g.fillStyle = '#cfe0ff';
      g.font = `600 30px ${SANS}`;
      g.textAlign = 'right';
      g.fillText('24 kHz', dx + dw - 26, dy + 50);
      g.textAlign = 'left';
    } else {
      g.fillStyle = '#11161c';
      g.fillRect(dx, dy, dw, dh);
    }
    g.fillStyle = '#fff';
    g.beginPath();
    g.roundRect(w / 2 - 110, 600, 220, 50, 10);
    g.fill();
    g.strokeStyle = '#3d6fb6';
    g.lineWidth = 4;
    g.stroke();
    g.fillStyle = '#2c4f8f';
    g.font = `600 34px ${SANS}`;
    g.textAlign = 'center';
    g.fillText('hielscher', w / 2, 637);
    g.fillStyle = '#3d6fb6';
    g.beginPath();
    g.roundRect(w / 2 - 210, 700, 420, 110, 55);
    g.fill();
    g.textAlign = 'left';
  });
  keep(screen.texture);
  const bezelH = slopeLen - 14;
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
  const bezel = new THREE.Mesh(keep(new THREE.PlaneGeometry(W - 18, bezelH)), bezelMat);
  // El perfil redondeado queda 1,7 mm por fuera de la recta del plano: el marco
  // se apoya sobre esa superficie, no sobre la recta.
  const mid = new THREE.Vector3((slopeA.x + slopeB.x) / 2, (slopeA.y + slopeB.y) / 2, 0).addScaledVector(outward, BEVEL + 2.05);
  bezel.position.copy(mid);
  bezel.up.copy(along);
  bezel.lookAt(mid.clone().add(outward));
  body.add(bezel);
  const knobBtn = new THREE.Mesh(keep(new THREE.CylinderGeometry(7.5, 7.5, 3, 40)), black);
  knobBtn.position.copy(mid).addScaledVector(along, -bezelH * 0.33).addScaledVector(outward, 1.6);
  knobBtn.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), outward);
  body.add(knobBtn);

  /* ── atrás: bloque de sujeción y perilla de estrella ── */
  const mount = new THREE.Group();
  body.add(mount);
  {
    const block = new THREE.Mesh(keep(roundedBox(22, 50, 40, 4)), black);
    block.position.set(D + 11, 55, 0);
    block.castShadow = true;
    mount.add(block);
    const stem = new THREE.Mesh(keep(new THREE.CylinderGeometry(4, 4, 18, 20)), steel);
    stem.rotation.z = Math.PI / 2;
    stem.position.set(D + 30, 55, 0);
    mount.add(stem);
  }
  const star = new THREE.Group();
  star.position.set(D + 42, 55, 0);
  mount.add(star);
  {
    const hub = new THREE.Mesh(keep(new THREE.CylinderGeometry(9, 10, 12, 32)), black);
    hub.rotation.z = Math.PI / 2;
    star.add(hub);
    const lobeGeo = keep(new THREE.CylinderGeometry(5.5, 6, 12, 24));
    for (let i = 0; i < 5; i++) {
      const lobe = new THREE.Mesh(lobeGeo, black);
      lobe.rotation.z = Math.PI / 2;
      const a = (i / 5) * Math.PI * 2;
      lobe.position.set(0, Math.cos(a) * 12, Math.sin(a) * 12);
      star.add(lobe);
    }
  }

  // Los cuatro tornillos de la base, que el plano dibuja en verde.
  {
    const screwGeo = keep(new THREE.CylinderGeometry(3, 3, 2, 16));
    const screwMat = keep(new THREE.MeshStandardMaterial({ color: 0x5fae5a, roughness: 0.5 }));
    for (const [x, z] of [[14, 58], [14, -58], [D - 14, 58], [D - 14, -58]] as const) {
      const screw = new THREE.Mesh(screwGeo, screwMat);
      screw.position.set(x, -BEVEL - 0.5, z);
      body.add(screw);
    }
  }

  /* ── transductor y sonotrodo, torneados según el plano ── */
  const horn = new THREE.Group();
  horn.position.set(40, -BEVEL, 0);
  body.add(horn);
  const hornMesh = new THREE.Mesh(
    keep(
      lathe([
        [0, 0], [21.5, 0], [21.5, -7], [20, -8], [20, -36], [19.2, -37], [20, -38], [20, -64], [9.4, -122], [9, -126],
        [10.4, -127], [10.4, -136], [9, -137], [9, -146], [10.4, -147], [10.4, -156], [9, -157], [9, -186], [0, -186],
      ]),
    ),
    brushed,
  );
  hornMesh.castShadow = true;
  horn.add(hornMesh);
  const transducer = new THREE.Object3D();
  transducer.position.y = -30;
  horn.add(transducer);
  const oring = new THREE.Mesh(keep(new THREE.TorusGeometry(10.2, 2, 16, 48)), rubber);
  oring.rotation.x = Math.PI / 2;
  oring.position.y = -190.5;
  horn.add(oring);
  const tip = new THREE.Mesh(keep(new THREE.CylinderGeometry(7, 7, 51.5, 48)), titanium);
  tip.position.y = -186 - 51.5 / 2 - 4.5;
  tip.castShadow = true;
  horn.add(tip);
  const TIP_Y = -186 - 51.5 - 4.5;

  /* ── soporte, matraz y muestra ── */
  const plate = new THREE.Mesh(keep(roundedBox(360, 10, 210, 4)), alu);
  plate.position.set(80, 14, 0);
  plate.castShadow = true;
  plate.receiveShadow = true;
  root.add(plate);
  {
    const footGeo = keep(new THREE.CylinderGeometry(9, 10, 9, 24));
    for (const [x, z] of [[-90, 92], [-90, -92], [250, 92], [250, -92]] as const) {
      const foot = new THREE.Mesh(footGeo, rubber);
      foot.position.set(x, 4.5, z);
      root.add(foot);
    }
  }
  const ROD_X = D + 62;
  const rod = new THREE.Mesh(keep(new THREE.CylinderGeometry(8, 8, 720, 40)), steel);
  rod.position.set(ROD_X, 19 + 360, 0);
  rod.castShadow = true;
  root.add(rod);

  const FLASK_Y = 19;
  const LIQUID = 62;
  // El vidrio se simula con reflejo y poca opacidad: el vidrio por transmisión
  // de three.js oculta los objetos transparentes de dentro (muestra y burbujas).
  const glass = keep(
    new THREE.MeshPhysicalMaterial({
      color: 0xf4fbfa,
      roughness: 0.03,
      clearcoat: 1,
      clearcoatRoughness: 0.02,
      envMapIntensity: 2.2,
      transparent: true,
      opacity: 0.22,
      side: THREE.DoubleSide,
      depthWrite: false,
    }),
  );
  const flask = new THREE.Mesh(
    keep(
      lathe([
        [0, 0], [40, 0], [43, 3], [44, 8], [42, 24], [22, 104], [17, 116], [17, 136], [19, 139], [19, 141], [16, 141],
        [15.5, 138], [15.5, 117], [20.5, 104], [40.5, 25], [41.5, 9], [39, 3.5], [0, 3.5],
      ]),
    ),
    glass,
  );
  flask.position.set(40, FLASK_Y, 0);
  flask.renderOrder = 3;
  root.add(flask);

  const liquid = new THREE.Mesh(
    keep(lathe([[0, 3.6], [38.8, 3.6], [40.3, 9], [39.4, 25], [30.8, LIQUID], [0, LIQUID]])),
    keep(
      new THREE.MeshPhysicalMaterial({
        color: 0xf2c400,
        roughness: 0.18,
        clearcoat: 0.6,
        emissive: 0xd99a00,
        emissiveIntensity: 0.22,
        transparent: true,
        opacity: 0.9,
        depthWrite: false,
        side: THREE.DoubleSide,
      }),
    ),
  );
  liquid.position.set(40, FLASK_Y, 0);
  liquid.renderOrder = 1;
  root.add(liquid);

  const surfaceGeo = keep(new THREE.CircleGeometry(30.6, 96));
  surfaceGeo.rotateX(-Math.PI / 2);
  const surfaceBase = Float32Array.from(surfaceGeo.attributes.position.array as ArrayLike<number>);
  const surface = new THREE.Mesh(
    surfaceGeo,
    keep(
      new THREE.MeshPhysicalMaterial({
        color: 0xf6cf12,
        roughness: 0.05,
        clearcoat: 1,
        emissive: 0xd99a00,
        emissiveIntensity: 0.2,
        transparent: true,
        opacity: 0.95,
      }),
    ),
  );
  surface.position.set(40, FLASK_Y + LIQUID + 0.05, 0);
  root.add(surface);

  // El equipo se apoya de modo que la punta quede 22 mm sobre el fondo interior.
  const tipWorld = FLASK_Y + 26;
  const bodyY = tipWorld - TIP_Y + BEVEL;
  body.position.set(0, bodyY, 0);
  const arm = new THREE.Mesh(keep(new THREE.CylinderGeometry(5, 5, 34, 20)), steel);
  arm.rotation.z = Math.PI / 2;
  arm.position.set(ROD_X - 22, bodyY + 55, 0);
  root.add(arm);
  const collar = new THREE.Mesh(keep(roundedBox(26, 30, 26, 4)), black);
  collar.position.set(ROD_X, bodyY + 55, 0);
  root.add(collar);

  /* ── cavitación: burbujas desde la punta y un halo bajo ella ── */
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
  glow.scale.set(70, 50, 1);
  glow.position.set(40, tipWorld - 6, 0);
  root.add(glow);

  const COUNT = 420;
  const bubbles = new THREE.InstancedMesh(
    keep(new THREE.SphereGeometry(1, 10, 8)),
    keep(
      new THREE.MeshPhysicalMaterial({
        color: 0xffffff,
        roughness: 0.05,
        clearcoat: 1,
        transparent: true,
        opacity: 0.9,
        emissive: 0xfff6c4,
        emissiveIntensity: 0.35,
      }),
    ),
    COUNT,
  );
  bubbles.position.set(40, 0, 0);
  bubbles.renderOrder = 2;
  root.add(bubbles);
  const dummy = new THREE.Object3D();
  const state = Array.from({ length: COUNT }, () => ({ x: 0, y: 0, z: 0, vx: 0, vy: 0, vz: 0, s: 0, life: 0, max: 1 }));
  dummy.scale.setScalar(0);
  dummy.updateMatrix();
  for (let i = 0; i < COUNT; i++) bubbles.setMatrixAt(i, dummy.matrix);

  const spawn = (b: (typeof state)[number]) => {
    const a = Math.random() * Math.PI * 2;
    const r = Math.random() * 6.5;
    b.x = Math.cos(a) * r;
    b.z = Math.sin(a) * r;
    b.y = tipWorld - 1 - Math.random() * 3;
    const out = 0.25 + Math.random() * 0.9;
    b.vx = Math.cos(a) * out * Math.random() * 1.6;
    b.vz = Math.sin(a) * out * Math.random() * 1.6;
    b.vy = -1.2 - Math.random() * 1.8;
    b.s = 0.35 + Math.random() * 1.1;
    b.life = 0;
    b.max = 0.9 + Math.random() * 1.8;
  };

  /* ── anclas para la escena ── */
  const screenAnchor = new THREE.Object3D();
  screenAnchor.position.copy(mid);
  body.add(screenAnchor);
  const bodyAnchor = new THREE.Object3D();
  bodyAnchor.position.set(D / 2, H / 2, 0);
  body.add(bodyAnchor);

  let powered = false;
  let lastScreen = 0;
  let shownLed = false;

  return {
    root,
    anchors: { screen: screenAnchor, tip, transducer, mount: star, body: bodyAnchor },
    normals: { screen: outward.clone(), side: new THREE.Vector3(0, 0, 1) },
    setPower(on) {
      powered = on;
      if (on) screenOn = true;
    },
    update(dt, t, level) {
      if (screenOn && t - lastScreen > 1 / 20) {
        lastScreen = t;
        bars = t;
        amplitude += ((powered ? 70 + 6 * Math.sin(t * 0.8) : 0) - amplitude) * 0.08;
        if (!powered && level < 0.02) screenOn = false;
        screen.redraw();
      }
      bezelMat.emissiveIntensity = 0.55 * level;
      if (shownLed !== powered) {
        shownLed = powered;
        ledOn = powered;
        sideFront.redraw();
      }
      (sideA.material as THREE.MeshPhysicalMaterial).emissiveIntensity = 0.04 * level;

      tip.position.x = (Math.random() - 0.5) * 0.25 * level;
      glowMat.opacity = 0.75 * level * (0.8 + Math.random() * 0.2);

      for (let i = 0; i < COUNT; i++) {
        const b = state[i];
        if (b.life >= b.max || b.s === 0) {
          if (level > 0.3 && Math.random() < 0.25 * level) spawn(b);
          else {
            dummy.scale.setScalar(0);
            dummy.updateMatrix();
            bubbles.setMatrixAt(i, dummy.matrix);
            continue;
          }
        }
        b.life += dt;
        b.vy += dt * 9;
        b.x += b.vx * dt * 14;
        b.z += b.vz * dt * 14;
        b.y += b.vy * dt * 10;
        const radial = Math.hypot(b.x, b.z);
        const limit = b.y < FLASK_Y + 25 ? 36 : 36 - (b.y - FLASK_Y - 25) * 0.2;
        if (radial > limit) {
          b.vx *= -0.4;
          b.vz *= -0.4;
          b.x *= limit / radial;
          b.z *= limit / radial;
        }
        if (b.y < FLASK_Y + 6) {
          b.y = FLASK_Y + 6;
          b.vy = Math.abs(b.vy);
        }
        if (b.y > FLASK_Y + LIQUID - 1) b.life = b.max;
        const k = Math.min(1, b.life * 6) * (1 - b.life / b.max) * level;
        dummy.position.set(b.x, b.y, b.z);
        dummy.scale.setScalar(b.s * (0.6 + k));
        dummy.updateMatrix();
        bubbles.setMatrixAt(i, dummy.matrix);
      }
      bubbles.instanceMatrix.needsUpdate = true;

      const p = surfaceGeo.attributes.position;
      if (level > 0.001) {
        for (let i = 0; i < p.count; i++) {
          const x = surfaceBase[i * 3];
          const z = surfaceBase[i * 3 + 2];
          const r = Math.hypot(x, z);
          p.setY(i, level * (Math.sin(r * 0.55 - t * 18) * 0.9 * Math.exp(-r * 0.03) + Math.sin(x * 0.4 + t * 7) * 0.25));
        }
        p.needsUpdate = true;
        surfaceGeo.computeVertexNormals();
      }
    },
    dispose() {
      for (const item of disposables) item.dispose();
      bubbles.dispose();
    },
  };
}
