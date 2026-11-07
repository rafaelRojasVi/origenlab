import { contact } from '../data/contact';

export interface WhatsAppQuoteOptions {
  productName?: string;
  brandName?: string;
}

/**
 * Mensaje prellenado de WhatsApp (tablero «Mensaje»). Nombra el modelo cuando
 * lo hay, o la marca si sólo se conoce esa, y no promete plazo ni condiciones:
 * es lo que la persona le dice a OrigenLab, no lo que OrigenLab le promete.
 * Con modelo y marca, nombra sólo el modelo: el nombre del modelo ya identifica
 * el equipo y la marca alargaba el mensaje sin añadir nada.
 */
export function buildWhatsAppQuoteMessage(options?: WhatsAppQuoteOptions): string {
  const { productName, brandName } = options ?? {};

  if (productName) return `Hola OrigenLab, quiero cotizar el ${productName}.`;
  if (brandName) return `Hola OrigenLab, quiero cotizar un equipo ${brandName}.`;
  return 'Hola OrigenLab, quiero cotizar un equipo.';
}

export function buildWhatsAppQuoteUrl(options?: WhatsAppQuoteOptions): string {
  return `https://wa.me/${contact.whatsappE164}?text=${encodeURIComponent(buildWhatsAppQuoteMessage(options))}`;
}

export function buildQuoteMailtoUrl(options?: WhatsAppQuoteOptions): string {
  const { productName, brandName } = options ?? {};
  const subject = productName
    ? `Cotización OrigenLab - ${productName}`
    : brandName
      ? `Cotización OrigenLab - ${brandName}`
      : 'Cotización OrigenLab';
  return `mailto:${contact.email}?subject=${encodeURIComponent(subject)}`;
}

/**
 * Plantilla del correo de cotización. Los campos son los mismos que pide la
 * lista operativa de `docs/company-scope.md`; prellenarlos evita el ida y vuelta
 * más común, que es pedir los datos básicos después del primer mensaje.
 */
const QUOTE_MAIL_TEMPLATE = [
  'Institución o empresa:',
  'Nombre y cargo:',
  'Ciudad y región:',
  'Aplicación o necesidad:',
  'Equipo o modelo de referencia (si lo tiene):',
  'Cantidad:',
  'Plazo estimado:',
  '',
].join('\n');

export function buildQuoteMailtoWithTemplate(options?: WhatsAppQuoteOptions): string {
  const { productName, brandName } = options ?? {};
  const subject = productName
    ? `Cotización OrigenLab - ${productName}`
    : brandName
      ? `Cotización OrigenLab - ${brandName}`
      : 'Cotización OrigenLab';
  const body = productName
    ? QUOTE_MAIL_TEMPLATE.replace(
        'Equipo o modelo de referencia (si lo tiene):',
        `Equipo o modelo de referencia (si lo tiene): ${productName}`,
      )
    : QUOTE_MAIL_TEMPLATE;
  return `mailto:${contact.email}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}
