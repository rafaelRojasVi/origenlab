/**
 * Escena de estudio para cualquier equipo modelado (`models/*.ts`): luz, sombra de contacto, cámara
 * orbital, vistas con nombre y marcadores que siguen a las piezas.
 *
 * Se carga sólo cuando la portada lo pide (import dinámico): el resto del
 * sitio no paga three.js. Con movimiento reducido no se carga nunca; la portada
 * deja la fotografía oficial.
 *
 * Estudio blanco («Blanco con color»): el fondo del lienzo es transparente y el
 * blanco lo pone el CSS (`--color-studio`, igual a `paper`), de modo que la
 * escena se asienta sobre el mismo fondo que la página y los pósteres se hornean
 * sobre blanco. El piso sólo lleva una sombra de contacto suave y teñida de teal
 * (nunca negra): un mapa de sombras desde una luz cenital que no ilumina (la
 * sombra queda bajo el equipo, no una sombra dura proyectada hacia un lado) y una
 * mancha difusa alrededor. La luz principal no cambia, así los colores reales de
 * cada equipo se conservan. La geometría y los materiales (`models/*.ts`) no se tocan.
 */
import * as THREE from 'three';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import type { BuiltModel } from './types';

export type AnchorPositions = Record<string, { x: number; y: number } | null>;

/**
 * Nivel de calidad del dibujo. Regla: la luz de entorno del estudio
 * (`RoomEnvironment` + `PMREMGenerator`) va en `high` y en `medium`, con la
 * misma intensidad, porque es un costo único al montar y sin ella los metales
 * (sonotrodo, varilla, placa) se ven casi negros; las sombras por mapa de
 * sombras van sólo en `high`.
 *
 * `high`: materiales físicos, luz de entorno, sombra suave y resolución hasta
 * 2×. `medium`: materiales físicos y luz de entorno iguales, sin mapa de
 * sombras (una mancha de contacto dibujada la sustituye) y resolución hasta
 * 1,5×. El nivel `low` no tiene escena: es la imagen fija de la página.
 */
export type Tier = 'high' | 'medium';

export interface ShowroomOptions {
  /** Por defecto `high` (la portada). */
  tier?: Tier;
}

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
  /**
   * Giro del equipo sobre su eje vertical, en radianes sobre su giro de
   * presentación, con un seguimiento suave; `null` vuelve al vaivén de reposo.
   */
  setYaw(offset: number | null): void;
  /** Baja (o sube) el nivel de calidad sin rehacer la escena. */
  setTier(tier: Tier): void;
  /**
   * Activa o desactiva el giro con el puntero. Desactivado, arrastrar sobre el
   * lienzo no mueve la cámara ni corta su viaje: en la página de producto el
   * gesto desplaza la página.
   */
  setControls(enabled: boolean): void;
  /** Posición en píxeles del lienzo de cada ancla, o `null` si queda detrás. */
  onFrame(callback: (anchors: AnchorPositions) => void): void;
  /** Cambia el equipo en el mismo escenario (un solo lienzo, un solo WebGL). */
  setModel(model: BuiltModel): void;
  /**
   * Fija el giro en el acto, sin seguimiento suave. Sólo para los pósteres y las
   * tiras de giro (`scripts/generate-posters.mjs`, `?poster=1`): con
   * `html[data-poster]` la escena se publica en `window.__studio`.
   */
  snapYaw(offset: number): void;
  /**
   * El navegador retiró el contexto WebGL (GPU reiniciada, demasiados
   * contextos, pestaña en segundo plano en un teléfono). El bucle ya se paró;
   * quien monta la escena la libera y vuelve a la imagen fija. No se intenta
   * rehacer la luz de entorno en el mismo contexto.
   */
  onContextLost(callback: () => void): void;
  /**
   * Libera la escena. `discard`: el lienzo se descarta y además se suelta su
   * contexto (`forceContextLoss`); sin él, el lienzo puede volver a usarse
   * («Ver en 3D», el regreso desde la caché).
   */
  dispose(discard?: boolean): void;
}

const MM = 0.01;

export function createShowroom(canvas: HTMLCanvasElement, initial: BuiltModel, options: ShowroomOptions = {}): Showroom {
  // En pantallas pequeñas (teléfonos) se dibuja con menos resolución y sombra.
  const isSmall = () => Math.min(window.innerWidth, window.innerHeight) < 700;
  let small = isSmall();
  let tier: Tier = options.tier ?? 'high';
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: !small, alpha: true });
  const pixelRatio = () => Math.min(window.devicePixelRatio, small || tier === 'medium' ? 1.5 : 2);
  renderer.setPixelRatio(pixelRatio());
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
  // OrbitControls fija `touch-action: none` en el lienzo y así un dedo sobre el
  // equipo no desplaza la página; se devuelve el desplazamiento vertical al
  // navegador y el giro queda con el arrastre horizontal.
  canvas.style.touchAction = 'pan-y';
  controls.minPolarAngle = 0.25;
  controls.maxPolarAngle = 1.52;

  const key = new THREE.DirectionalLight(0xffffff, 2.4);
  key.position.set(-4, 9, 6);
  scene.add(key);
  // Sombra de contacto: luz cenital de intensidad 0 que sólo proyecta la sombra
  // (`ShadowMaterial` la lee sin importar la intensidad). Cae bajo el equipo.
  const shade = new THREE.DirectionalLight(0xffffff, 0);
  shade.position.set(0.5, 10, 0.8);
  shade.castShadow = true;
  shade.shadow.mapSize.set(small ? 1024 : 2048, small ? 1024 : 2048);
  Object.assign(shade.shadow.camera, { left: -5, right: 5, top: 5, bottom: -5 });
  shade.shadow.bias = -0.0004;
  scene.add(shade);
  const fill = new THREE.DirectionalLight(0xdfe8ff, 0.7);
  fill.position.set(6, 3, 4);
  scene.add(fill);
  const rim = new THREE.DirectionalLight(0xffffff, 1.2);
  rim.position.set(2, 5, -7);
  scene.add(rim);

  const ground = new THREE.Mesh(new THREE.PlaneGeometry(40, 40), new THREE.ShadowMaterial({ color: 0x0d3b36, opacity: 0.16 }));
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  scene.add(ground);

  // Mancha difusa teñida de teal: con mapa de sombras es el halo alrededor de la
  // sombra de contacto; sin él (niveles medio y bajo) es la única que asienta el equipo.
  const blobCanvas = document.createElement('canvas');
  blobCanvas.width = blobCanvas.height = 128;
  const g = blobCanvas.getContext('2d')!;
  const fade = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  fade.addColorStop(0, 'rgba(13,59,54,.30)');
  fade.addColorStop(0.55, 'rgba(13,59,54,.12)');
  fade.addColorStop(1, 'rgba(13,59,54,0)');
  g.fillStyle = fade;
  g.fillRect(0, 0, 128, 128);
  const blobTexture = new THREE.CanvasTexture(blobCanvas);
  const blob = new THREE.Mesh(
    new THREE.PlaneGeometry(1, 1),
    new THREE.MeshBasicMaterial({ map: blobTexture, transparent: true, depthWrite: false }),
  );
  blob.rotation.x = -Math.PI / 2;
  blob.position.y = 0.002;
  scene.add(blob);

  const applyTier = () => {
    shade.castShadow = tier === 'high';
    ground.visible = tier === 'high';
    (blob.material as THREE.MeshBasicMaterial).opacity = tier === 'high' ? 0.6 : 1;
    renderer.setPixelRatio(pixelRatio());
  };
  applyTier();

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
    const foot = new THREE.Box3().setFromObject(next.root).getSize(new THREE.Vector3());
    blob.scale.set(foot.x * 1.5, foot.z * 1.5, 1);
  };
  mount(initial);

  const general = () => {
    const at = framed.getCenter(new THREE.Vector3());
    const half = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    const across = Math.hypot(framedSize.x, framedSize.z) * 0.8;
    const distance = Math.max(framedSize.y / 2 / half, across / 2 / (half * camera.aspect)) * (model.frameMargin ?? 1.18);
    return { at, pos: at.clone().add(new THREE.Vector3(0.32, 0.18, 1).normalize().multiplyScalar(distance)) };
  };
  const viewFor = (name: string) => {
    const view = model.views[name];
    if (!view) return general();
    const at = world(view.target);
    return { at, pos: at.clone().addScaledVector(worldDir(view.direction).normalize(), view.distance * MM) };
  };

  // El destino se recalcula en cada cuadro: si el equipo gira durante el viaje
  // (`setYaw`), la cámara llega igual a la pieza que buscaba.
  let tween: { fromP: THREE.Vector3; fromT: THREE.Vector3; view: string; t: number; ms: number; smooth: boolean } | null = null;
  let idle = true;
  let yawOffset: number | null = null;
  const startTween = (name: string, ms: number, smooth: boolean) => {
    idle = name === 'general' && idle;
    tween = { fromP: camera.position.clone(), fromT: controls.target.clone(), view: name, t: 0, ms, smooth };
  };
  const goTo = (name: string) => startTween(name, 1200, false);
  const onPointerDown = () => {
    if (!controls.enabled) return;
    idle = false;
    tween = null;
  };
  canvas.addEventListener('pointerdown', onPointerDown);

  const resize = () => {
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (!w || !h) return;
    // Un giro de pantalla o una ventana que cambia de tamaño puede cruzar el umbral de «pequeña».
    const nowSmall = isSmall();
    if (nowSmall !== small) {
      small = nowSmall;
      renderer.setPixelRatio(pixelRatio());
      shade.shadow.mapSize.set(small ? 1024 : 2048, small ? 1024 : 2048);
      shade.shadow.map?.dispose();
      shade.shadow.map = null;
    }
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
  let lost = false;
  let lostCallback: (() => void) | null = null;
  const onContextLost = (event: Event) => {
    // Sin `preventDefault` el navegador no devolvería nunca el contexto.
    event.preventDefault();
    lost = true;
    cancelAnimationFrame(raf);
    raf = 0;
    lostCallback?.();
  };
  canvas.addEventListener('webglcontextlost', onContextLost);

  const frame = (now: number) => {
    raf = 0;
    const dt = Math.min((now - last) / 1000, 0.05);
    last = now;
    t += dt;
    level = levelOverride ?? level + ((powered ? 1 : 0) - level) * Math.min(1, dt * 3);

    if (tween) {
      tween.t = Math.min(1, tween.t + Math.max(dt, 1 / 60) / (tween.ms / 1000));
      const e = tween.smooth ? tween.t * tween.t * (3 - 2 * tween.t) : 1 - Math.pow(1 - tween.t, 3);
      const v = viewFor(tween.view);
      camera.position.lerpVectors(tween.fromP, v.pos, e);
      controls.target.lerpVectors(tween.fromT, v.at, e);
      if (tween.t >= 1) tween = null;
    }
    // Giro fijado por la página, o vaivén de reposo; los dos con seguimiento
    // suave y por el camino corto, para que pasar de uno a otro no salte.
    const aim = yawOffset !== null ? yaw + yawOffset : idle && !tween ? yaw + Math.sin(t * 0.25) * 0.18 : null;
    if (aim !== null) {
      const d = aim - stage.rotation.y;
      stage.rotation.y += Math.atan2(Math.sin(d), Math.cos(d)) * Math.min(1, dt * 6);
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
    if (raf || lost) return;
    last = performance.now();
    raf = requestAnimationFrame(frame);
  }
  const onVisibility = () => {
    if (!document.hidden && visible) start();
  };
  document.addEventListener('visibilitychange', onVisibility);
  start();

  const api: Showroom = {
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
      yawOffset = null;
      mount(next);
      idle = true;
      tween = { fromP: camera.position.clone(), fromT: controls.target.clone(), view: 'general', t: 0, ms: 1200, smooth: false };
      start();
    },
    goTo,
    tweenTo: (name, ms = 800) => startTween(name, ms, true),
    setLevel(next) {
      levelOverride = next === null ? null : Math.min(1, Math.max(0, next));
      if (levelOverride !== null) level = levelOverride;
    },
    setYaw(offset) {
      yawOffset = offset;
      if (offset === null) idle = true;
      start();
    },
    snapYaw(offset) {
      yawOffset = offset;
      stage.rotation.y = yaw + offset;
      start();
    },
    setControls(enabled) {
      controls.enabled = enabled;
    },
    setTier(next) {
      if (next === tier) return;
      tier = next;
      applyTier();
      resize();
    },
    onFrame(callback) {
      frameCallback = callback;
    },
    onContextLost(callback) {
      lostCallback = callback;
    },
    dispose(discard = false) {
      lost = true;
      cancelAnimationFrame(raf);
      raf = 0;
      observer.disconnect();
      io.disconnect();
      document.removeEventListener('visibilitychange', onVisibility);
      canvas.removeEventListener('pointerdown', onPointerDown);
      canvas.removeEventListener('webglcontextlost', onContextLost);
      controls.dispose();
      model.dispose();
      environment.dispose();
      pmrem.dispose();
      ground.geometry.dispose();
      (ground.material as THREE.Material).dispose();
      blob.geometry.dispose();
      (blob.material as THREE.Material).dispose();
      blobTexture.dispose();
      shade.shadow.dispose();
      renderer.dispose();
      if (discard) renderer.forceContextLoss();
    },
  };
  // Pósteres y tiras de giro: el script de pósteres gira el equipo cuadro a cuadro.
  if (document.documentElement.hasAttribute('data-poster')) (window as unknown as { __studio?: Showroom }).__studio = api;
  return api;
}
