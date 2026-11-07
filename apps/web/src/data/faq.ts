/**
 * Preguntas frecuentes — respuestas genéricas y veraces; sin plazos ni marcas inventadas.
 */
import { publicClaim } from './claims';
import { company } from './company';
import { contact } from './contact';

export interface FaqItem {
  id: string;
  question: string;
  answer: string;
}

export const faqItems: FaqItem[] = [
  {
    id: '1',
    question: '¿Cómo solicito una cotización?',
    answer:
      'Escríbanos a ' +
      contact.email +
      ' o por WhatsApp al ' +
      contact.phoneDisplay +
      '. Indique categoría (alimentos, control de calidad o laboratorio clínico), aplicación o equipo de referencia si la tiene, y su institución o ciudad. Con eso podemos orientar la siguiente respuesta.',
  },
  {
    id: '2',
    question: '¿Qué tipo de clientes atienden?',
    answer:
      company.audience.join(', ') +
      '. Atención en todo ' +
      company.geography +
      '.',
  },
  {
    id: '3',
    question: '¿Tienen catálogos o fichas técnicas?',
    answer: company.catalogNote,
  },
  {
    id: '4',
    question: '¿Cómo los contacto y en qué horario?',
    answer:
      'Correo ' +
      contact.email +
      ', teléfono y WhatsApp ' +
      contact.phoneDisplay +
      '. Ubicación: ' +
      contact.locationPublic +
      '. Horario de referencia: lunes a viernes ' +
      contact.hours +
      ' (confirmar por el canal que use).',
  },
];

export interface BuyingFaq {
  q: string;
  a: string;
}

/**
 * Preguntas de compra para la ficha del modelo (tablero «Modelo»). Cada
 * respuesta que cita una afirmación la toma de `publicClaim`, y una pregunta
 * se descarta cuando su afirmación no está aprobada.
 *
 * Excepción deliberada: «¿Cómo se paga?». El abono 50/50 no está aprobado para
 * el sitio público, así que la pregunta no se descarta sino que responde
 * «Condiciones de pago en la cotización.» y sólo cita la proporción si el
 * registro la aprueba. No hay pregunta de garantía: el sitio no la declara.
 *
 * `resolve` es la puerta de las afirmaciones; las pruebas pasan otra.
 */
export function buyingFaqs(resolve: (id: string) => string | undefined = publicClaim): BuyingFaq[] {
  const faqs: BuyingFaq[] = [];

  const sameDay = resolve('cotizacion-mismo-dia-habil');
  if (sameDay) {
    faqs.push({
      q: '¿Cuánto demora la cotización?',
      a: `${sameDay}. Cuéntenos su aplicación para ajustar la cotización al equipo.`,
    });
  }
  if (resolve('despacho-todo-chile')) {
    faqs.push({ q: '¿Despachan fuera de Santiago?', a: 'Despachamos a todo Chile.' });
  }
  if (resolve('puesta-en-marcha-con-fabrica')) {
    faqs.push({
      q: '¿Incluye puesta en marcha y capacitación?',
      a: 'Ofrecemos puesta en marcha y capacitación con fábrica. Las condiciones quedan indicadas en su cotización.',
    });
  }
  const payment = resolve('pago-50-50');
  faqs.push({
    q: '¿Cómo se paga?',
    a: payment ? `${payment}.` : 'Condiciones de pago en la cotización.',
  });
  if (resolve('vende-mercado-publico')) {
    faqs.push({
      q: '¿Pueden vender por Mercado Público?',
      a: 'Sí, vendemos por Mercado Público y participamos en licitaciones. Indíquenos el número de la licitación al cotizar.',
    });
  }

  return faqs;
}
