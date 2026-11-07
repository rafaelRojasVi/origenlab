/**
 * Contrato entre un equipo modelado y la escena de estudio.
 *
 * Cada archivo de `models/` exporta `build(): BuiltModel` y nada más. El modelo
 * se construye en milímetros, apoyado en el suelo (y = 0); la escena lo reduce
 * a su escala, lo centra, lo ilumina y le pone cámara.
 */
import type * as THREE from 'three';

export interface ModelView {
  /** Pieza a la que mira la cámara. */
  target: THREE.Object3D;
  /** Desde dónde se mira, en el marco del modelo (se normaliza). */
  direction: THREE.Vector3;
  /** Distancia de la cámara al objetivo, en milímetros. */
  distance: number;
}

export interface BuiltModel {
  /** Todo el conjunto, en milímetros. */
  root: THREE.Group;
  /** Puntos que la página señala con un marcador numerado. */
  anchors: Record<string, THREE.Object3D>;
  /** Vistas con nombre además de la general, que la escena calcula sola. */
  views: Record<string, ModelView>;
  /**
   * Altura en milímetros por encima de la cual la vista general no encuadra
   * (una varilla de soporte, una tapa abierta). Sin valor, encuadra todo.
   */
  frameTop?: number;
  /** Giro de presentación en radianes (por defecto 0,55). */
  yaw?: number;
  /** Avanza la animación; `level` va de 0 (en reposo) a 1 (en marcha). */
  update(dt: number, t: number, level: number): void;
  setPower(on: boolean): void;
  /**
   * Fija la cifra de la pantalla del equipo (p. ej. la amplitud que marca el
   * scroll); `null` la devuelve a su animación propia. Opcional: sólo los
   * equipos con pantalla dibujada la implementan.
   */
  setReadout?(percent: number | null): void;
  dispose(): void;
}

/** Opciones de construcción; un modelo puede ignorarlas. */
export interface BuildOptions {
  /** Nivel de calidad medio: menos partículas (burbujas, chispas). */
  lite?: boolean;
}

export type ModelModule = { build: (options?: BuildOptions) => BuiltModel };
