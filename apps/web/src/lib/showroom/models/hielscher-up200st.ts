/**
 * Hielscher UP200St: generador de mesón con pantalla táctil en color y
 * transductor de acero inoxidable sujeto en vertical a un soporte, unidos por
 * un cable. Modelado a partir de la fotografía de referencia del equipo real
 * (transductor en el soporte, cable gris en arco hasta el generador, sonotrodo
 * dentro de un vaso con líquido rojo) y de las cifras publicadas. Todo está en
 * milímetros; la escena lo reduce a su escala al final.
 *
 *   transductor  Ø 45 × 230 (UP200St-T; ficha de Faust y Buddeberg, «Ø x L:
 *                45 x 230 mm»). Cuerpo de acero cepillado con anillo negro
 *                arriba, un botón negro al frente y anillos de agarre abajo.
 *   soporte      ST1-16 (hielscher.com): varilla Ø 16 de acero inoxidable,
 *                base de 300 × 150, 500 de alto; mordaza ST1-Clamp.
 *   sonotrodos   S26d2, S26d7 y S26d14 (hielscher.com y ficha técnica): el
 *                número es el diámetro de la punta. Por defecto, S26d14.
 *
 * sourceNote — estimado, no publicado:
 *   · El generador (UP200St-G) no tiene medidas de cuerpo publicadas: las de
 *     Faust (343 × 170 × 75, 2 kg) son del embalaje, porque el equipo de la
 *     foto es una caja de pie. Se dibuja de 125 de ancho × 200 de alto × 240 de
 *     fondo, en proporción con el transductor de Ø 45 en la fotografía.
 *   · El largo del sonotrodo (90 mm bajo el cuello del transductor), la altura
 *     de la mordaza y la forma del vaso (embudo con tres asas, como el de la
 *     foto) salen de la fotografía, a escala del transductor.
 *   · El cable se dibuja liso, como en la foto, de Ø 8 estimado.
 *   · La interfaz de la pantalla es verosímil (amplitud, potencia, tiempo),
 *     no una captura del equipo.
 *
 * Los tres sonotrodos son mallas con el nombre de su referencia en `anchors`;
 * la historia elige cuál se ve con `root.userData.sonotrode` (un id S26…),
 * porque `StoryScene.swapSonotrode` sólo conoce los del UP400St.
 *
 * Nada de este archivo decide qué se muestra en la página: sólo construye el
 * objeto y expone las piezas que la escena necesita animar o señalar.
 */
import * as THREE from 'three';
import type { BuildOptions, BuiltModel } from '../types';
import { SANS, canvasTexture, disposer, labGlass, lathe, roundedBox } from './kit';

/* Generador (estimado; ver arriba). */
const GW = 125;
const GH = 200;
const GD = 240;
const BEVEL = 4;
const FEET = 4;
const GEN_X = 262;
const GEN_Z = 12;

/* Soporte ST1-16 y transductor (el eje del transductor es x = 0, z = 0). */
const PLATE_T = 10;
const ROD_X = 140;
const ROD_Z = -42;
const ROD_H = 500;
const T_LEN = 230;
const T_R = 22.5;
const T_Y = 222; // base del cuerpo del transductor
const NECK = 26; // cuello bajo el cuerpo
const SONO_LEN = 90;
const TIP_Y = T_Y - NECK - SONO_LEN; // punta del sonotrodo
const CLAMP_Y = T_Y + 165;

/* Vaso (puesta en escena de la foto). */
const LIQUID_TOP = PLATE_T + 128;

export type Up200Sonotrode = 'S26d2' | 'S26d7' | 'S26d14';

export function build(options: BuildOptions = {}): BuiltModel {
  const root = new THREE.Group();
  const { keep, dispose: disposeAll } = disposer();
  root.userData.sonotrode = 'S26d14' satisfies Up200Sonotrode;

  /* ── materiales ── */
  const housingMat = keep(
    new THREE.MeshPhysicalMaterial({ color: 0xe3e5e7, roughness: 0.42, metalness: 0.04, clearcoat: 0.45, clearcoatRoughness: 0.3 }),
  );
  const panelGrey = keep(new THREE.MeshPhysicalMaterial({ color: 0xa9afb6, roughness: 0.5, metalness: 0.1, clearcoat: 0.3 }));
  const brushed = keep(new THREE.MeshPhysicalMaterial({ color: 0xcfd3d7, metalness: 1, roughness: 0.27, envMapIntensity: 1.3 }));
  const steel = keep(new THREE.MeshPhysicalMaterial({ color: 0xd9dde0, metalness: 1, roughness: 0.2 }));
  const titanium = keep(new THREE.MeshPhysicalMaterial({ color: 0xb4b8bc, metalness: 1, roughness: 0.34, envMapIntensity: 1.2 }));
  const black = keep(new THREE.MeshStandardMaterial({ color: 0x16181b, roughness: 0.55 }));
  const rubber = keep(new THREE.MeshStandardMaterial({ color: 0x111214, roughness: 0.9 }));
  const cableGrey = keep(new THREE.MeshPhysicalMaterial({ color: 0x9aa0a6, roughness: 0.55, metalness: 0.2, clearcoat: 0.3 }));
  const plugWhite = keep(new THREE.MeshStandardMaterial({ color: 0xeef0f1, roughness: 0.5 }));

  /* ═════════ generador: caja de pie, frente inclinado con la pantalla ═════════ */
  const generator = new THREE.Group();
  generator.name = 'generator';
  generator.position.set(GEN_X, 0, GEN_Z);
  root.add(generator);
  const body = new THREE.Group();
  body.position.y = FEET;
  generator.add(body);

  // Perfil lateral (x = fondo desde el frente, y = alto), con el bisel por
  // fuera: el contorno va 4 mm hacia adentro de la medida final.
  const SLOPE_A = new THREE.Vector2(BEVEL, 92);
  const SLOPE_B = new THREE.Vector2(36, GH - BEVEL);
  const profile = new THREE.Shape();
  profile.moveTo(BEVEL + 6, BEVEL);
  profile.lineTo(GD - BEVEL - 6, BEVEL);
  profile.quadraticCurveTo(GD - BEVEL, BEVEL, GD - BEVEL, BEVEL + 6);
  profile.lineTo(GD - BEVEL, GH - BEVEL - 22);
  profile.quadraticCurveTo(GD - BEVEL, GH - BEVEL, GD - BEVEL - 22, GH - BEVEL);
  profile.lineTo(SLOPE_B.x + 8, SLOPE_B.y);
  profile.quadraticCurveTo(SLOPE_B.x, SLOPE_B.y, SLOPE_B.x - 2, SLOPE_B.y - 6);
  profile.lineTo(SLOPE_A.x, SLOPE_A.y);
  profile.lineTo(BEVEL, BEVEL + 6);
  profile.quadraticCurveTo(BEVEL, BEVEL, BEVEL + 6, BEVEL);
  const housingGeo = keep(
    new THREE.ExtrudeGeometry(profile, {
      depth: GW - BEVEL * 2,
      bevelEnabled: true,
      bevelSize: BEVEL,
      bevelThickness: BEVEL,
      bevelSegments: 5,
      curveSegments: 18,
    }),
  );
  housingGeo.translate(0, 0, -(GW - BEVEL * 2) / 2);
  // x del perfil -> z del mundo (el frente mira a +z): z = GD/2 - x.
  housingGeo.rotateY(Math.PI / 2);
  housingGeo.translate(0, 0, GD / 2);
  const housing = new THREE.Mesh(housingGeo, housingMat);
  housing.castShadow = true;
  housing.receiveShadow = true;
  body.add(housing);
  {
    const footGeo = keep(new THREE.CylinderGeometry(7, 8, FEET, 20));
    for (const [x, z] of [[-46, 96], [46, 96], [-46, -96], [46, -96]] as const) {
      const foot = new THREE.Mesh(footGeo, rubber);
      foot.position.set(x, FEET / 2, z);
      generator.add(foot);
    }
  }

  /* pantalla táctil en color, en la cara inclinada */
  const slopeDir = SLOPE_B.clone().sub(SLOPE_A).normalize();
  const slopeLen = SLOPE_A.distanceTo(SLOPE_B);
  const along = new THREE.Vector3(0, slopeDir.y, -slopeDir.x);
  const outward = new THREE.Vector3(0, slopeDir.x, slopeDir.y);
  const slopeMid = new THREE.Vector3(0, (SLOPE_A.y + SLOPE_B.y) / 2, GD / 2 - (SLOPE_A.x + SLOPE_B.x) / 2).addScaledVector(
    outward,
    BEVEL + 0.3,
  );

  // La pantalla queda encendida en espera, como en la foto: azul, con la
  // amplitud fijada; al apagar el ultrasonido la potencia vuelve a 0 W.
  let amplitude = 70;
  let watts = 0;
  let seconds = 0;
  let bars = 0;
  const PW = GW - 12;
  const PH = slopeLen - 8;
  const screen = canvasTexture(1024, Math.round((1024 * PH) / PW), (g, w, h) => {
    g.fillStyle = '#dcdfe2';
    g.fillRect(0, 0, w, h);
    // Rótulo arriba.
    g.fillStyle = '#2c4f8f';
    g.font = `600 58px ${SANS}`;
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText('hielscher', w / 2, 62);
    g.fillStyle = '#6b737d';
    g.font = `500 26px ${SANS}`;
    g.fillText('ULTRASOUND TECHNOLOGY', w / 2, 106);
    // Marco oscuro y pantalla.
    const dx = 120;
    const dy = 150;
    const dw = w - 240;
    const dh = Math.round(dw * 0.74);
    g.fillStyle = '#5d646d';
    g.beginPath();
    g.roundRect(dx - 40, dy - 30, dw + 80, dh + 60, 34);
    g.fill();
    g.fillStyle = '#0a0d11';
    g.fillRect(dx - 6, dy - 6, dw + 12, dh + 12);
    {
      const grad = g.createLinearGradient(0, dy, 0, dy + dh);
      grad.addColorStop(0, '#1a5bb5');
      grad.addColorStop(1, '#0b2d66');
      g.fillStyle = grad;
      g.fillRect(dx, dy, dw, dh);
      g.textAlign = 'left';
      g.textBaseline = 'alphabetic';
      g.fillStyle = '#0a2552';
      g.fillRect(dx, dy, dw, 64);
      g.fillStyle = '#cfe0ff';
      g.font = `600 34px ${SANS}`;
      g.fillText('UP200St', dx + 24, dy + 44);
      g.textAlign = 'right';
      g.fillText('26 kHz', dx + dw - 24, dy + 44);
      g.textAlign = 'left';
      g.fillStyle = '#fff';
      g.font = `700 170px ${SANS}`;
      g.fillText(`${Math.round(amplitude)}%`, dx + 26, dy + 238);
      g.fillStyle = '#9ec1f7';
      g.font = `500 32px ${SANS}`;
      g.fillText('Amplitud', dx + 32, dy + 282);
      // Barra de amplitud.
      g.fillStyle = '#0a2552';
      g.beginPath();
      g.roundRect(dx + 32, dy + 306, dw - 64, 22, 11);
      g.fill();
      g.fillStyle = '#34d399';
      g.beginPath();
      g.roundRect(dx + 32, dy + 306, (dw - 64) * Math.max(0.02, Math.min(1, amplitude / 100)), 22, 11);
      g.fill();
      // Potencia y tiempo.
      const mm = String(Math.floor(seconds / 60)).padStart(2, '0');
      const ss = String(Math.floor(seconds % 60)).padStart(2, '0');
      g.fillStyle = '#9ec1f7';
      g.font = `500 30px ${SANS}`;
      g.fillText('Potencia', dx + 32, dy + 382);
      g.fillText('Tiempo', dx + dw / 2 + 16, dy + 382);
      g.fillStyle = '#fff';
      g.font = `700 64px ${SANS}`;
      g.fillText(`${Math.round(watts)} W`, dx + 32, dy + 448);
      g.fillText(`${mm}:${ss}`, dx + dw / 2 + 16, dy + 448);
      for (let i = 0; i < 22; i++) {
        const v = 0.4 + 0.45 * Math.sin(bars * 6 + i * 0.7) * Math.sin(bars * 2.3 + i * 0.3);
        g.fillStyle = i < 16 ? '#34d399' : '#fbbf24';
        g.fillRect(dx + 32 + i * ((dw - 64) / 22), dy + dh - 18 - v * 60, (dw - 64) / 22 - 6, v * 60);
      }
    }
    // Modelo abajo, a la izquierda; luz de marcha a la derecha.
    g.textAlign = 'left';
    g.textBaseline = 'middle';
    g.fillStyle = '#3a414a';
    g.font = `700 40px ${SANS}`;
    g.fillText('UP200St', dx - 30, dy + dh + 76);
    g.beginPath();
    g.arc(dx + dw + 14, dy + dh + 76, 13, 0, Math.PI * 2);
    g.fillStyle = '#4ade80';
    g.fill();
  });
  keep(screen.texture);
  const bezelMat = keep(
    new THREE.MeshPhysicalMaterial({
      map: screen.texture,
      emissive: 0xffffff,
      emissiveMap: screen.texture,
      emissiveIntensity: 0,
      roughness: 0.22,
      clearcoat: 1,
      clearcoatRoughness: 0.06,
    }),
  );
  const bezel = new THREE.Mesh(keep(new THREE.PlaneGeometry(PW, PH)), bezelMat);
  bezel.position.copy(slopeMid);
  bezel.up.copy(along);
  bezel.lookAt(slopeMid.clone().add(outward));
  body.add(bezel);

  /* frente bajo: placa de conexiones con la toma y su cable */
  const FRONT_Z = GD / 2 + 0.4;
  {
    const plate = new THREE.Mesh(keep(roundedBox(GW - 22, 64, 2, 0.8)), panelGrey);
    plate.position.set(0, 44, FRONT_Z);
    body.add(plate);
    const sd = new THREE.Mesh(keep(new THREE.BoxGeometry(24, 2.6, 1.4)), black);
    sd.position.set(-30, 66, FRONT_Z + 1.2);
    body.add(sd);
    const lan = new THREE.Mesh(keep(new THREE.BoxGeometry(14, 11, 2)), black);
    lan.position.set(30, 64, FRONT_Z + 1.2);
    body.add(lan);
    const sensor = new THREE.Mesh(keep(new THREE.CylinderGeometry(5.5, 5.5, 4, 24)), steel);
    sensor.rotation.x = Math.PI / 2;
    sensor.position.set(0, 64, FRONT_Z + 2);
    body.add(sensor);
    // Conector claro con sus dos tornillos, y el cable que baja al mesón.
    const plug = new THREE.Mesh(keep(roundedBox(40, 18, 16, 3)), plugWhite);
    plug.position.set(-6, 32, FRONT_Z + 8);
    body.add(plug);
    const screwGeo = keep(new THREE.CylinderGeometry(3, 3, 6, 16));
    for (const x of [-30, 18]) {
      const screw = new THREE.Mesh(screwGeo, steel);
      screw.rotation.x = Math.PI / 2;
      screw.position.set(x, 32, FRONT_Z + 3);
      body.add(screw);
    }
    const front = new THREE.CatmullRomCurve3([
      new THREE.Vector3(-6, 32 + FEET, FRONT_Z + 16),
      new THREE.Vector3(-6, 30 + FEET, FRONT_Z + 34),
      new THREE.Vector3(-4, 6, FRONT_Z + 52),
      new THREE.Vector3(20, 4, FRONT_Z + 70),
      new THREE.Vector3(80, 4, FRONT_Z + 60),
    ]);
    const frontCable = new THREE.Mesh(keep(new THREE.TubeGeometry(front, 40, 3.2, 10, false)), rubber);
    generator.add(frontCable);
  }
  // Atrás: toma del transductor.
  const SOCKET = new THREE.Vector3(GEN_X - 20, FEET + 150, GEN_Z - GD / 2 - 2);
  {
    const ring = new THREE.Mesh(keep(new THREE.CylinderGeometry(9, 9, 6, 28)), steel);
    ring.rotation.x = Math.PI / 2;
    ring.position.copy(SOCKET);
    root.add(ring);
  }

  /* ═════════ soporte ST1-16 ═════════ */
  const stand = new THREE.Group();
  stand.name = 'stand';
  root.add(stand);
  {
    const plate = new THREE.Mesh(keep(roundedBox(300, PLATE_T, 150, 2.5)), brushed);
    plate.position.set(30, PLATE_T / 2, -10);
    plate.castShadow = true;
    plate.receiveShadow = true;
    stand.add(plate);
    const rod = new THREE.Mesh(keep(new THREE.CylinderGeometry(8, 8, ROD_H, 40)), steel);
    rod.position.set(ROD_X, PLATE_T + ROD_H / 2, ROD_Z);
    rod.castShadow = true;
    stand.add(rod);
    const thread = new THREE.Mesh(keep(new THREE.CylinderGeometry(13, 13, 8, 32)), steel);
    thread.position.set(ROD_X, PLATE_T + 4, ROD_Z);
    stand.add(thread);
  }

  /* mordaza: nuez en la varilla, brazo y pinza alrededor del transductor */
  const clamp = new THREE.Group();
  clamp.name = 'clamp';
  stand.add(clamp);
  const between = (a: THREE.Vector3, b: THREE.Vector3, r: number, mat: THREE.Material) => {
    const len = a.distanceTo(b);
    const m = new THREE.Mesh(keep(new THREE.CylinderGeometry(r, r, len, 20)), mat);
    m.position.copy(a).add(b).multiplyScalar(0.5);
    m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), b.clone().sub(a).normalize());
    m.castShadow = true;
    clamp.add(m);
    return m;
  };
  const knob = (at: THREE.Vector3, dir: THREE.Vector3) => {
    const stem = between(at, at.clone().addScaledVector(dir, 14), 2.5, steel);
    stem.castShadow = false;
    const head = new THREE.Mesh(keep(new THREE.CylinderGeometry(7, 7, 6, 6)), black);
    head.position.copy(at).addScaledVector(dir, 16);
    head.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir);
    clamp.add(head);
  };
  {
    const boss = new THREE.Mesh(keep(roundedBox(26, 30, 26, 3)), steel);
    boss.position.set(ROD_X, CLAMP_Y, ROD_Z);
    clamp.add(boss);
    knob(new THREE.Vector3(ROD_X, CLAMP_Y + 6, ROD_Z + 13), new THREE.Vector3(0, 0, 1));
    knob(new THREE.Vector3(ROD_X + 13, CLAMP_Y - 6, ROD_Z), new THREE.Vector3(1, 0, 0));
    // El brazo sale de la nuez y llega a la pinza, detrás del transductor.
    const armEnd = new THREE.Vector3(T_R + 16, CLAMP_Y, -6);
    between(new THREE.Vector3(ROD_X, CLAMP_Y, ROD_Z), armEnd, 6, steel);
    // Pinza: dos mandíbulas en arco y su tornillo de cierre al frente.
    const jawGeo = keep(new THREE.TorusGeometry(T_R + 3.5, 3, 12, 40, Math.PI * 0.82));
    for (const y of [CLAMP_Y - 7, CLAMP_Y + 7]) {
      for (const side of [1, -1]) {
        const jaw = new THREE.Mesh(jawGeo, steel);
        jaw.rotation.x = Math.PI / 2;
        jaw.rotation.z = side > 0 ? -Math.PI * 0.41 : Math.PI - Math.PI * 0.41;
        jaw.position.set(0, y, 0);
        jaw.castShadow = true;
        clamp.add(jaw);
      }
    }
    const hinge = new THREE.Mesh(keep(new THREE.BoxGeometry(14, 24, 12)), steel);
    hinge.position.set(T_R + 12, CLAMP_Y, -4);
    clamp.add(hinge);
    // Tornillo de cierre: atraviesa las puntas de las mandíbulas, al frente.
    knob(new THREE.Vector3(-T_R - 5, CLAMP_Y, 6), new THREE.Vector3(-1, 0, 0.25).normalize());
    // Otra perilla al lado del brazo, como en la foto.
    knob(new THREE.Vector3(T_R + 12, CLAMP_Y + 12, -4), new THREE.Vector3(0, 1, 0));
  }

  /* ═════════ transductor UP200St-T, Ø 45 × 230 ═════════ */
  const transducer = new THREE.Group();
  transducer.name = 'transducer';
  transducer.position.y = T_Y;
  root.add(transducer);
  {
    // Perfil: collarín abajo, anillos de agarre en la mitad baja, cuerpo liso arriba.
    const pts: [number, number][] = [[0, 0], [15, 0], [15, 6], [18, 9], [20.5, 14], [T_R, 18]];
    for (let y = 34; y <= 112; y += 13) pts.push([T_R, y], [T_R - 1.1, y + 1], [T_R - 1.1, y + 3], [T_R, y + 4]);
    pts.push([T_R, 196], [T_R - 0.8, 197], [T_R - 0.8, 199], [T_R, 200], [T_R, T_LEN - 12], [0, T_LEN - 12]);
    const shell = new THREE.Mesh(keep(lathe(pts)), brushed);
    shell.castShadow = true;
    transducer.add(shell);
    // Anillo negro del extremo y la salida del cable.
    const cap = new THREE.Mesh(keep(lathe([[0, 0], [T_R + 0.4, 0], [T_R + 0.4, 9], [T_R - 2, 12], [9, 12], [0, 12]])), black);
    cap.position.y = T_LEN - 12;
    transducer.add(cap);
    const gland = new THREE.Mesh(keep(lathe([[0, 0], [9, 0], [9, 6], [7.5, 8], [6, 26], [4.5, 30], [0, 30]], 32)), cableGrey);
    gland.position.y = T_LEN;
    transducer.add(gland);
    // Botón negro al frente.
    const button = new THREE.Mesh(keep(new THREE.CylinderGeometry(4.2, 4.2, 3, 24)), black);
    button.rotation.x = Math.PI / 2;
    button.position.set(0, 128, T_R + 1);
    transducer.add(button);
    // Rótulo grabado bajo el anillo negro.
    const label = canvasTexture(512, 128, (g, w, h) => {
      g.clearRect(0, 0, w, h);
      g.fillStyle = 'rgba(30,40,60,.75)';
      g.font = `600 54px ${SANS}`;
      g.textAlign = 'center';
      g.textBaseline = 'middle';
      g.fillText('hielscher', w / 2, h * 0.36);
      g.font = `500 34px ${SANS}`;
      g.fillText('UP200St-T', w / 2, h * 0.8);
    });
    keep(label.texture);
    const tag = new THREE.Mesh(
      keep(new THREE.CylinderGeometry(T_R + 0.15, T_R + 0.15, 12, 48, 1, true, -1, 2)),
      keep(new THREE.MeshStandardMaterial({ map: label.texture, transparent: true, roughness: 0.4, metalness: 0.3, depthWrite: false })),
    );
    tag.position.y = 184;
    transducer.add(tag);
    // Cuello hacia el sonotrodo.
    const neck = new THREE.Mesh(keep(lathe([[0, 0], [11, 0], [11, -NECK + 6], [9.5, -NECK + 4], [9.5, -NECK], [0, -NECK]], 48)), titanium);
    transducer.add(neck);
  }

  /* sonotrodos intercambiables: S26d2, S26d7, S26d14 */
  const tips = new THREE.Group();
  tips.position.y = T_Y - NECK;
  root.add(tips);
  const variant = (r: number) =>
    new THREE.Mesh(
      keep(lathe([[0, 0], [9, 0], [9, -6], [Math.max(r, 3.5), -16], [r, -SONO_LEN + 2], [r * 0.9, -SONO_LEN], [0, -SONO_LEN]], 48)),
      titanium,
    );
  const sonotrodes: { id: Up200Sonotrode; r: number; mesh: THREE.Mesh }[] = [
    { id: 'S26d2', r: 1, mesh: variant(1) },
    { id: 'S26d7', r: 3.5, mesh: variant(3.5) },
    { id: 'S26d14', r: 7, mesh: new THREE.Mesh(keep(lathe([[0, 0], [9, 0], [9, -6], [7, -9], [7, -SONO_LEN], [0, -SONO_LEN]], 48)), titanium) },
  ];
  for (const s of sonotrodes) {
    s.mesh.castShadow = true;
    s.mesh.visible = s.id === 'S26d14';
    tips.add(s.mesh);
  }
  const tip = new THREE.Object3D();
  tip.position.y = -SONO_LEN + 22;
  tips.add(tip);

  /* ═════════ vaso de embudo con tres asas y la muestra roja ═════════ */
  const vessel = new THREE.Group();
  vessel.position.y = PLATE_T;
  root.add(vessel);
  const glass = keep(labGlass());
  // Más fino que el vidrio común: dos capas sobre la muestra roja la volvían rosada.
  glass.opacity = 0.1;
  const funnel = new THREE.Mesh(
    keep(
      lathe([
        [0, 18], [7, 18], [13, 22], [29, 64], [37, 96], [38, 166], [40, 169], [40, 171], [37.4, 171], [35.6, 166], [35.6, 96],
        [27.5, 65], [11.5, 24], [5, 21], [0, 21],
      ]),
    ),
    glass,
  );
  funnel.renderOrder = 3;
  vessel.add(funnel);
  const liquidMat = keep(
    new THREE.MeshPhysicalMaterial({
      color: 0x9a0018,
      roughness: 0.35,
      clearcoat: 0.15,
      emissive: 0x6a0012,
      emissiveIntensity: 0.55,
      transparent: true,
      opacity: 0.93,
      depthWrite: false,
      side: THREE.DoubleSide,
    }),
  );
  const LQ = LIQUID_TOP - PLATE_T;
  const liquid = new THREE.Mesh(keep(lathe([[0, 21.5], [5, 21.5], [11, 24.5], [27, 65], [35, 96], [35, LQ], [0, LQ]])), liquidMat);
  liquid.renderOrder = 1;
  vessel.add(liquid);
  // Las tres asas, de vidrio, llenas de la misma muestra.
  {
    const loop = new THREE.CatmullRomCurve3(
      [
        [10, 26], [24, 12], [44, 6], [62, 14], [66, 34], [56, 52], [38, 62], [30, 66],
      ].map(([r, y]) => new THREE.Vector3(r, y, 0)),
    );
    const loopGlass = keep(new THREE.TubeGeometry(loop, 48, 6, 14, false));
    const loopLiquid = keep(new THREE.TubeGeometry(loop, 48, 4.6, 12, false));
    for (let i = 0; i < 3; i++) {
      const a = (i / 3) * Math.PI * 2 + Math.PI / 2; // una atrás, dos adelante a los lados
      const g = new THREE.Mesh(loopGlass, glass);
      g.rotation.y = a;
      g.renderOrder = 3;
      vessel.add(g);
      const l = new THREE.Mesh(loopLiquid, liquidMat);
      l.rotation.y = a;
      l.renderOrder = 1;
      vessel.add(l);
    }
  }
  const surfaceGeo = keep(new THREE.CircleGeometry(34.8, 72));
  surfaceGeo.rotateX(-Math.PI / 2);
  const surfaceBase = Float32Array.from(surfaceGeo.attributes.position.array as ArrayLike<number>);
  const surfaceMat = keep(
    new THREE.MeshPhysicalMaterial({
      color: 0xa8081f,
      roughness: 0.2,
      clearcoat: 0.4,
      emissive: 0x6a0012,
      emissiveIntensity: 0.5,
      transparent: true,
      opacity: 0.92,
      depthWrite: false,
    }),
  );
  const surface = new THREE.Mesh(surfaceGeo, surfaceMat);
  surface.position.y = LQ + 0.05;
  surface.renderOrder = 1;
  vessel.add(surface);
  const vesselAnchor = new THREE.Object3D();
  vesselAnchor.position.set(0, 90, 38);
  vessel.add(vesselAnchor);

  /* ── cavitación: chorro turbio bajo la punta, halo y burbujas ── */
  const streamTex = canvasTexture(64, 256, (g, w, h) => {
    const grad = g.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, 'rgba(255,240,244,.95)');
    grad.addColorStop(0.5, 'rgba(255,210,220,.45)');
    grad.addColorStop(1, 'rgba(255,200,210,0)');
    g.fillStyle = grad;
    g.fillRect(0, 0, w, h);
    g.globalCompositeOperation = 'destination-out';
    for (let i = 0; i < 40; i++) {
      g.fillStyle = `rgba(0,0,0,${0.15 + 0.35 * Math.abs(Math.sin(i * 12.9898))})`;
      g.fillRect((i * 37) % w, (i * 53) % h, 6, 22);
    }
    g.globalCompositeOperation = 'source-over';
  });
  streamTex.texture.wrapS = streamTex.texture.wrapT = THREE.RepeatWrapping;
  keep(streamTex.texture);
  const streamMat = keep(
    new THREE.MeshBasicMaterial({
      map: streamTex.texture,
      transparent: true,
      opacity: 0,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      side: THREE.DoubleSide,
    }),
  );
  const STREAM_H = TIP_Y - (PLATE_T + 30);
  const streamGeo = keep(new THREE.CylinderGeometry(7, 24, STREAM_H, 32, 1, true));
  const stream = new THREE.Mesh(streamGeo, streamMat);
  stream.position.y = TIP_Y - STREAM_H / 2;
  stream.renderOrder = 2;
  root.add(stream);

  const glowTex = canvasTexture(128, 128, (g) => {
    const r = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    r.addColorStop(0, 'rgba(255,255,255,.95)');
    r.addColorStop(0.35, 'rgba(255,214,224,.45)');
    r.addColorStop(1, 'rgba(255,214,224,0)');
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
  glow.scale.set(46, 34, 1);
  glow.position.set(0, TIP_Y - 4, 0);
  glow.renderOrder = 2;
  root.add(glow);

  const COUNT = options.lite ? 110 : 280;
  const bubbleGeo = keep(new THREE.SphereGeometry(1, 10, 8));
  const bubbleMat = keep(
    new THREE.MeshPhysicalMaterial({ color: 0xffffff, roughness: 0.05, clearcoat: 1, transparent: true, opacity: 0.85, emissive: 0xffe4ea, emissiveIntensity: 0.35 }),
  );
  const bubbles = new THREE.InstancedMesh(bubbleGeo, bubbleMat, COUNT);
  bubbles.renderOrder = 2;
  root.add(bubbles);
  const dummy = new THREE.Object3D();
  dummy.scale.setScalar(0);
  dummy.updateMatrix();
  for (let i = 0; i < COUNT; i++) bubbles.setMatrixAt(i, dummy.matrix);
  const state = Array.from({ length: COUNT }, () => ({ x: 0, y: 0, z: 0, vx: 0, vy: 0, vz: 0, s: 0, life: 0, max: 1 }));
  let tipR = 7;
  const FLOOR = PLATE_T + 28;
  // Radio interior del embudo a la altura y.
  const wallAt = (y: number) => {
    const h = y - PLATE_T;
    if (h >= 96) return 33;
    if (h >= 65) return 26 + ((h - 65) / 31) * 7;
    return Math.max(6, 11 + ((h - 24) / 41) * 15);
  };
  const spawn = (b: (typeof state)[number]) => {
    const a = Math.random() * Math.PI * 2;
    const r = Math.random() * Math.max(tipR, 1.5) * 0.9;
    b.x = Math.cos(a) * r;
    b.z = Math.sin(a) * r;
    b.y = TIP_Y - 1 - Math.random() * 3;
    const out = 0.25 + Math.random() * 0.9;
    b.vx = Math.cos(a) * out * Math.random() * 1.4;
    b.vz = Math.sin(a) * out * Math.random() * 1.4;
    b.vy = -1.4 - Math.random() * 1.8;
    b.s = 0.2 + Math.random() * 0.6;
    b.life = 0;
    b.max = 0.8 + Math.random() * 1.6;
  };

  /* ═════════ cable gris del transductor al generador ═════════ */
  {
    const top = T_Y + T_LEN + 30;
    const curve = new THREE.CatmullRomCurve3([
      new THREE.Vector3(0, top - 4, 0),
      new THREE.Vector3(0, top + 30, 0),
      new THREE.Vector3(18, top + 80, -6),
      new THREE.Vector3(70, top + 100, -22),
      new THREE.Vector3(128, top + 72, -40),
      new THREE.Vector3(178, top - 10, -70),
      new THREE.Vector3(215, 330, -110),
      new THREE.Vector3(SOCKET.x, SOCKET.y + 70, SOCKET.z - 30),
      new THREE.Vector3(SOCKET.x, SOCKET.y + 10, SOCKET.z - 14),
      new THREE.Vector3(SOCKET.x, SOCKET.y, SOCKET.z - 2),
    ]);
    const cable = new THREE.Mesh(keep(new THREE.TubeGeometry(curve, 160, 4, 12, false)), cableGrey);
    cable.castShadow = true;
    root.add(cable);
  }

  /* ── anclas y vistas ── */
  // El marcador va al borde izquierdo de la pantalla, para que su nota no la tape;
  // la vista mira al centro.
  const screenView = new THREE.Object3D();
  screenView.position.copy(slopeMid);
  body.add(screenView);
  const screenAnchor = new THREE.Object3D();
  screenAnchor.position.copy(slopeMid).add(new THREE.Vector3(-PW / 2 + 6, 0, 0));
  body.add(screenAnchor);
  const transducerAnchor = new THREE.Object3D();
  transducerAnchor.position.set(0, 120, T_R);
  transducer.add(transducerAnchor);
  const transducerView = new THREE.Object3D();
  transducerView.position.set(30, 150, 0);
  transducer.add(transducerView);
  const standAnchor = new THREE.Object3D();
  standAnchor.position.set(ROD_X, CLAMP_Y, ROD_Z + 14);
  stand.add(standAnchor);

  let powered = false;
  let lastScreen = 0;
  let readout: number | null = null;
  let shown = 'S26d14';
  const liquidBase = new THREE.Color(0x9a0018);
  const liquidCloud = new THREE.Color(0xc74a63);
  const surfBase = new THREE.Color(0xa8081f);

  return {
    root,
    anchors: {
      screen: screenAnchor,
      transducer: transducerAnchor,
      tip,
      stand: standAnchor,
      vessel: vesselAnchor,
      ...Object.fromEntries(sonotrodes.map((s) => [s.id, s.mesh])),
    },
    views: {
      pantalla: { target: screenView, direction: outward.clone().add(new THREE.Vector3(-0.15, 0.1, 0.1)), distance: 470 },
      transductor: { target: transducerView, direction: new THREE.Vector3(0.35, 0.12, 1), distance: 520 },
      sonotrodo: { target: tip, direction: new THREE.Vector3(0.3, 0.15, 1), distance: 430 },
    },
    frameTop: T_Y + T_LEN + 150,
    frameMargin: 1.3,
    yaw: -0.12,
    setPower(on) {
      powered = on;
    },
    setReadout(percent) {
      readout = percent;
    },
    update(dt, t, level) {
      // Sonotrodo visible, según la historia.
      const want = String(root.userData.sonotrode ?? 'S26d14');
      if (want !== shown && sonotrodes.some((s) => s.id === want)) {
        shown = want;
        for (const s of sonotrodes) s.mesh.visible = s.id === want;
      }
      tipR = (sonotrodes.find((s) => s.mesh.visible) ?? sonotrodes[2]).r;

      if (powered) seconds += dt;
      if (t - lastScreen > 1 / 20) {
        lastScreen = t;
        bars = t;
        if (readout !== null && powered) amplitude = readout;
        else amplitude += ((powered ? 70 + 6 * Math.sin(t * 0.8) : 70) - amplitude) * 0.08;
        watts += ((powered ? amplitude * (0.5 + 1.3 * level) + 3 * Math.sin(t * 3) : 0) - watts) * 0.15;
        if (!powered && level < 0.02) seconds = 0;
        screen.redraw();
      }
      bezelMat.emissiveIntensity = 0.35 + 0.2 * level;

      // La muestra se enturbia con la cavitación.
      liquidMat.color.copy(liquidBase).lerp(liquidCloud, 0.55 * level);
      liquidMat.opacity = 0.93 + 0.05 * level;
      surfaceMat.color.copy(surfBase).lerp(liquidCloud, 0.6 * level);

      tips.position.x = (Math.random() - 0.5) * 0.25 * level;
      const k = Math.sqrt(Math.max(tipR, 1) / 7);
      glowMat.opacity = 0.65 * level * (0.8 + Math.random() * 0.2);
      glow.scale.set(46 * k, 34 * k, 1);
      streamMat.opacity = 0.32 * level;
      stream.scale.set(Math.max(0.35, k), 1, Math.max(0.35, k));
      stream.visible = level > 0.01;
      streamTex.texture.offset.y = -t * 1.6;

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
        if (b.y < FLOOR) {
          b.y = FLOOR;
          b.vy = Math.abs(b.vy);
        }
        const limit = wallAt(b.y) - 1.5;
        const radial = Math.hypot(b.x, b.z);
        if (radial > limit) {
          b.vx *= -0.4;
          b.vz *= -0.4;
          b.x *= limit / radial;
          b.z *= limit / radial;
        }
        if (b.y > LIQUID_TOP - 1) b.life = b.max;
        const f = Math.min(1, b.life * 6) * (1 - b.life / b.max) * level;
        dummy.position.set(b.x, b.y, b.z);
        dummy.scale.setScalar(b.s * (0.6 + f));
        dummy.updateMatrix();
        bubbles.setMatrixAt(i, dummy.matrix);
      }
      bubbles.instanceMatrix.needsUpdate = true;

      if (level > 0.001) {
        const p = surfaceGeo.attributes.position;
        for (let i = 0; i < p.count; i++) {
          const x = surfaceBase[i * 3];
          const z = surfaceBase[i * 3 + 2];
          const r = Math.hypot(x, z);
          p.setY(i, level * (Math.sin(r * 0.6 - t * 18) * 0.8 * Math.exp(-r * 0.035) + Math.sin(x * 0.4 + t * 7) * 0.2));
        }
        p.needsUpdate = true;
        surfaceGeo.computeVertexNormals();
      }
    },
    dispose() {
      disposeAll();
      bubbles.dispose();
    },
  };
}
