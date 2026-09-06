import { contact } from '../data/contact';

export interface WhatsAppQuoteOptions {
  productName?: string;
  brandName?: string;
}

const GENERIC_QUOTE_MESSAGE =
  'Hola OrigenLab, quiero solicitar una cotización para equipamiento de laboratorio. ¿Me pueden orientar con disponibilidad, configuración y condiciones comerciales?';

/** Mensaje prefilled para wa.me; codificación segura vía encodeURIComponent. */
export function buildWhatsAppQuoteMessage(options?: WhatsAppQuoteOptions): string {
  const { productName, brandName } = options ?? {};

  if (productName && brandName) {
    return `Hola OrigenLab, quiero cotizar ${productName} de ${brandName}. ¿Me pueden enviar información técnica, configuración disponible y condiciones comerciales?`;
  }
  if (brandName) {
    return `Hola OrigenLab, quiero cotizar productos de ${brandName}. ¿Me pueden enviar información técnica, configuración disponible y condiciones comerciales?`;
  }
  return GENERIC_QUOTE_MESSAGE;
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
