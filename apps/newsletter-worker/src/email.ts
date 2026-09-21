/**
 * Envío del correo de confirmación.
 *
 * El proveedor no está decidido, así que aquí sólo vive la interfaz. Eso no es
 * un hueco: es la forma de que el resto del sistema se pueda escribir y probar
 * entero sin enviar nada y sin comprometer al negocio con un encargado del
 * tratamiento que nadie ha aprobado.
 *
 * `NullSender` es el remitente de producción mientras no haya otro, y **falla
 * de forma cerrada**. No devuelve éxito silencioso, no encola, no reintenta:
 * rechaza. Un remitente que finge haber enviado dejaría a una persona esperando
 * un correo que no existe y a la base con una solicitud que nadie puede
 * confirmar. Como consecuencia, con `NullSender` configurado el alta responde
 * 503 y la solicitud se descarta.
 */

export interface ConfirmationMessage {
  to: string;
  /** URL completa de confirmación, con el token opaco. */
  confirmUrl: string;
  consentTextVersion: string;
}

export interface EmailSender {
  readonly id: string;
  send(message: ConfirmationMessage): Promise<void>;
}

export class SenderUnavailable extends Error {
  constructor(senderId: string) {
    super(`remitente no disponible: ${senderId}`);
    this.name = 'SenderUnavailable';
  }
}

/** Remitente de producción mientras no haya proveedor. Rechaza siempre. */
export class NullSender implements EmailSender {
  readonly id = 'null';

  async send(): Promise<void> {
    throw new SenderUnavailable(this.id);
  }
}

/** Remitente de pruebas. Guarda lo enviado en memoria y no sale a la red. */
export class FakeSender implements EmailSender {
  readonly id = 'fake';
  readonly sent: ConfirmationMessage[] = [];
  shouldFail = false;

  async send(message: ConfirmationMessage): Promise<void> {
    if (this.shouldFail) throw new SenderUnavailable(this.id);
    this.sent.push(message);
  }
}

export function senderFor(id: string): EmailSender {
  switch (id) {
    case 'null':
      return new NullSender();
    default:
      /* Un identificador desconocido no se interpreta con optimismo. */
      return new NullSender();
  }
}
