/**
 * Aplicaciones: seis tareas de laboratorio, no seis mercados.
 *
 * `/aplicaciones/` pregunta «qué necesita hacer con la muestra», que es la
 * pregunta con la que llega un laboratorio, y `/categorias/*` pregunta «qué
 * tipo de laboratorio es usted», que es la que sirve para segmentar. Antes las
 * dos vivían en el mismo sitio y la página de aplicaciones repetía las tres
 * líneas comerciales: quien buscaba cómo romper una célula encontraba
 * «alimentos, control de calidad y laboratorio clínico».
 *
 * Cada aplicación tiene que llevar a equipo concreto y a la conversación
 * técnica. Una aplicación sin `familyIds` sería un callejón sin salida y
 * `validate:catalog` la bloquea.
 *
 * El orden es el aprobado en la revisión del 2026-09-07 y no es un flujo: una
 * tarea puede necesitar dos familias, y una familia sirve a varias tareas. La
 * primera familia de `familyIds` es la que encabeza la sección.
 */

export interface Application {
  id: string;
  /** Nombre de la tarea, en el lenguaje del laboratorio. */
  name: string;
  /** Ancla dentro de `/aplicaciones/`. */
  slug: string;
  /** Qué se está intentando hacer con la muestra. */
  task: string;
  /** Por qué el equipo importa para esa tarea. */
  detail: string;
  /** Lo que conviene traer a la conversación antes de cotizar. */
  bring: readonly string[];
  /** Familias de `equipmentScope.ts` que resuelven la tarea. Al menos una. */
  familyIds: readonly string[];
}

export const applications: readonly Application[] = [
  {
    id: 'ruptura-celular',
    name: 'Ruptura celular y extracción',
    slug: 'ruptura-celular',
    task: 'Abrir células o tejido para liberar lo que hay dentro, o arrastrar un compuesto fuera de una matriz sólida.',
    detail:
      'El ultrasonido de sonda hace el trabajo por cavitación: las burbujas que se forman y colapsan en el líquido rompen la pared celular sin abrasivos ni cambio de disolvente. Un dispersor de rotor y estátor llega a lo mismo por corte mecánico y conviene cuando la matriz es fibrosa y hay volumen. Los dos caminos existen y no compiten: dependen del tipo de muestra.',
    bring: [
      'Qué muestra es y en qué estado llega: cultivo, tejido, semilla, matriz vegetal.',
      'Volumen por lote y cuántos lotes al día.',
      'Si el compuesto que busca es sensible a la temperatura.',
      'Si el método tiene que escalar después a planta piloto.',
    ],
    familyIds: ['sonicacion', 'dispersion-homogeneizacion'],
  },
  {
    id: 'dispersion-homogeneizacion',
    name: 'Dispersión y homogeneización',
    slug: 'dispersion-homogeneizacion',
    task: 'Llevar una muestra a composición uniforme, o repartir un sólido o una segunda fase en un líquido que no la acepta sola.',
    detail:
      'En rotor y estátor el resultado lo decide la herramienta antes que el equipo: cada elemento declara su velocidad periférica y la finura que alcanza en suspensión y en emulsión. Por eso la conversación empieza por el medio y el tamaño de partícula que necesita, y no por el número de modelo.',
    bring: [
      'Viscosidad aproximada del medio.',
      'Tamaño de partícula o de gota que necesita alcanzar.',
      'Volumen por lote y si el trabajo es por lote o continuo.',
      'Si necesita trabajar en vacío para no arrastrar aire.',
    ],
    familyIds: ['dispersion-homogeneizacion', 'sonicacion'],
  },
  {
    id: 'centrifugacion-separacion',
    name: 'Centrifugación y separación',
    slug: 'centrifugacion-separacion',
    task: 'Separar fases o sedimentar un componente por diferencia de densidad, con o sin control de temperatura.',
    detail:
      'La elección se cierra con dos datos: el formato del tubo o de la placa, que fija el rotor, y la fuerza que pide el protocolo, que se expresa en veces g y no en revoluciones. La versión refrigerada deja de ser un extra cuando la muestra se degrada por encima de unos pocos grados.',
    bring: [
      'Formato y número de tubos, placas o botellas por corrida.',
      'La fuerza que pide el protocolo, en veces g.',
      'Si necesita control de temperatura y a qué valor.',
      'Espacio disponible de mesón y alimentación eléctrica.',
    ],
    familyIds: ['centrifugacion'],
  },
  {
    id: 'pesaje-humedad',
    name: 'Pesaje y análisis de humedad',
    slug: 'pesaje-humedad',
    task: 'Determinar masa con la resolución que exige el método, o determinar contenido de humedad por pérdida de masa al calentar.',
    detail:
      'En pesaje el dato que decide es la legibilidad, no la capacidad: bajar un orden de magnitud en el último dígito cambia la balanza, el entorno que necesita y el procedimiento de calibración. En humedad, el analizador termogravimétrico pesa y calienta en el mismo plato, de modo que entrega en minutos lo que la estufa entrega en horas.',
    bring: [
      'La legibilidad que exige su método, no sólo la capacidad máxima.',
      'Si necesita calibración interna y registro con fecha y hora.',
      'Para humedad: tipo de muestra y rango de temperatura de secado.',
      'Si el puesto de pesaje está en un banco estable o en planta.',
    ],
    familyIds: ['pesaje-humedad'],
  },
  {
    id: 'osmolalidad',
    name: 'Osmolalidad',
    slug: 'osmolalidad',
    task: 'Medir la concentración total de partículas disueltas en una muestra acuosa, o la concentración molal en un disolvente orgánico.',
    detail:
      'Se mide por descenso del punto de congelación: la muestra se sobreenfría, se provoca la cristalización y se lee la temperatura a la que congela, que baja en proporción a las partículas disueltas. Con 50 a 100 µl y minuto y medio hay resultado, y sin suministro de agua en el puesto.',
    bring: [
      'Tipo de muestra: suero, plasma, orina, solución parenteral, disolvente orgánico.',
      'Volumen de muestra que puede destinar a cada medición.',
      'Cuántas mediciones al día y si necesita imprimir el resultado.',
      'Si necesita registro por usuario y estado de calibración.',
    ],
    familyIds: ['osmometria'],
  },
  {
    id: 'electroforesis',
    name: 'Electroforesis',
    slug: 'electroforesis',
    task: 'Separar proteínas o fragmentos de ácido nucleico en gel, aplicando un campo eléctrico.',
    detail:
      'Son tres decisiones que tienen que encajar: el formato del gel, la cubeta que lo acepta y la fuente capaz de sostener el voltaje que ese formato pide. Un equipo aislado no resuelve nada si la fuente no llega, y los consumibles de montaje deciden si la corrida es reproducible.',
    bring: [
      'Qué separa: proteína o ácido nucleico.',
      'Técnica: SDS-PAGE, nativa, enfoque isoeléctrico, bidimensional o agarosa.',
      'Formato de gel que ya usa, o si parte de cero.',
      'Si necesita transferencia y documentación del gel.',
    ],
    familyIds: ['electroforesis'],
  },
];

export function applicationById(id: string): Application | undefined {
  return applications.find((entry) => entry.id === id);
}

/** Aplicaciones que se resuelven con una familia dada. */
export function applicationsForFamily(familyId: string): Application[] {
  return applications.filter((entry) => entry.familyIds.includes(familyId));
}
