/**
 * Motion tuning for logo-lab previews only (not production header).
 * Same physics; different loop length and trail density per surface.
 */

export type MotionContext = 'hero' | 'header' | 'footer' | 'surface' | 'favicon';

export interface MotionPreset {
  loopSeconds: number;
  trailLength: number;
  label: string;
}

export const MOTION_PRESETS: Record<MotionContext, MotionPreset> = {
  hero: {
    loopSeconds: 14,
    trailLength: 210,
    label: 'Full hero motion',
  },
  header: {
    loopSeconds: 18,
    trailLength: 68,
    label: 'Compact header, restrained motion, brighter atom',
  },
  footer: {
    loopSeconds: 17,
    trailLength: 82,
    label: 'Footer, subtle',
  },
  surface: {
    loopSeconds: 16,
    trailLength: 92,
    label: 'Light surface, subtle',
  },
  favicon: {
    loopSeconds: 22,
    trailLength: 32,
    label: 'Tiny demo, barely-there',
  },
};

export function getMotionPreset(context: MotionContext): MotionPreset {
  return MOTION_PRESETS[context];
}
