/**
 * Asesoría técnica antes de cotizar.
 *
 * Hechos personales confirmados por la persona y por el negocio el 2026-09-06,
 * y sólo esos tres: nombre, profesión y cargo. El sitio **no** publica
 * biografía, años de experiencia, titulación concreta, retrato, testimonios ni
 * ningún otro dato personal. Añadir cualquiera de ellos exige confirmación
 * escrita de la persona, y una fotografía exige además un activo con derechos
 * en el repositorio: ver `docs/design/CONTENT_NEEDED.md`.
 *
 * Los canales de contacto son los corporativos de `contact.ts`. No se publica
 * un canal personal.
 */
export const consultant = {
  name: 'Tatiana Vivanco',
  profession: 'Bioquímica',
  role: 'Gerente de Ventas',
  company: 'OrigenLab',
} as const;

/**
 * Lo que conviene contar en el primer mensaje. Son las dimensiones que acotan
 * un equipo antes de pedir precio, no un formulario: el sitio no tiene
 * formularios y la consulta llega por correo o WhatsApp.
 */
export interface ConsultationInput {
  label: string;
  hint: string;
}

export const consultationInputs: readonly ConsultationInput[] = [
  {
    label: 'Aplicación',
    hint: 'Qué proceso o ensayo va a realizar con el equipo.',
  },
  {
    label: 'Muestra o material',
    hint: 'Con qué trabaja: matriz, viscosidad, temperatura, si es material delicado.',
  },
  {
    label: 'Volumen de trabajo',
    hint: 'Volumen por corrida o por lote, y formato de tubo, placa o recipiente.',
  },
  {
    label: 'Frecuencia de uso',
    hint: 'Uso puntual, diario o continuo. Cambia el equipo que conviene.',
  },
  {
    label: 'Capacidad requerida',
    hint: 'Cuántas muestras por corrida necesita procesar.',
  },
  {
    label: 'Condiciones de instalación',
    hint: 'Espacio disponible, alimentación eléctrica, ventilación y accesos.',
  },
  {
    label: 'Objetivo técnico',
    hint: 'Precisión, rango, norma o método asociado, si el método lo exige.',
  },
];

/**
 * Preguntas reales con las que llegan los laboratorios. Sustituyen a la prueba
 * social que el repositorio no puede sostener: hacen concreto el valor técnico
 * sin inventar clientes, casos ni testimonios.
 */
export const consultationQuestions: readonly string[] = [
  '¿Qué capacidad necesito?',
  '¿Qué sonda corresponde a mi volumen?',
  '¿Qué precisión requiere el método?',
  '¿Qué equipo se adapta a mi flujo de trabajo?',
];

/**
 * Cómo se llega de la consulta a la cotización.
 *
 * El paso final separa a propósito la respuesta técnica del plazo de entrega:
 * la disponibilidad, el flete y la confirmación de fábrica no dependen de
 * OrigenLab y no se prometen. Ninguna cifra de plazo aparece aquí; la promesa
 * numérica vive en `claims.ts` sin aprobar y no se renderiza.
 */
export interface ProcessStep {
  title: string;
  body: string;
}

export const consultationProcess: readonly ProcessStep[] = [
  {
    title: 'Nos cuenta su aplicación',
    body: 'Por correo o WhatsApp, con lo que tenga a mano. No hace falta traer un modelo de referencia ni una especificación cerrada.',
  },
  {
    title: 'Revisamos la necesidad técnica',
    body: 'Tatiana Vivanco revisa qué exige el proceso: muestra, volumen, capacidad, frecuencia y condiciones de instalación.',
  },
  {
    title: 'Acotamos la configuración o las alternativas',
    body: 'Comparamos las opciones que corresponden y descartamos las que no, con los accesorios que el uso realmente pide.',
  },
  {
    title: 'Preparamos la cotización formal',
    body: 'Propuesta por escrito con configuración, accesorios y condiciones comerciales confirmadas.',
  },
  {
    title: 'Confirmamos disponibilidad y entrega',
    body: 'Cuando el equipo lo requiere, se confirma con el fabricante antes de comprometer una fecha. No prometemos stock ni plazos sin esa confirmación.',
  },
];
