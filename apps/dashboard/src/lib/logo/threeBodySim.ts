/**
 * Real 2D three-body gravitational simulation (Velocity-Verlet).
 * Initial conditions: equal-mass figure-eight choreography (Moore / Chenciner).
 *
 * Visual styling (rings, glow, broken trails) lives in components — not here.
 */

export type Vec2 = [number, number];
export type History = Float64Array; // flat [step * 3 * 2]

const BODIES = 3;

export interface SimConfig {
  steps: number;
  dt: number;
  gravConst: number;
  softening: number;
}

export const DEFAULT_SIM: SimConfig = {
  steps: 12000,
  dt: 0.0035,
  gravConst: 1.0,
  softening: 0.02,
};

function acceleration(
  positions: Float64Array,
  masses: Float64Array,
  g: number,
  eps: number,
): Float64Array {
  const acc = new Float64Array(BODIES * 2);
  for (let i = 0; i < BODIES; i++) {
    for (let j = 0; j < BODIES; j++) {
      if (i === j) continue;
      const dx = positions[j * 2] - positions[i * 2];
      const dy = positions[j * 2 + 1] - positions[i * 2 + 1];
      const distSq = dx * dx + dy * dy + eps * eps;
      const invDistCube = 1 / distSq ** 1.5;
      acc[i * 2] += g * masses[j] * dx * invDistCube;
      acc[i * 2 + 1] += g * masses[j] * dy * invDistCube;
    }
  }
  return acc;
}

/** Run simulation; returns flat history length steps * 3 * 2. */
export function simulateThreeBody(cfg: SimConfig = DEFAULT_SIM): Float64Array {
  const positions = new Float64Array([
    0.97000436, -0.24308753,
    -0.97000436, 0.24308753,
    0.0, 0.0,
  ]);
  const velocities = new Float64Array([
    0.466203685, 0.43236573,
    0.466203685, 0.43236573,
    -0.93240737, -0.86473146,
  ]); // figure-eight choreography (Moore / Chenciner)
  const masses = new Float64Array([1, 1, 1]);
  const history = new Float64Array(cfg.steps * BODIES * 2);

  let acc = acceleration(positions, masses, cfg.gravConst, cfg.softening);

  for (let s = 0; s < cfg.steps; s++) {
    const base = s * BODIES * 2;
    for (let i = 0; i < BODIES * 2; i++) history[base + i] = positions[i];

    for (let i = 0; i < BODIES * 2; i++) {
      positions[i] += velocities[i] * cfg.dt + 0.5 * acc[i] * cfg.dt * cfg.dt;
    }
    const newAcc = acceleration(positions, masses, cfg.gravConst, cfg.softening);
    for (let i = 0; i < BODIES * 2; i++) {
      velocities[i] += 0.5 * (acc[i] + newAcc[i]) * cfg.dt;
    }
    acc = newAcc;
  }

  return history;
}

/**
 * Center, scale, rotate, and lightly stretch for logo composition (not physics).
 * Slightly wider horizontal spread keeps bodies from clustering in a tight central band.
 */
export function normalizeHistory(
  history: Float64Array,
  targetRadius = 2.28,
  stretchX = 1.1,
  stretchY = 0.94,
): Float64Array {
  const steps = history.length / (BODIES * 2);
  let cx = 0;
  let cy = 0;
  const n = steps * BODIES;
  for (let s = 0; s < steps; s++) {
    for (let b = 0; b < BODIES; b++) {
      const i = (s * BODIES + b) * 2;
      cx += history[i];
      cy += history[i + 1];
    }
  }
  cx /= n;
  cy /= n;

  let maxR = 0;
  const out = new Float64Array(history.length);
  for (let i = 0; i < history.length; i += 2) {
    const x = history[i] - cx;
    const y = history[i + 1] - cy;
    out[i] = x;
    out[i + 1] = y;
    maxR = Math.max(maxR, Math.hypot(x, y));
  }
  const scale = targetRadius / maxR;

  const rad = (-22 * Math.PI) / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);

  for (let i = 0; i < out.length; i += 2) {
    const x = out[i] * scale * stretchX;
    const y = out[i + 1] * scale * stretchY;
    out[i] = x * cos - y * sin;
    out[i + 1] = x * sin + y * cos;
  }
  return out;
}

/** Index of best loop closure after minStep (seamless period). */
export function findLoopEnd(history: Float64Array, minStep = 400): number {
  const ref = new Float64Array(BODIES * 2);
  for (let i = 0; i < BODIES * 2; i++) ref[i] = history[i];

  const steps = history.length / (BODIES * 2);
  let bestI = minStep;
  let bestErr = Infinity;

  for (let s = minStep; s < steps; s++) {
    const base = s * BODIES * 2;
    let err = 0;
    for (let i = 0; i < BODIES * 2; i++) {
      const d = history[base + i] - ref[i];
      err += d * d;
    }
    err = Math.sqrt(err);
    if (err < bestErr) {
      bestErr = err;
      bestI = s;
    }
  }
  return bestI + 1;
}

export function getBodyXY(history: Float64Array, step: number, body: number): Vec2 {
  const i = (step * BODIES + body) * 2;
  return [history[i], history[i + 1]];
}

/** Resample one period to N display frames (for canvas schedule). */
export function buildFrameIndices(_history: Float64Array, loopEnd: number, nFrames: number): number[] {
  const indices: number[] = [];
  for (let f = 0; f < nFrames; f++) {
    indices.push(Math.round((f / Math.max(nFrames - 1, 1)) * (loopEnd - 1)));
  }
  return indices;
}

/** Downsample polyline for SVG path `d`. */
/** Sample a segment of the orbit (for premium accent arcs, not full loops). */
export function samplePathSegment(
  history: Float64Array,
  body: number,
  loopEnd: number,
  fracStart: number,
  fracEnd: number,
  maxPoints = 20,
): Vec2[] {
  const start = Math.floor(loopEnd * fracStart);
  const end = Math.floor(loopEnd * fracEnd);
  const pts: Vec2[] = [];
  const span = Math.max(end - start, 1);
  const stride = Math.max(1, Math.floor(span / maxPoints));
  for (let s = start; s <= end; s += stride) {
    pts.push(getBodyXY(history, Math.min(s, loopEnd - 1), body));
  }
  const last = getBodyXY(history, Math.min(end, loopEnd - 1), body);
  const p = pts[pts.length - 1];
  if (!p || p[0] !== last[0] || p[1] !== last[1]) pts.push(last);
  return pts;
}

export function sampleBodyPath(
  history: Float64Array,
  body: number,
  loopEnd: number,
  maxPoints = 48,
): Vec2[] {
  const pts: Vec2[] = [];
  const stride = Math.max(1, Math.floor(loopEnd / maxPoints));
  for (let s = 0; s < loopEnd; s += stride) {
    pts.push(getBodyXY(history, s, body));
  }
  const last = getBodyXY(history, loopEnd - 1, body);
  if (pts[pts.length - 1][0] !== last[0] || pts[pts.length - 1][1] !== last[1]) {
    pts.push(last);
  }
  return pts;
}

export function pointsToSvgPath(points: Vec2[], close = false): string {
  if (points.length === 0) return '';
  let d = `M ${points[0][0].toFixed(3)} ${points[0][1].toFixed(3)}`;
  for (let i = 1; i < points.length; i++) {
    d += ` L ${points[i][0].toFixed(3)} ${points[i][1].toFixed(3)}`;
  }
  if (close) d += ' Z';
  return d;
}
