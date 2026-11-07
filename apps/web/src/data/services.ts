/**
 * Servicios y soporte confirmados por el negocio.
 */
export interface ServiceOffering {
  id: string;
  name: string;
  shortDescription: string;
}

export const serviceOfferings: ServiceOffering[] = [
  {
    id: '1',
    name: 'Soporte',
    shortDescription: 'Respuesta a dudas de uso y coordinación cuando el equipo lo requiere.',
  },
  {
    id: '2',
    name: 'Asesorías',
    shortDescription: 'Ayuda para acotar opciones antes de comprar; sin compromiso de compra.',
  },
  {
    id: '3',
    name: 'Instalación',
    shortDescription: 'Cuando el equipo lo exige; alcance acordado por escrito.',
  },
  {
    id: '4',
    name: 'Puesta en marcha',
    shortDescription: 'Equipos más complejos: puesta en marcha según equipo y acuerdo comercial.',
  },
];
