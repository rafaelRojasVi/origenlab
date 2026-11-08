/**
 * Löser i-Osmometer — osmómetro por descenso crioscópico.
 *
 * Dimensiones de la hoja técnica oficial de Löser Messtechnik
 * (Leaflet16M.PDF, fuente: chemicalnor.pt, verificado 2026-11-02):
 *   Ancho 190 mm · Alto 278 mm · Fondo 216 mm · Peso ≈ 4,3 kg
 *
 * Estructura del frontal (de arriba a abajo), estimada de la foto oficial ‹est›:
 *   − Marco amarillo: 12 mm
 *   − Banda superior gris clara (pantalla + ranura): 85 mm
 *   − Bahía negra rehundida (impresora + cabeza): 95 mm
 *   − Repisa plateada con pozo de enfriamiento: 18 mm
 *   − Banda inferior negra (i OSMOMETER + logo): 68 mm
 *   Total: 278 mm ✓
 *
 * La bahía aparece rehundida ~8 mm respecto a la banda superior ‹est›.
 */
import * as THREE from 'three';
import type { BuiltModel } from '../types';
import { SANS, canvasTexture, lathe, roundedBox, disposer } from './kit';

/* ── dimensiones maestras (fuente oficial) ── */
const W = 190;
const H = 278;
const D = 216;
const BEVEL = 10;

/* ── zonas verticales (est. de foto) ── */
const FRAME_T  = 12;
const LOWER_H  = 68;   // banda inferior negra
const LEDGE_H  = 18;   // repisa plateada
const BAY_H    = 95;   // bahía negra
const UPPER_H  = H - FRAME_T - LOWER_H - LEDGE_H - BAY_H;  // = 85

const BAY_Y0   = LOWER_H + LEDGE_H;        // y inferior de la bahía  = 86
const BAY_Y1   = BAY_Y0 + BAY_H;           // y superior de la bahía  = 181
const UPPER_Y0 = BAY_Y1;                   // y inferior de la banda superior
const UPPER_Y1 = H - FRAME_T;              // y superior de la banda superior = 266

/* ── profundidad del frontal ── */
const FZ = D / 2;           // cara frontal del housing = 108
const UZ = FZ + 7;          // banda superior: sobresale 7 mm del housing
const BZ = FZ + 1;          // fondo de la bahía: al ras del housing
const IZ = FZ + 4;          // elementos en la bahía: 4 mm de vuelo

/* ── pantalla ── */
const SCR_W = 108;
const SCR_H  = 74;
const SCR_CX = 22;          // offset x desde el centro: a la derecha
const SCR_CY = (UPPER_Y0 + UPPER_Y1) / 2;   // = 223.5

export function build(): BuiltModel {
  const { keep, dispose } = disposer();
  const root = new THREE.Group();

  /* ══════════════════════════════════════════════════════
     MATERIALES
  ══════════════════════════════════════════════════════ */
  const yellowMat = keep(new THREE.MeshPhysicalMaterial({
    color: 0xe6c800, roughness: 0.32, metalness: 0.06,
    clearcoat: 0.5, clearcoatRoughness: 0.28,
  }));
  const lightGreyMat = keep(new THREE.MeshPhysicalMaterial({
    color: 0xd4d8dc, roughness: 0.38, metalness: 0.12,
  }));
  const darkMat = keep(new THREE.MeshStandardMaterial({ color: 0x18191c, roughness: 0.7 }));
  const aluMat  = keep(new THREE.MeshPhysicalMaterial({ color: 0xb8bcbf, metalness: 0.8, roughness: 0.22 }));
  const steel   = keep(new THREE.MeshPhysicalMaterial({ color: 0xd0d4d8, metalness: 0.88, roughness: 0.20 }));
  const plasticGray = keep(new THREE.MeshStandardMaterial({ color: 0x7a8088, roughness: 0.62 }));
  const rubberMat   = keep(new THREE.MeshStandardMaterial({ color: 0x121314, roughness: 0.92 }));
  const backDarkMat = keep(new THREE.MeshStandardMaterial({ color: 0x2c3038, roughness: 0.58, metalness: 0.18 }));

  /* ══════════════════════════════════════════════════════
     CARCASA AMARILLA
  ══════════════════════════════════════════════════════ */
  const housing = new THREE.Group();
  root.add(housing);

  const body = new THREE.Mesh(keep(roundedBox(W, H, D, BEVEL)), yellowMat);
  body.position.y = H / 2;
  body.castShadow = true;
  body.receiveShadow = true;
  housing.add(body);

  // Línea de costura de molde a 2/3 de la altura (usada en LADOS más adelante)
  const SEAM_Y = Math.round(H * 0.665);   // ≈ 185 mm

  // Tapa superior gris clara: el gris frontal envuelve el techo
  const topCap = new THREE.Mesh(
    keep(new THREE.BoxGeometry(W - 2, 11, D - 2)),
    lightGreyMat,
  );
  topCap.position.set(0, H + 5.5, 0);
  topCap.castShadow = true;
  housing.add(topCap);

  // Patas de goma antideslizantes en las cuatro esquinas inferiores
  const footGeo = keep(new THREE.CylinderGeometry(6, 7, 5, 20));
  const footPositions: [number, number][] = [[-1, -1], [-1, 1], [1, -1], [1, 1]];
  for (const [fx, fz] of footPositions) {
    const foot = new THREE.Mesh(footGeo, rubberMat);
    foot.position.set(fx * (W / 2 - 15), -2.5, fz * (D / 2 - 15));
    foot.castShadow = true;
    housing.add(foot);
  }

  /* ══════════════════════════════════════════════════════
     BANDA INFERIOR NEGRA  (y 0 → LOWER_H)
  ══════════════════════════════════════════════════════ */
  const innerW = W - FRAME_T * 2;

  const lowerPanel = new THREE.Mesh(
    keep(new THREE.BoxGeometry(innerW, LOWER_H, 5)),
    darkMat,
  );
  lowerPanel.position.set(0, LOWER_H / 2, BZ + 2);
  housing.add(lowerPanel);

  // Rótulo: "i OSMOMETER" + logo Löser
  const lowerTex = keep(canvasTexture(512, 180, (g, w, h) => {
    g.clearRect(0, 0, w, h);
    g.fillStyle = '#fff';
    g.font = `italic 700 30px ${SANS}`;
    g.textBaseline = 'middle';
    g.fillText('i', 14, h * 0.5);
    g.font = `600 27px ${SANS}`;
    g.fillText(' OSMOMETER', 28, h * 0.5);
    // logo Löser — badge rojo centrado en el tercio derecho
    const lx = w * 0.64, ly = h * 0.2;
    g.fillStyle = '#be0010';
    g.beginPath(); g.roundRect(lx, ly, 84, 46, 5); g.fill();
    g.fillStyle = '#fff';
    g.font = `700 26px ${SANS}`;
    g.textAlign = 'center';
    g.fillText('Löser', lx + 42, ly + 28);
    g.textAlign = 'left';
  }).texture);
  const lowerLabel = new THREE.Mesh(
    keep(new THREE.PlaneGeometry(innerW - 6, LOWER_H - 10)),
    keep(new THREE.MeshBasicMaterial({ map: lowerTex, transparent: true,
      polygonOffset: true, polygonOffsetFactor: -1 })),
  );
  lowerLabel.position.set(0, LOWER_H / 2, BZ + 5);
  housing.add(lowerLabel);

  /* ══════════════════════════════════════════════════════
     REPISA PLATEADA  (y LOWER_H → BAY_Y0)
  ══════════════════════════════════════════════════════ */
  const ledge = new THREE.Mesh(
    keep(new THREE.BoxGeometry(innerW, LEDGE_H, 8)),
    aluMat,
  );
  ledge.position.set(0, LOWER_H + LEDGE_H / 2, BZ + 2);
  ledge.castShadow = true;
  housing.add(ledge);

  // Pozo de enfriamiento en la repisa — círculo oscuro
  const WELL_X = 16;   // centro-derecha de la repisa ‹est›
  const wellDisc = new THREE.Mesh(
    keep(new THREE.CylinderGeometry(7, 7, 9, 32)),
    keep(new THREE.MeshPhysicalMaterial({ color: 0x1c2028, metalness: 0.7, roughness: 0.4 })),
  );
  wellDisc.position.set(WELL_X, LOWER_H + LEDGE_H / 2, BZ + 2);
  housing.add(wellDisc);

  // Borde metálico del pozo
  const wellRim = new THREE.Mesh(
    keep(lathe([[7, 0],[8, 0],[8.5, 1],[8.5, 2],[8, 2.5],[7, 2.5]])),
    aluMat,
  );
  wellRim.position.set(WELL_X, LOWER_H + LEDGE_H, BZ + 2);
  wellRim.rotation.x = Math.PI / 2;
  housing.add(wellRim);

  /* ══════════════════════════════════════════════════════
     BAHÍA NEGRA REHUNDIDA  (y BAY_Y0 → BAY_Y1)
  ══════════════════════════════════════════════════════ */
  // Fondo de la bahía (plano oscuro al nivel del housing)
  const bayBg = new THREE.Mesh(
    keep(new THREE.BoxGeometry(innerW, BAY_H, 4)),
    darkMat,
  );
  bayBg.position.set(0, BAY_Y0 + BAY_H / 2, BZ + 1);
  bayBg.receiveShadow = true;
  housing.add(bayBg);

  /* ── interruptor de encendido (far-left de la bahía) ── */
  const rockerBody = new THREE.Mesh(
    keep(new THREE.BoxGeometry(8, 20, 5)),
    keep(new THREE.MeshPhysicalMaterial({ color: 0x24272d, roughness: 0.45, clearcoat: 0.4 })),
  );
  rockerBody.position.set(-(innerW / 2 - 9), BAY_Y0 + BAY_H * 0.52, IZ);
  housing.add(rockerBody);
  const rockerKnob = new THREE.Mesh(
    keep(new THREE.BoxGeometry(5, 15, 3)),
    keep(new THREE.MeshStandardMaterial({ color: 0x383d46, roughness: 0.6 })),
  );
  rockerKnob.position.set(-(innerW / 2 - 9), BAY_Y0 + BAY_H * 0.52, IZ + 3.5);
  housing.add(rockerKnob);

  /* ── impresora térmica (left-centre de la bahía) ── */
  const PRINT_X = -(innerW / 2 - 38);   // -45mm desde centro
  const PRINT_Y0 = BAY_Y0 + 20;         // y base de la impresora
  const printerGroup = new THREE.Group();
  printerGroup.position.set(PRINT_X, PRINT_Y0, IZ);
  housing.add(printerGroup);

  // Cuerpo de la impresora
  const pFrame = new THREE.Mesh(
    keep(roundedBox(62, 60, 10, 3)),
    keep(new THREE.MeshStandardMaterial({ color: 0x22252a, roughness: 0.65 })),
  );
  pFrame.position.set(0, 30, 0);
  printerGroup.add(pFrame);

  // Rodillo de papel
  const pRoller = new THREE.Mesh(
    keep(new THREE.CylinderGeometry(4, 4, 54, 20)),
    plasticGray,
  );
  pRoller.rotation.z = Math.PI / 2;
  pRoller.position.set(0, 50, 4);
  printerGroup.add(pRoller);

  // Papel saliente
  const paperGeo = keep(new THREE.PlaneGeometry(50, 64));
  const paper = new THREE.Mesh(paperGeo,
    keep(new THREE.MeshStandardMaterial({ color: 0xf4f1e8, roughness: 0.8, side: THREE.DoubleSide })));
  paper.position.set(0, 18, 8);
  paper.rotation.x = 0.1;
  printerGroup.add(paper);

  const paperTex = keep(canvasTexture(256, 320, (g, w, h) => {
    g.fillStyle = '#f4f1e8'; g.fillRect(0, 0, w, h);
    g.fillStyle = '#222'; g.font = `500 16px ${SANS}`;
    g.textAlign = 'center';
    ['i-Osmometer','──────────','+308 mosm/kg','+312 mosm/kg','+310 mosm/kg','──────────','Cal: OK']
      .forEach((l, i) => g.fillText(l, w / 2, 28 + i * 34));
  }).texture);
  const paperLabel = new THREE.Mesh(
    keep(new THREE.PlaneGeometry(48, 62)),
    keep(new THREE.MeshBasicMaterial({ map: paperTex, transparent: true })),
  );
  paperLabel.position.set(0, 18, 9);
  paperLabel.rotation.x = 0.1;
  printerGroup.add(paperLabel);

  /* ── botones >> || debajo de la impresora ── */
  const btnGeo = keep(new THREE.CylinderGeometry(8, 8, 5, 32));
  const btnMat = keep(new THREE.MeshPhysicalMaterial({ color: 0x2a58b8, roughness: 0.38, clearcoat: 0.7 }));
  const BTN_Y = BAY_Y0 + 12;
  for (let i = 0; i < 2; i++) {
    const btn = new THREE.Mesh(btnGeo, btnMat);
    btn.rotation.x = -Math.PI / 2;
    btn.position.set(PRINT_X - 10 + i * 24, BTN_Y, IZ);
    housing.add(btn);
    const symTex = keep(canvasTexture(64, 64, (g, w, h) => {
      g.fillStyle = '#fff'; g.font = `700 26px ${SANS}`;
      g.textAlign = 'center'; g.textBaseline = 'middle';
      g.fillText(i === 0 ? '>>' : '||', w / 2, h / 2);
    }).texture);
    const sym = new THREE.Mesh(keep(new THREE.PlaneGeometry(13, 13)),
      keep(new THREE.MeshBasicMaterial({ map: symTex, transparent: true, depthWrite: false })));
    sym.position.set(PRINT_X - 10 + i * 24, BTN_Y, IZ + 3.5);
    housing.add(sym);
  }

  /* ══════════════════════════════════════════════════════
     CABEZA DE MEDICIÓN (centre-right de la bahía)
  ══════════════════════════════════════════════════════ */
  const HEAD_X      = WELL_X;           // mismo x que el pozo
  const HEAD_BASE_Y = BAY_Y0 + BAY_H * 0.62;  // = 86 + 58.9 = 145mm
  const HEAD_Z      = IZ;               // al nivel de los demás elementos

  const headGroup = new THREE.Group();
  headGroup.position.set(HEAD_X, HEAD_BASE_Y, HEAD_Z);
  housing.add(headGroup);

  // Varilla guía vertical (acero)
  const rodGeo = keep(new THREE.CylinderGeometry(2.2, 2.2, BAY_H * 0.88, 20));
  const rod = new THREE.Mesh(rodGeo, steel);
  rod.position.set(0, 0, -3);
  headGroup.add(rod);

  // Abrazadera con aspecto moleteado: anillos apilados alternos
  const CLAMP_Y = BAY_H * 0.44 - 4;
  const knurlA = keep(new THREE.MeshPhysicalMaterial({ color: 0xb4b8bc, metalness: 0.72, roughness: 0.40 }));
  const knurlB = keep(new THREE.MeshPhysicalMaterial({ color: 0x8e9298, metalness: 0.60, roughness: 0.55 }));
  const ringGeoKnurl = keep(new THREE.CylinderGeometry(5, 5, 1.4, 20));
  for (let k = 0; k < 7; k++) {
    const ring = new THREE.Mesh(ringGeoKnurl, k % 2 === 0 ? knurlA : knurlB);
    ring.position.set(0, CLAMP_Y - 4.2 + k * 1.5, -3);
    headGroup.add(ring);
  }
  // Cuerpo de la abrazadera (bloque trasero)
  const clampBody = new THREE.Mesh(keep(new THREE.BoxGeometry(12, 12, 14)), knurlA);
  clampBody.position.set(0, CLAMP_Y, -10);
  headGroup.add(clampBody);
  // Tornillo lateral de apriete
  const screwMesh = new THREE.Mesh(keep(new THREE.CylinderGeometry(2, 2, 8, 12)), steel);
  screwMesh.rotation.z = Math.PI / 2;
  screwMesh.position.set(8, CLAMP_Y, -3);
  headGroup.add(screwMesh);

  // Portamuestra / cabezal (bloque oscuro: en la foto parece oscuro, no blanco)
  const probeHolder = new THREE.Mesh(keep(roundedBox(26, 22, 22, 3)),
    keep(new THREE.MeshPhysicalMaterial({ color: 0x2a2e34, roughness: 0.50, metalness: 0.15 })));
  probeHolder.castShadow = true;
  headGroup.add(probeHolder);

  // Guía lateral (deslizadera)
  const guide = new THREE.Mesh(keep(new THREE.BoxGeometry(7, 16, 8)), steel);
  guide.position.set(9, 0, -3);
  headGroup.add(guide);

  // Frente biselado del portamuestra (plano inclinado ligeramente, como en la foto)
  const slantFront = new THREE.Mesh(
    keep(new THREE.BoxGeometry(22, 7, 4)),
    keep(new THREE.MeshPhysicalMaterial({ color: 0x34383f, roughness: 0.46, metalness: 0.10 })),
  );
  slantFront.position.set(-2, -8, 12);
  slantFront.rotation.x = 0.42;
  headGroup.add(slantFront);

  // Aguja termistora Peltier: varilla metálica fina que baja al interior del tubo
  const needleMat = keep(new THREE.MeshPhysicalMaterial({ color: 0xc6cad2, metalness: 0.88, roughness: 0.16 }));
  const needle = new THREE.Mesh(keep(new THREE.CylinderGeometry(0.85, 0.85, 34, 8)), needleMat);
  needle.position.set(-1, -11 - 17, 0);
  headGroup.add(needle);
  const needleTip = new THREE.Mesh(
    keep(new THREE.CylinderGeometry(1.6, 0.85, 4, 8)),
    needleMat,
  );
  needleTip.position.set(-1, -11 - 32, 0);
  headGroup.add(needleTip);

  // Tubo de muestra (translúcido, cuelga desde el portamuestra)
  const sampleTube = new THREE.Group();
  headGroup.add(sampleTube);

  const tubeMesh = new THREE.Mesh(
    keep(lathe([[0,0],[3.8,0],[4,1.5],[4,28],[3.5,29],[0,29]])),
    keep(new THREE.MeshPhysicalMaterial({
      color: 0xd8e8ff, transparent: true, opacity: 0.55,
      roughness: 0.04, clearcoat: 1, depthWrite: false,
    })),
  );
  tubeMesh.renderOrder = 2;
  tubeMesh.rotation.x = Math.PI;   // boca arriba, base al pozo
  sampleTube.add(tubeMesh);

  const sampleLiquid = new THREE.Mesh(
    keep(lathe([[0,1],[3.2,1],[3.2,20],[0,20]])),
    keep(new THREE.MeshPhysicalMaterial({
      color: 0xb0ccff, transparent: true, opacity: 0.85,
      roughness: 0.08, depthWrite: false, emissive: 0x5080d0, emissiveIntensity: 0,
    })),
  );
  sampleLiquid.rotation.x = Math.PI;
  sampleLiquid.renderOrder = 1;
  sampleTube.add(sampleLiquid);

  // Tapa roja del tubo (visible desde lejos, como en la foto)
  const capMesh = new THREE.Mesh(
    keep(lathe([[0,0],[4,0],[4.5,1],[4.5,7],[4,8],[0,8]])),
    keep(new THREE.MeshPhysicalMaterial({ color: 0xcc1015, roughness: 0.42, clearcoat: 0.6 })),
  );
  capMesh.rotation.x = Math.PI;
  sampleTube.add(capMesh);

  /* ── brillo de enfriamiento en el pozo ── */
  const frostTex = keep(canvasTexture(128, 128, (g) => {
    const r = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    r.addColorStop(0, 'rgba(180,230,255,0.95)');
    r.addColorStop(0.4, 'rgba(100,180,255,0.38)');
    r.addColorStop(1, 'rgba(100,180,255,0)');
    g.fillStyle = r; g.fillRect(0, 0, 128, 128);
  }).texture);
  const frostMat = keep(new THREE.SpriteMaterial({
    map: frostTex, blending: THREE.AdditiveBlending,
    depthWrite: false, transparent: true, opacity: 0,
  }));
  // el glow se mueve con el pozo (posición fija en world space, separado del headGroup)
  const frostGlow = new THREE.Sprite(frostMat);
  // La geometría del sprite es la que three comparte entre todos; liberarla sólo vacía su copia en la GPU.
  keep(frostGlow.geometry);
  frostGlow.scale.set(26, 26, 1);
  frostGlow.position.set(0, -11 - 5, 0);   // abajo del portamuestra, donde está el pozo
  headGroup.add(frostGlow);

  /* ══════════════════════════════════════════════════════
     BANDA SUPERIOR GRIS CLARA  (y UPPER_Y0 → UPPER_Y1)
  ══════════════════════════════════════════════════════ */
  // Superficie principal: gris claro, sobresale del housing y de la bahía
  const upperPanel = new THREE.Mesh(
    keep(new THREE.BoxGeometry(innerW, UPPER_H, 8)),
    lightGreyMat,
  );
  upperPanel.position.set(0, UPPER_Y0 + UPPER_H / 2, UZ - 2);
  upperPanel.castShadow = true;
  housing.add(upperPanel);

  /* ── ranura de tarjeta / USB (izquierda de la banda superior) ── */
  const cardSlot = new THREE.Mesh(
    keep(new THREE.BoxGeometry(12, 42, 5)),
    keep(new THREE.MeshStandardMaterial({ color: 0x111316, roughness: 0.5 })),
  );
  cardSlot.position.set(-(innerW / 2 - 16), SCR_CY, UZ + 0.5);
  housing.add(cardSlot);

  /* ── bisel de la pantalla ── */
  const screenBezel = new THREE.Mesh(
    keep(new THREE.BoxGeometry(SCR_W + 7, SCR_H + 7, 5)),
    keep(new THREE.MeshStandardMaterial({ color: 0x101214, roughness: 0.5 })),
  );
  screenBezel.position.set(SCR_CX, SCR_CY, UZ + 0.5);
  housing.add(screenBezel);

  /* ── pantalla táctil ── */
  let screenOn    = false;
  let osmoReading = 308;
  let tempPhase   = 0;

  const { texture: screenTex, redraw: redrawScreen } = canvasTexture(512, 380, (g, w, h) => {
    // fondo blanco
    g.fillStyle = screenOn ? '#f2f3f0' : '#0e0f10';
    g.fillRect(0, 0, w, h);
    if (!screenOn) return;

    // Cabecera: "Ready" en rojo, chip CalZP en amarillo
    g.fillStyle = '#cc1018';
    g.font = `700 28px ${SANS}`;
    g.textBaseline = 'middle';
    g.fillText('Ready', 14, 26);

    // Chip CalZP (naranja/amarillo)
    g.fillStyle = '#e8a000';
    g.beginPath(); g.roundRect(w - 110, 10, 96, 30, 6); g.fill();
    g.fillStyle = '#fff';
    g.font = `600 17px ${SANS}`;
    g.textAlign = 'center';
    g.fillText('CalZP: OK', w - 62, 26);
    g.textAlign = 'left';

    // Lectura principal grande
    g.fillStyle = '#111';
    g.font = `700 92px ${SANS}`;
    const numStr = `+${Math.round(osmoReading)}`;
    g.fillText(numStr, 14, 148);
    const nw = g.measureText(numStr).width;
    g.font = `500 36px ${SANS}`;
    g.fillStyle = '#333';
    g.fillText('mosm', 14 + nw + 6, 138);

    // Línea de estado
    g.fillStyle = '#555';
    g.font = `500 18px ${SANS}`;
    g.fillText('Status:', 14, 178);
    g.fillStyle = '#111';
    g.fillText('Measurement o.k.', 80, 178);

    // Mini-gráfico de la curva de enfriamiento
    const gx = 14, gy = 198, gw = w - 28, gh = 110;
    g.fillStyle = '#eaede8';
    g.fillRect(gx, gy, gw, gh);
    g.strokeStyle = '#ccc'; g.lineWidth = 1;
    g.strokeRect(gx, gy, gw, gh);
    // eje
    g.strokeStyle = '#999'; g.lineWidth = 1;
    g.beginPath();
    g.moveTo(gx + 8, gy + 8); g.lineTo(gx + 8, gy + gh - 8);
    g.lineTo(gx + gw - 8, gy + gh - 8);
    g.stroke();
    // curva
    g.strokeStyle = '#1858c8'; g.lineWidth = 2.5;
    g.beginPath();
    const pts = 60;
    const vis = Math.min(1, tempPhase * 1.25);
    for (let i = 0; i <= pts; i++) {
      const tx = i / pts;
      if (tx > vis) break;
      let ty: number;
      if (tx < 0.30) ty = (tx / 0.30) * 0.58;
      else if (tx < 0.38) ty = 0.58 + (tx - 0.30) / 0.08 * 0.24;
      else if (tx < 0.46) ty = 0.82 - (tx - 0.38) / 0.08 * 0.20;
      else ty = 0.62 + Math.sin((tx - 0.46) * 9) * 0.018;
      const px = gx + 8 + tx * (gw - 16);
      const py = gy + gh - 8 - ty * (gh - 16);
      if (i === 0) g.moveTo(px, py); else g.lineTo(px, py);
    }
    g.stroke();

    // Fila de iconos
    const icons = ['⬜', 'Cal', 'Ini', '🔢', '◼', '●', '▲'];
    g.font = `500 15px ${SANS}`; g.textAlign = 'center';
    icons.forEach((ic, i) => {
      const bx = gx + 8 + i * (gw - 16) / (icons.length - 1);
      g.fillStyle = '#d0d8d0';
      g.beginPath(); g.roundRect(bx - 13, gy + gh + 5, 26, 18, 3); g.fill();
      g.fillStyle = '#222';
      g.fillText(ic, bx, gy + gh + 17);
    });
    g.textAlign = 'left';
  });
  keep(screenTex);

  const screenMat = keep(new THREE.MeshPhysicalMaterial({
    map: screenTex, emissive: 0xffffff, emissiveMap: screenTex,
    emissiveIntensity: 0, roughness: 0.08, clearcoat: 1, clearcoatRoughness: 0.04,
  }));
  const screenMesh = new THREE.Mesh(keep(new THREE.PlaneGeometry(SCR_W, SCR_H)), screenMat);
  screenMesh.position.set(SCR_CX, SCR_CY, UZ + 3.5);
  housing.add(screenMesh);

  /* ══════════════════════════════════════════════════════
     LADOS — rejillas de ventilación, tornillos de costura
  ══════════════════════════════════════════════════════ */
  // Textura: grid de ranuras redondeadas oscuras sobre fondo oscuro
  // ── Rejillas de ventilación: ranuras sobre placa de metal oscuro ──
  const ventTex = keep(canvasTexture(384, 288, (g, w, h) => {
    // Base plate: medium grey, clearly distinct from the yellow housing
    g.fillStyle = '#636870';
    g.fillRect(0, 0, w, h);
    // Recessed border (darker)
    g.strokeStyle = '#3e424a'; g.lineWidth = 5;
    g.strokeRect(7, 7, w - 14, h - 14);
    // Rows of rounded ventilation slots
    const slotW = 38, slotH = 10, gapX = 9, gapY = 11;
    const cols = 6, rows = 12;
    const ox = (w - (cols * slotW + (cols - 1) * gapX)) / 2;
    const oy = (h - (rows * slotH + (rows - 1) * gapY)) / 2;
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const x = ox + c * (slotW + gapX), y = oy + r * (slotH + gapY);
        // Shadow rim below slot opening
        g.fillStyle = '#2a2d32';
        g.beginPath(); g.roundRect(x, y + 2, slotW, slotH + 1, 4); g.fill();
        // Slot opening (near-black)
        g.fillStyle = '#0e1012';
        g.beginPath(); g.roundRect(x, y, slotW, slotH, 3); g.fill();
        // Highlight on top lip of slot
        g.fillStyle = '#7a8088';
        g.fillRect(x + 3, y, slotW - 6, 1);
      }
    }
  }).texture);
  // MeshBasicMaterial: no depende del lighting → siempre visible contra el amarillo
  const ventMat = keep(new THREE.MeshBasicMaterial({
    map: ventTex,
    polygonOffset: true, polygonOffsetFactor: -2,
  }));
  const VENT_CZ = -(D / 2 - 58);   // 58 mm desde el borde trasero → z ≈ −50

  // rotation.y = -PI/2 → normal (-1,0,0) → visible desde el lado izquierdo (-X)
  const ventL = new THREE.Mesh(keep(new THREE.PlaneGeometry(92, 68)), ventMat);
  ventL.position.set(-(W / 2) - 0.5, 50, VENT_CZ);
  ventL.rotation.y = -Math.PI / 2;
  housing.add(ventL);

  // rotation.y = +PI/2 → normal (+1,0,0) → visible desde el lado derecho (+X)
  const ventR = new THREE.Mesh(keep(new THREE.PlaneGeometry(92, 68)), ventMat);
  ventR.position.set(W / 2 + 0.5, 50, VENT_CZ);
  ventR.rotation.y = Math.PI / 2;
  housing.add(ventR);

  // ── Tira de costura (parting line) en cada lateral y en el dorso ──
  const seamStripMat = keep(new THREE.MeshStandardMaterial({ color: 0x1a1c20, roughness: 0.85 }));
  for (const sx of [-1, 1]) {
    const strip = new THREE.Mesh(keep(new THREE.BoxGeometry(3, 6, D - 26)), seamStripMat);
    strip.position.set(sx * (W / 2 + 1.0), SEAM_Y, 0);
    housing.add(strip);
  }
  const backSeam = new THREE.Mesh(keep(new THREE.BoxGeometry(W - 10, 6, 3)), seamStripMat);
  backSeam.position.set(0, SEAM_Y, -(D / 2 + 1.0));
  housing.add(backSeam);

  // ── Tornillos de costura a lo largo de la línea de unión ──
  const seamScrewGeo = keep(new THREE.CylinderGeometry(3.2, 3.2, 1.5, 10));
  const seamScrewMat = keep(new THREE.MeshPhysicalMaterial({
    color: 0x8a8e94, metalness: 0.72, roughness: 0.30,
  }));
  const seamScrewZs: number[] = [-(D / 2) + 22, -(D / 4), D / 4];
  for (const sz of seamScrewZs) {
    for (const side of [-1, 1]) {
      const ss = new THREE.Mesh(seamScrewGeo, seamScrewMat);
      ss.rotation.z = Math.PI / 2;
      ss.position.set(side * (W / 2 + 0.4), SEAM_Y, sz);
      housing.add(ss);
    }
  }

  // ── Rótulo serigrafíado en el lado derecho (debajo de la costura) ──
  const sideLabelTex = keep(canvasTexture(320, 64, (g, w, h) => {
    g.clearRect(0, 0, w, h);
    g.fillStyle = 'rgba(0,0,0,0.60)';
    g.font = `700 22px ${SANS}`;
    g.textBaseline = 'middle'; g.textAlign = 'center';
    g.fillText('i-Osmometer', w / 2, h * 0.33);
    g.fillStyle = 'rgba(0,0,0,0.40)';
    g.font = `500 15px ${SANS}`;
    g.fillText('Löser Messtechnik', w / 2, h * 0.70);
  }).texture);
  const sideLabel = new THREE.Mesh(
    keep(new THREE.PlaneGeometry(78, 20)),
    keep(new THREE.MeshBasicMaterial({
      map: sideLabelTex, transparent: true, depthWrite: false,
      polygonOffset: true, polygonOffsetFactor: -1,
    })),
  );
  // rotation.y = +PI/2 → normal (+1,0,0) → visible desde el lado derecho
  sideLabel.position.set(W / 2 + 0.5, SEAM_Y + 30, D / 4);
  sideLabel.rotation.y = Math.PI / 2;
  housing.add(sideLabel);

  /* ══════════════════════════════════════════════════════
     PANEL TRASERO
  ══════════════════════════════════════════════════════ */
  const BK = -(D / 2);   // z de la cara trasera = −108 mm

  // Fondo oscuro del panel trasero
  const backPanel = new THREE.Mesh(
    keep(new THREE.BoxGeometry(W - 3, H - 3, 2)),
    backDarkMat,
  );
  backPanel.position.set(0, H / 2, BK - 1);
  housing.add(backPanel);

  // ── Toma IEC C14 + cajón fusible + rocker trasero ──
  const IEC_X = W / 4 - 2;   // ≈ 45 mm desde centro (aparece a la izq. visto desde atrás)
  const IEC_Y = 32;
  const iecMat = keep(new THREE.MeshStandardMaterial({ color: 0x111316, roughness: 0.6 }));

  const iecBody = new THREE.Mesh(keep(new THREE.BoxGeometry(36, 30, 8)), iecMat);
  iecBody.position.set(IEC_X, IEC_Y, BK - 5);
  housing.add(iecBody);

  const iecHoleMesh = new THREE.Mesh(
    keep(new THREE.BoxGeometry(30, 22, 3)),
    keep(new THREE.MeshStandardMaterial({ color: 0x050608, roughness: 0.95 })),
  );
  iecHoleMesh.position.set(IEC_X, IEC_Y + 3, BK - 7.5);
  housing.add(iecHoleMesh);

  const fuseMesh = new THREE.Mesh(keep(new THREE.BoxGeometry(28, 7, 5)), iecMat);
  fuseMesh.position.set(IEC_X, IEC_Y - 16, BK - 5);
  housing.add(fuseMesh);

  const bkRockerMesh = new THREE.Mesh(
    keep(new THREE.BoxGeometry(14, 22, 6)),
    keep(new THREE.MeshStandardMaterial({ color: 0x22262c, roughness: 0.50 })),
  );
  bkRockerMesh.position.set(IEC_X - 30, IEC_Y, BK - 5);
  housing.add(bkRockerMesh);

  // ── Rejilla del ventilador (lower-left del panel trasero) ──
  const fanGrilleTex = keep(canvasTexture(256, 256, (g, w, h) => {
    const cx = w / 2, cy = h / 2, r = w * 0.46;
    g.fillStyle = '#1a1d22';
    g.beginPath(); g.arc(cx, cy, r, 0, Math.PI * 2); g.fill();
    g.strokeStyle = '#2c3038'; g.lineWidth = 3;
    for (const fr of [0.72, 0.42]) {
      g.beginPath(); g.arc(cx, cy, r * fr, 0, Math.PI * 2); g.stroke();
    }
    g.fillStyle = '#0a0b0d';
    for (let a = 0; a < 8; a++) {
      const angle = (a / 8) * Math.PI * 2;
      g.save(); g.translate(cx, cy); g.rotate(angle);
      g.beginPath(); g.roundRect(-4, 14, 8, r * 0.5, 3); g.fill();
      g.restore();
    }
    g.fillStyle = '#2a2e38';
    g.beginPath(); g.arc(cx, cy, 13, 0, Math.PI * 2); g.fill();
  }).texture);
  const fanGrille = new THREE.Mesh(
    keep(new THREE.PlaneGeometry(58, 58)),
    keep(new THREE.MeshBasicMaterial({ map: fanGrilleTex })),
  );
  fanGrille.position.set(-(W / 4), 42, BK - 2.5);
  fanGrille.rotation.y = Math.PI;  // cara apunta hacia −Z (visible desde atrás)
  housing.add(fanGrille);

  // ── Puertos USB + RS-232 + LAN ──
  const PORT_X0 = -48;
  interface PortDef { x: number; y: number; w: number; h: number; label: string }
  const portData: PortDef[] = [
    { x: PORT_X0,      y:  92, w: 16, h: 10, label: 'USB'    },
    { x: PORT_X0 + 26, y:  92, w: 26, h: 18, label: 'RS-232' },
    { x: PORT_X0,      y: 118, w: 20, h: 14, label: 'LAN'    },
  ];
  const portBodyMat2 = keep(new THREE.MeshStandardMaterial({ color: 0x141618, roughness: 0.55 }));
  const portHoleMat  = keep(new THREE.MeshStandardMaterial({ color: 0x040506, roughness: 0.95 }));
  for (const { x: px, y: py, w: pw, h: ph, label } of portData) {
    const portBox = new THREE.Mesh(keep(new THREE.BoxGeometry(pw, ph, 6)), portBodyMat2);
    portBox.position.set(px, py, BK - 5);
    housing.add(portBox);
    const portHole = new THREE.Mesh(keep(new THREE.BoxGeometry(pw - 4, ph - 4, 3)), portHoleMat);
    portHole.position.set(px, py, BK - 7);
    housing.add(portHole);
    // Etiqueta pequeña (texto espejado en el canvas para corregir el giro Y=PI)
    const pLabelTex = keep(canvasTexture(128, 32, (g, w, h) => {
      g.clearRect(0, 0, w, h);
      g.fillStyle = '#888';
      g.font = `600 14px ${SANS}`;
      g.textBaseline = 'middle'; g.textAlign = 'center';
      g.fillText(label, w / 2, h / 2);
    }).texture);
    const pLabel = new THREE.Mesh(
      keep(new THREE.PlaneGeometry(pw, 6)),
      keep(new THREE.MeshBasicMaterial({ map: pLabelTex, transparent: true, depthWrite: false })),
    );
    pLabel.position.set(px, py - ph / 2 - 5, BK - 2);
    pLabel.rotation.y = Math.PI;
    housing.add(pLabel);
  }

  // ── Placa de características (upper back) ──
  const ratingTex = keep(canvasTexture(512, 128, (g, w, h) => {
    g.fillStyle = '#e8e4d8'; g.fillRect(0, 0, w, h);
    g.strokeStyle = '#ccc'; g.lineWidth = 1; g.strokeRect(2, 2, w - 4, h - 4);
    g.fillStyle = '#222';
    g.font = `700 20px ${SANS}`; g.textBaseline = 'middle'; g.textAlign = 'center';
    g.fillText('Löser Messtechnik', w / 2, h * 0.27);
    g.font = `600 16px ${SANS}`;
    g.fillText('i-Osmometer', w / 2, h * 0.52);
    g.font = `500 13px ${SANS}`; g.fillStyle = '#555';
    g.fillText('100–240 V~  50/60 Hz', w / 2, h * 0.76);
  }).texture);
  const ratingPlate = new THREE.Mesh(
    keep(new THREE.PlaneGeometry(102, 38)),
    keep(new THREE.MeshStandardMaterial({ map: ratingTex, roughness: 0.60 })),
  );
  ratingPlate.position.set(0, H - 55, BK - 2.5);
  ratingPlate.rotation.y = Math.PI;
  housing.add(ratingPlate);

  // ── Tornillos en las cuatro esquinas del panel trasero ──
  const bkScrewGeo = keep(new THREE.CylinderGeometry(4, 4, 2, 8));
  const bkScrewMat = keep(new THREE.MeshPhysicalMaterial({
    color: 0x888e96, metalness: 0.68, roughness: 0.34,
  }));
  const bkSlotMat = keep(new THREE.MeshStandardMaterial({ color: 0x333333, roughness: 0.8 }));
  const bkScrewCorners: [number, number][] = [
    [-(W / 2 - 18), 18], [W / 2 - 18, 18],
    [-(W / 2 - 18), H - 18], [W / 2 - 18, H - 18],
  ];
  for (const [bx, by] of bkScrewCorners) {
    const bs = new THREE.Mesh(bkScrewGeo, bkScrewMat);
    bs.rotation.x = Math.PI / 2;
    bs.position.set(bx, by, BK - 2.5);
    housing.add(bs);
    const bkSlot = new THREE.Mesh(keep(new THREE.BoxGeometry(6, 1.5, 0.8)), bkSlotMat);
    bkSlot.position.set(bx, by, BK - 4);
    housing.add(bkSlot);
  }

  /* ══════════════════════════════════════════════════════
     ANCLAS
  ══════════════════════════════════════════════════════ */
  const screenAnchor = new THREE.Object3D();
  screenAnchor.position.set(SCR_CX, SCR_CY, UZ + 3.5);
  housing.add(screenAnchor);

  const headAnchor = new THREE.Object3D();
  headAnchor.position.set(0, 0, 0);
  headGroup.add(headAnchor);

  const printerAnchor = new THREE.Object3D();
  printerAnchor.position.set(0, 30, 10);
  printerGroup.add(printerAnchor);

  const wellAnchor = new THREE.Object3D();
  wellAnchor.position.set(WELL_X, LOWER_H + LEDGE_H + 2, FZ + 4);
  housing.add(wellAnchor);

  const backAnchor = new THREE.Object3D();
  backAnchor.position.set(0, H / 2, -(D / 2));
  housing.add(backAnchor);

  const bodyAnchor = new THREE.Object3D();
  bodyAnchor.position.set(0, H / 2, 0);
  housing.add(bodyAnchor);

  /* ══════════════════════════════════════════════════════
     ESTADO
  ══════════════════════════════════════════════════════ */
  let powered   = false;
  let headY     = 0;      // 0 = arriba, 1 = bajo (en el pozo)
  let lastDraw  = 0;
  const liqMat  = sampleLiquid.material as THREE.MeshPhysicalMaterial;

  // El DROP hace que la base del tubo llegue justo al pozo en la repisa
  // HEAD_BASE_Y (centro del portamuestra) = 145mm
  // La base del tubo está ~11mm abajo del portamuestra
  // El pozo está en y = LOWER_H + LEDGE_H/2 = 68+9 = 77mm
  // DROP = HEAD_BASE_Y - portamuestra_offset - well_y = 145 - 11 - 77 = 57mm
  const DROP = HEAD_BASE_Y - 11 - (LOWER_H + LEDGE_H / 2);

  return {
    root,
    anchors: {
      screen:  screenAnchor,
      head:    headAnchor,
      printer: printerAnchor,
    },
    views: {
      pantalla: {
        target:    screenAnchor,
        direction: new THREE.Vector3(0, 0.06, 1),
        distance:  195,
      },
      cabeza: {
        target:    headAnchor,
        direction: new THREE.Vector3(-0.55, 0.08, 1),
        distance:  140,
      },
      lateral: {
        target:    bodyAnchor,
        direction: new THREE.Vector3(0.88, 0.10, 1),
        distance:  550,
      },
      trasera: {
        target:    backAnchor,
        direction: new THREE.Vector3(0.30, 0.08, -1),
        distance:  490,
      },
    },
    frameTop: H + 14,
    yaw: 0.38,
    setPower(on: boolean) {
      powered = on;
      if (on) screenOn = true;
    },
    update(dt: number, t: number, level: number) {
      // Cabezal baja al activar
      headY += (level - headY) * Math.min(1, dt * 1.6);
      headGroup.position.y = HEAD_BASE_Y - headY * DROP;

      // Brillo de enfriamiento en el pozo
      frostMat.opacity = 0.75 * level * (0.82 + Math.sin(t * 3.3) * 0.18);
      liqMat.emissiveIntensity = 0.45 * level;

      // Curva de congelación
      if (powered) {
        tempPhase = Math.min(1, tempPhase + dt * 0.17);
        osmoReading += (308 - osmoReading) * 0.025;
      } else {
        tempPhase = Math.max(0, tempPhase - dt * 0.55);
        osmoReading += (0 - osmoReading) * 0.04;
      }

      // Pantalla
      if (screenOn && t - lastDraw > 1 / 15) {
        lastDraw = t;
        if (!powered && level < 0.02) screenOn = false;
        redrawScreen();
      }
      screenMat.emissiveIntensity = 0.62 * level;
    },
    dispose,
  };
}
