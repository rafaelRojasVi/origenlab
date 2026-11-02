/**
 * Adam Equipment Highland: pendiente de modelar. Marcador de posición del tamaño de un
 * equipo de mesa, para que la sala de pruebas funcione mientras tanto.
 */
import * as THREE from 'three';
import type { BuiltModel } from '../types';
import { disposer, roundedBox } from './kit';

export function build(): BuiltModel {
  const { keep, dispose } = disposer();
  const root = new THREE.Group();
  const block = new THREE.Mesh(keep(roundedBox(240, 200, 200, 12)), keep(new THREE.MeshStandardMaterial({ color: 0xc9ced6, roughness: 0.5 })));
  block.position.y = 100;
  block.castShadow = true;
  root.add(block);
  return { root, anchors: {}, views: {}, update() {}, setPower() {}, dispose };
}
