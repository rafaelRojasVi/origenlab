/**
 * Canvas brightness / contrast per surface.
 * Header: transparent canvas (no square tile), light teal on #042f2e.
 */

import { LOGO_PALETTE } from './palette';
import type { MotionContext } from './motionPresets';

export interface CanvasVisualProfile {
  ringColor: string;
  ringAlpha: number;
  ringLinePx: number;
  nucleusFill: string;
  nucleusRingColor: string;
  nucleusRingAlpha: number;
  bodyCoreColors: readonly [string, string, string];
  bodyGlowColors: readonly [string, string, string];
  bodyGlowAlphaMul: number;
  bodyCoreStroke: string;
  trailGlowMul: number;
  trailCoreMul: number;
  /** No fill rect, blends into header bar */
  transparentBg: boolean;
  /** Fewer halo layers (cleaner electrons) */
  compactBodies: boolean;
}

const DEFAULT_PROFILE: CanvasVisualProfile = {
  ringColor: LOGO_PALETTE.brand500,
  ringAlpha: 0.18,
  ringLinePx: 1.35,
  nucleusFill: LOGO_PALETTE.brand100,
  nucleusRingColor: LOGO_PALETTE.brand400,
  nucleusRingAlpha: 0.16,
  bodyCoreColors: [
    LOGO_PALETTE.brand100,
    LOGO_PALETTE.brand200,
    LOGO_PALETTE.brand300,
  ],
  bodyGlowColors: [
    LOGO_PALETTE.brand300,
    LOGO_PALETTE.brand400,
    LOGO_PALETTE.brand500,
  ],
  bodyGlowAlphaMul: 1,
  bodyCoreStroke: LOGO_PALETTE.brand50,
  trailGlowMul: 1,
  trailCoreMul: 1,
  transparentBg: false,
  compactBodies: false,
};

/** Production site header, integrated on #042f2e */
const HEADER_PROFILE: CanvasVisualProfile = {
  ringColor: LOGO_PALETTE.brand500,
  ringAlpha: 0.24,
  ringLinePx: 1.12,
  nucleusFill: LOGO_PALETTE.brand50,
  nucleusRingColor: LOGO_PALETTE.brand300,
  nucleusRingAlpha: 0.2,
  bodyCoreColors: [
    LOGO_PALETTE.brand100,
    LOGO_PALETTE.brand200,
    LOGO_PALETTE.brand300,
  ],
  bodyGlowColors: [
    LOGO_PALETTE.brand300,
    LOGO_PALETTE.brand400,
    LOGO_PALETTE.brand500,
  ],
  bodyGlowAlphaMul: 0.55,
  bodyCoreStroke: LOGO_PALETTE.brand50,
  trailGlowMul: 0.75,
  trailCoreMul: 0.8,
  transparentBg: true,
  compactBodies: true,
};

/** Logo-lab favicon motion demo, light atom on dark tile */
const FAVICON_PROFILE: CanvasVisualProfile = {
  ringColor: LOGO_PALETTE.brand200,
  ringAlpha: 0.34,
  ringLinePx: 1.05,
  nucleusFill: LOGO_PALETTE.brand50,
  nucleusRingColor: LOGO_PALETTE.brand100,
  nucleusRingAlpha: 0.28,
  bodyCoreColors: [
    LOGO_PALETTE.brand50,
    LOGO_PALETTE.brand100,
    LOGO_PALETTE.brand200,
  ],
  bodyGlowColors: [
    LOGO_PALETTE.brand200,
    LOGO_PALETTE.brand300,
    LOGO_PALETTE.brand400,
  ],
  bodyGlowAlphaMul: 0.5,
  bodyCoreStroke: LOGO_PALETTE.brand50,
  trailGlowMul: 0.65,
  trailCoreMul: 0.7,
  transparentBg: false,
  compactBodies: true,
};

export function getVisualProfile(motionContext?: MotionContext): CanvasVisualProfile {
  if (motionContext === 'header') return HEADER_PROFILE;
  if (motionContext === 'favicon') return FAVICON_PROFILE;
  return DEFAULT_PROFILE;
}
