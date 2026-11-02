/**
 * Escena de estudio para cualquier equipo modelado (`models/*.ts`): luz, sombra de contacto, cámara
 * orbital, vistas con nombre y marcadores que siguen a las piezas.
 *
 * Se carga sólo cuando la portada lo pide (import dinámico): el resto del
 * sitio no paga three.js. Con movimiento reducido no se carga nunca; la portada
 * deja la fotografía oficial.
 *
 * El fondo es transparente: el papel lo pone el CSS, de modo que la escena se
 * asienta sobre el mismo `paper` que el resto de la página y no introduce un
 * color propio.
 */
import * as THREE from 'three';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import type { BuiltModel } from './types';

export type AnchorPositions = Record<string, { x: number; y: number } | null>;

export interface Showroom {
  setPower(on: boolean): void;
  /** `general` o el nombre de una vista del modelo. */
  goTo(view: string): void;
  /** Posición en píxeles del lienzo de cada ancla, o `null` si queda detrás. */
  onFrame(callback: (anchors: AnchorPositions) => void): void;
  dispose(): void;
}

const MM = 0.01;

export function createShowroom(canvas: HTMLCanvasElement, model: BuiltModel): Showroom {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  const environment = pmrem.fromScene(new RoomEnvironment(), 0.03).texture;
  scene.environment = environment;

  const camera = new THREE.PerspectiveCamera(30, 1, 0.05, 100);
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.06;
  controls.enablePan = false;
  controls.enableZoom = false; // la rueda desplaza la página; el acercamiento lo dan las vistas
  controls.minPolarAngle = 0.25;
  controls.maxPolarAngle = 1.52;

  const key = new THREE.DirectionalLight(0xffffff, 2.4);
  key.position.set(-4, 9, 6);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  Object.assign(key.shadow.camera, { left: -5, right: 5, top: 5, bottom: -5 });
  key.shadow.radius = 6;
  key.shadow.bias = -0.0004;
  scene.add(key);
  const fill = new THREE.DirectionalLight(0xdfe8ff, 0.7);
  fill.position.set(6, 3, 4);
  scene.add(fill);
  const rim = new THREE.DirectionalLight(0xffffff, 1.2);
  rim.position.set(2, 5, -7);
  scene.add(rim);

  const ground = new THREE.Mesh(new THREE.PlaneGeometry(40, 40), new THREE.ShadowMaterial({ opacity: 0.16 }));
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  scene.add(ground);

  const yaw = model.yaw ?? 0.55;
  const stage = new THREE.Group();
  stage.add(model.root);
  stage.scale.setScalar(MM);
  stage.rotation.y = yaw;
  scene.add(stage);

  // Centrar el conjunto sobre el origen, apoyado en el suelo.
  stage.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(model.root);
  const center = box.getCenter(new THREE.Vector3());
  stage.position.x -= center.x;
  stage.position.z -= center.z;
  stage.updateMatrixWorld(true);

  /* ── vistas, calculadas desde las piezas y no a mano ── */
  const world = (o: THREE.Object3D) => o.getWorldPosition(new THREE.Vector3());
  const worldDir = (v: THREE.Vector3) => v.clone().transformDirection(stage.matrixWorld);

  // La vista general encuadra el conjunto entero (hasta `frameTop`, si el
  // modelo lo da) con un margen, en alto y en ancho según el lienzo.
  const framed = new THREE.Box3().setFromObject(model.root);
  if (model.frameTop !== undefined) framed.max.y = Math.min(framed.max.y, model.frameTop * MM);
  const framedSize = framed.getSize(new THREE.Vector3());

  const general = () => {
    const at = framed.getCenter(new THREE.Vector3());
    const half = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    const across = Math.hypot(framedSize.x, framedSize.z) * 0.8;
    const distance = Math.max(framedSize.y / 2 / half, across / 2 / (half * camera.aspect)) * 1.18;
    return { at, pos: at.clone().add(new THREE.Vector3(0.32, 0.18, 1).normalize().multiplyScalar(distance)) };
  };
  const viewFor = (name: string) => {
    const view = model.views[name];
    if (!view) return general();
    const at = world(view.target);
    return { at, pos: at.clone().addScaledVector(worldDir(view.direction).normalize(), view.distance * MM) };
  };

  let tween: { fromP: THREE.Vector3; fromT: THREE.Vector3; toP: THREE.Vector3; toT: THREE.Vector3; t: number } | null = null;
  let idle = true;
  const goTo = (name: string) => {
    idle = name === 'general' && idle;
    const v = viewFor(name);
    tween = { fromP: camera.position.clone(), fromT: controls.target.clone(), toP: v.pos, toT: v.at, t: 0 };
  };
  canvas.addEventListener('pointerdown', () => {
    idle = false;
    tween = null;
  });

  const resize = () => {
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    if (idle && !tween) {
      const v = general();
      camera.position.copy(v.pos);
      controls.target.copy(v.at);
    }
  };
  const observer = new ResizeObserver(resize);
  observer.observe(canvas);
  resize();

  /* ── bucle: sólo mientras la escena está en pantalla y la pestaña visible ── */
  let visible = true;
  const io = new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting;
    if (visible) start();
  });
  io.observe(canvas);

  let powered = false;
  let level = 0;
  let t = 0;
  let raf = 0;
  let last = performance.now();
  let frameCallback: Parameters<Showroom['onFrame']>[0] | null = null;
  const anchorNames = Object.keys(model.anchors);

  const frame = (now: number) => {
    raf = 0;
    const dt = Math.min((now - last) / 1000, 0.05);
    last = now;
    t += dt;
    level += ((powered ? 1 : 0) - level) * Math.min(1, dt * 3);

    if (tween) {
      tween.t = Math.min(1, tween.t + Math.max(dt, 1 / 60) / 1.2);
      const e = 1 - Math.pow(1 - tween.t, 3);
      camera.position.lerpVectors(tween.fromP, tween.toP, e);
      controls.target.lerpVectors(tween.fromT, tween.toT, e);
      if (tween.t >= 1) tween = null;
    } else if (idle) {
      stage.rotation.y = yaw + Math.sin(t * 0.25) * 0.18;
    }
    controls.update();
    model.update(dt, t, level);

    if (frameCallback) {
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      const out: AnchorPositions = {};
      for (const name of anchorNames) {
        const p = world(model.anchors[name]).project(camera);
        out[name] = p.z < 1 ? { x: ((p.x + 1) / 2) * w, y: ((1 - p.y) / 2) * h } : null;
      }
      frameCallback(out);
    }

    renderer.render(scene, camera);
    if (visible && !document.hidden) start();
  };
  function start() {
    if (raf) return;
    last = performance.now();
    raf = requestAnimationFrame(frame);
  }
  const onVisibility = () => {
    if (!document.hidden && visible) start();
  };
  document.addEventListener('visibilitychange', onVisibility);
  start();

  return {
    setPower(on) {
      powered = on;
      model.setPower(on);
    },
    goTo,
    onFrame(callback) {
      frameCallback = callback;
    },
    dispose() {
      cancelAnimationFrame(raf);
      observer.disconnect();
      io.disconnect();
      document.removeEventListener('visibilitychange', onVisibility);
      controls.dispose();
      model.dispose();
      environment.dispose();
      pmrem.dispose();
      renderer.dispose();
    },
  };
}
