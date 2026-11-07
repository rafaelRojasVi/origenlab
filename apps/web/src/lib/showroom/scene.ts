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
  /**
   * Como `goTo`, con duración propia y suavizado `smoothstep` (la página de
   * producto lo usa por tramos de scroll; `goTo` conserva su salida suave de 1,2 s).
   */
  tweenTo(view: string, durationMs?: number): void;
  /**
   * Fija el nivel de marcha (0–1) sin transición, p. ej. desde el scroll; `null`
   * devuelve el nivel al que sigue a `setPower`. No toca la pantalla del modelo.
   */
  setLevel(level: number | null): void;
  /** Posición en píxeles del lienzo de cada ancla, o `null` si queda detrás. */
  onFrame(callback: (anchors: AnchorPositions) => void): void;
  /** Cambia el equipo en el mismo escenario (un solo lienzo, un solo WebGL). */
  setModel(model: BuiltModel): void;
  dispose(): void;
}

const MM = 0.01;

export function createShowroom(canvas: HTMLCanvasElement, initial: BuiltModel): Showroom {
  // En pantallas pequeñas (teléfonos) se dibuja con menos resolución y sombra.
  const small = Math.min(window.innerWidth, window.innerHeight) < 700;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: !small, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, small ? 1.5 : 2));
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
  key.shadow.mapSize.set(small ? 1024 : 2048, small ? 1024 : 2048);
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

  const stage = new THREE.Group();
  stage.scale.setScalar(MM);
  scene.add(stage);

  const world = (o: THREE.Object3D) => o.getWorldPosition(new THREE.Vector3());
  const worldDir = (v: THREE.Vector3) => v.clone().transformDirection(stage.matrixWorld);

  // Estado del equipo montado; `mount` lo rehace al cambiar de equipo.
  let model = initial;
  let yaw = 0.55;
  const framed = new THREE.Box3();
  const framedSize = new THREE.Vector3();
  let anchorNames: string[] = [];
  const mount = (next: BuiltModel) => {
    model = next;
    yaw = next.yaw ?? 0.55;
    stage.add(next.root);
    stage.position.set(0, 0, 0);
    stage.rotation.set(0, yaw, 0);
    // Centrar el conjunto sobre el origen, apoyado en el suelo.
    stage.updateMatrixWorld(true);
    const center = new THREE.Box3().setFromObject(next.root).getCenter(new THREE.Vector3());
    stage.position.x -= center.x;
    stage.position.z -= center.z;
    stage.updateMatrixWorld(true);
    // La vista general encuadra el conjunto entero (hasta `frameTop`, si el
    // modelo lo da) con un margen, en alto y en ancho según el lienzo.
    framed.setFromObject(next.root);
    if (next.frameTop !== undefined) framed.max.y = Math.min(framed.max.y, next.frameTop * MM);
    framed.getSize(framedSize);
    anchorNames = Object.keys(next.anchors);
  };
  mount(initial);

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

  let tween: { fromP: THREE.Vector3; fromT: THREE.Vector3; toP: THREE.Vector3; toT: THREE.Vector3; t: number; ms: number; smooth: boolean } | null = null;
  let idle = true;
  const startTween = (name: string, ms: number, smooth: boolean) => {
    idle = name === 'general' && idle;
    const v = viewFor(name);
    tween = { fromP: camera.position.clone(), fromT: controls.target.clone(), toP: v.pos, toT: v.at, t: 0, ms, smooth };
  };
  const goTo = (name: string) => startTween(name, 1200, false);
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
  let levelOverride: number | null = null;
  let t = 0;
  let raf = 0;
  let last = performance.now();
  let frameCallback: Parameters<Showroom['onFrame']>[0] | null = null;
  const ray = new THREE.Raycaster();
  const hidden: Record<string, boolean> = {};
  let occlusionTick = 0;

  const frame = (now: number) => {
    raf = 0;
    const dt = Math.min((now - last) / 1000, 0.05);
    last = now;
    t += dt;
    level = levelOverride ?? level + ((powered ? 1 : 0) - level) * Math.min(1, dt * 3);

    if (tween) {
      tween.t = Math.min(1, tween.t + Math.max(dt, 1 / 60) / (tween.ms / 1000));
      const e = tween.smooth ? tween.t * tween.t * (3 - 2 * tween.t) : 1 - Math.pow(1 - tween.t, 3);
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
      // Un marcador tapado por el propio equipo (la cara delantera vista desde
      // atrás) se oculta. El rayo se lanza cada pocos cuadros, no en todos.
      if (++occlusionTick % 6 === 0) {
        for (const name of anchorNames) {
          const at = world(model.anchors[name]);
          const toAnchor = at.clone().sub(camera.position);
          const reach = toAnchor.length();
          ray.set(camera.position, toAnchor.normalize());
          ray.camera = camera;
          ray.far = reach;
          // Sólo tapa lo opaco: vidrio, muestra y burbujas no ocultan el marcador.
          const opaque = (item: THREE.Intersection) => {
            const material = (item.object as THREE.Mesh).material as THREE.Material | THREE.Material[] | undefined;
            const first = Array.isArray(material) ? material[0] : material;
            return Boolean(first) && !(first!.transparent && first!.opacity < 0.98) && !(item.object as THREE.Object3D & { isSprite?: boolean }).isSprite;
          };
          const hit = ray.intersectObject(model.root, true).find(opaque);
          // 60 mm de margen: un ancla puede estar dentro o justo detrás de su pieza
          // (una perilla, un rotor bajo el borde de la cámara); sólo un cuerpo entero la tapa.
          hidden[name] = Boolean(hit && hit.distance < reach - 0.6);
        }
      }
      const out: AnchorPositions = {};
      for (const name of anchorNames) {
        const p = world(model.anchors[name]).project(camera);
        out[name] = p.z < 1 && !hidden[name] ? { x: ((p.x + 1) / 2) * w, y: ((1 - p.y) / 2) * h } : null;
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
    setModel(next) {
      stage.remove(model.root);
      model.dispose();
      for (const name of Object.keys(hidden)) delete hidden[name];
      powered = false;
      level = 0;
      levelOverride = null;
      mount(next);
      idle = true;
      const v = general();
      tween = { fromP: camera.position.clone(), fromT: controls.target.clone(), toP: v.pos, toT: v.at, t: 0, ms: 1200, smooth: false };
      start();
    },
    goTo,
    tweenTo: (name, ms = 800) => startTween(name, ms, true),
    setLevel(next) {
      levelOverride = next === null ? null : Math.min(1, Math.max(0, next));
      if (levelOverride !== null) level = levelOverride;
    },
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
