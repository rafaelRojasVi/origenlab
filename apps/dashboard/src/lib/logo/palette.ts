/** OrigenLab brand palette — mirrors `global.css` @theme tokens. */
export const LOGO_PALETTE = {
  brand50: '#f0fdfa',
  brand100: '#ccfbf1',
  brand200: '#99f6e4',
  brand300: '#5eead4',
  brand400: '#2dd4bf',
  brand500: '#14b8a6',
  brand600: '#0d9488',
  brand700: '#0f766e',
  brand800: '#115e59',
  brand900: '#134e4a',
  brand950: '#042f2e',
  white: '#ffffff',
} as const;

/** Fluorescent dot cores (animated + static). */
export const BODY_CORE_COLORS = [
  LOGO_PALETTE.brand100,
  LOGO_PALETTE.brand200,
  LOGO_PALETTE.brand300,
] as const;

/** Outer halos / trail glow. */
export const BODY_GLOW_COLORS = [
  LOGO_PALETTE.brand300,
  LOGO_PALETTE.brand400,
  LOGO_PALETTE.brand500,
] as const;
