/**
 * Precomputed mark geometry from the real three-body simulation.
 * Used by static SVG components and as reduced-motion canvas fallback.
 */

import { ATOM_RING_RX, ATOM_RING_RY } from './composition';
import {
  buildFrameIndices,
  findLoopEnd,
  getBodyXY,
  normalizeHistory,
  pointsToSvgPath,
  sampleBodyPath,
  samplePathSegment,
  simulateThreeBody,
  type Vec2,
} from './threeBodySim';

export type MarkSolution = 'premium' | 'trace' | 'minimal';

export interface MarkGeometry {
  loopEnd: number;
  bodyPaths: [string, string, string];
  ghostPaths?: [string, string, string];
  bodyPositions: [Vec2, Vec2, Vec2];
  ringSpecs: { rx: number; ry: number; angle: number }[];
}

let cachedHistory: { history: Float64Array; loopEnd: number } | null = null;

function getSimCache(loopSeconds: number, fps: number) {
  if (!cachedHistory) {
    const history = normalizeHistory(simulateThreeBody());
    const loopEnd = findLoopEnd(history);
    cachedHistory = { history, loopEnd };
  }
  const nFrames = Math.max(Math.round(loopSeconds * fps), 1);
  const frameIndices = buildFrameIndices(cachedHistory.history, cachedHistory.loopEnd, nFrames);
  return { ...cachedHistory, frameIndices };
}

export function resetSimCache(): void {
  cachedHistory = null;
}

/** Electrons spaced around the orbit (outer lobes), not clustered at loop mid. */
function spreadElectronPositions(
  history: Float64Array,
  loopEnd: number,
): [Vec2, Vec2, Vec2] {
  const fracs = [0.13, 0.47, 0.81];
  return fracs.map((f, b) => getBodyXY(history, Math.floor(loopEnd * f), b)) as [Vec2, Vec2, Vec2];
}

export function getMarkGeometry(solution: MarkSolution): MarkGeometry {
  const { history, loopEnd } = getSimCache(14, 30);
  const bodyPositions = spreadElectronPositions(history, loopEnd);

  if (solution === 'premium') {
    // Short outer-lobe accents only, no full ghost loops (reads squiggly at 32px)
    const segments: [number, number][] = [
      [0.1, 0.2],
      [0.42, 0.52],
      [0.74, 0.84],
    ];
    const bodyPaths = segments.map(([a, b], i) =>
      pointsToSvgPath(samplePathSegment(history, i, loopEnd, a, b, 10)),
    ) as [string, string, string];

    return {
      loopEnd,
      bodyPaths,
      bodyPositions,
      ringSpecs: [
        { rx: ATOM_RING_RX, ry: ATOM_RING_RY, angle: 0 },
        { rx: ATOM_RING_RX, ry: ATOM_RING_RY, angle: 60 },
        { rx: ATOM_RING_RX, ry: ATOM_RING_RY, angle: -60 },
      ],
    };
  }

  const maxPts = solution === 'minimal' ? 24 : 56;
  const bodyPaths = [0, 1, 2].map((b) =>
    pointsToSvgPath(sampleBodyPath(history, b, loopEnd, maxPts)),
  ) as [string, string, string];

  const ringSpecs =
    solution === 'trace'
      ? [{ rx: ATOM_RING_RX, ry: ATOM_RING_RY, angle: 0 }]
      : [{ rx: ATOM_RING_RX * 0.92, ry: ATOM_RING_RY * 0.92, angle: 0 }];

  return { loopEnd, bodyPaths, bodyPositions, ringSpecs };
}

export function getSimHistoryForCanvas(loopSeconds: number, fps: number) {
  return getSimCache(loopSeconds, fps);
}

export const MARK_NOTES: Record<
  MarkSolution,
  { title: string; bestFor: string; recommended: string; description: string }
> = {
  premium: {
    title: 'Premium Atom',
    recommended: 'Recommended for header',
    bestFor: 'Header, footer, general brand lockups',
    description:
      'Compact atom: bold nucleus, three orbit ellipses, spaced electrons, short lobe accents. Clear at 32–40px.',
  },
  trace: {
    title: 'Mathematical Trace',
    recommended: 'Recommended for hero animation / technical brand motion',
    bestFor: 'Hero animation, technical storytelling',
    description:
      'Simulation paths are the identity. Lighter atom frame: motion and trails carry the mark.',
  },
  minimal: {
    title: 'Minimal Scientific',
    recommended: 'Recommended for favicon / tiny use',
    bestFor: 'Favicon, app icon, tiny UI slots',
    description:
      'Stripped to nucleus + three bodies + one orbit hint. Readable at 16–32px.',
  },
};
