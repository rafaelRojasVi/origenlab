/**
 * Los equipos de la sala 3D, en el orden en que la sala de pruebas los muestra.
 * Lo que la página dice de cada uno vive en `content/<id>.ts`; su modelo, en
 * `models/<id>.ts`, que el navegador pide sólo cuando hace falta (`loaders.ts`).
 */
import type { ShowroomEntry } from './content/types';
import { entry as e0 } from './content/hielscher-up400st';
import { entry as e1 } from './content/ika-t25-digital';
import { entry as e2 } from './content/ortoalresa-digicen-22-r';
import { entry as e3 } from './content/ohaus-pioneer-px';
import { entry as e4 } from './content/adam-highland';
import { entry as e5 } from './content/loeser-i-osmometer';
import { entry as e6 } from './content/serva-bluevertical-prime';
import { entry as e7 } from './content/hielscher-up200st';
import { entry as e8 } from './content/hielscher-up100h';
import { entry as e9 } from './content/ohaus-mb120';
import { entry as e10 } from './content/ohaus-mb32';

export const showroomEntries: readonly ShowroomEntry[] = [e0, e1, e2, e3, e4, e5, e6, e7, e8, e9, e10];

export function showroomEntry(id: string): ShowroomEntry | undefined {
  return showroomEntries.find((entry) => entry.id === id);
}
