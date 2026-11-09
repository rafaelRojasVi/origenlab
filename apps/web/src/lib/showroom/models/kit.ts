/**
 * Piezas comunes a los equipos modelados: texturas dibujadas en un lienzo
 * (rótulos, pantallas), cajas redondeadas, sólidos de revolución y materiales
 * de laboratorio que se repiten de un fabricante a otro.
 *
 * Todo en milímetros. Quien construye un modelo registra lo que crea con
 * `keep` y lo libera en su `dispose`.
 */
import * as THREE from 'three';

export const SANS = '"Plus Jakarta Sans", ui-sans-serif, system-ui, sans-serif';

export type Draw = (g: CanvasRenderingContext2D, w: number, h: number) => void;

/** Una textura dibujada a mano; `redraw` la repinta (pantallas que cambian). */
export function canvasTexture(w: number, h: number, draw: Draw) {
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

/** Caja de esquinas redondeadas, centrada en el origen. */
export function roundedBox(w: number, h: number, d: number, r: number) {
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
    depth: Math.max(d - 2 * r, 0.01),
    bevelEnabled: true,
    bevelSize: r,
    bevelThickness: r,
    bevelSegments: 4,
  });
  g.translate(0, 0, -Math.max(d - 2 * r, 0.01) / 2);
  return g;
}

/** Sólido de revolución a partir de pares [radio, altura]. */
export function lathe(points: [number, number][], segments = 96) {
  return new THREE.LatheGeometry(
    points.map(([r, y]) => new THREE.Vector2(r, y)),
    segments,
  );
}

/**
 * Vidrio de laboratorio. Se simula con reflejo y poca opacidad: el vidrio por
 * transmisión de three.js oculta los objetos transparentes que tiene dentro
 * (una muestra, unas burbujas). Dibújelo con `renderOrder` mayor que lo que
 * contiene.
 */
export function labGlass() {
  return new THREE.MeshPhysicalMaterial({
    color: 0xf4fbfa,
    roughness: 0.03,
    clearcoat: 1,
    clearcoatRoughness: 0.02,
    envMapIntensity: 2.2,
    transparent: true,
    opacity: 0.22,
    side: THREE.DoubleSide,
    depthWrite: false,
  });
}

/** Lleva la cuenta de geometrías, materiales y texturas para liberarlas juntas. */
export function disposer() {
  const items: { dispose(): void }[] = [];
  return {
    keep<T extends { dispose(): void }>(item: T): T {
      items.push(item);
      return item;
    },
    dispose() {
      for (const item of items) item.dispose();
      items.length = 0;
    },
  };
}
