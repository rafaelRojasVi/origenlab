/**
 * «Guion de llamada»: what to say when calling about a sent quote, filled from the selected row
 * (contact, quote N°, product, sent date) and the person signed in. Text from the owner's sheet.
 */
import { personLabel, quoteProduct } from "../caseDisplay";
import { quoteNumberOf, type SegRow } from "../seguimiento";
import { shortDate } from "./PipelineBoard";

export function CallScript({ row, me, now, onClose }: { row: SegRow; me: string; now: Date; onClose: () => void }) {
  const card = row.card;
  const contact = personLabel(card.contact?.name);
  const quote = quoteNumberOf(card);
  const product = quoteProduct(card) ?? "los equipos";
  const sent = row.sentAt ? shortDate(row.sentAt, now) : null;
  const client = card.organization?.name ?? "Sin institución";
  const b = "font-semibold text-accent";
  return (
    <aside
      aria-label="Guion de llamada"
      className="flex flex-col gap-3 rounded-xl border border-line bg-canvas-raised p-4 xl:sticky xl:top-4 xl:w-[21rem]"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-[15px] font-bold text-ink">Guion de llamada</h2>
        <button
          type="button"
          aria-label="Cerrar guion"
          onClick={onClose}
          className="inline-grid h-9 w-9 place-items-center rounded-md border border-line-strong text-ink-muted hover:bg-canvas-sunken hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
        >
          <svg viewBox="0 0 16 16" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
            <path d="m4 4 8 8M12 4l-8 8" />
          </svg>
        </button>
      </div>
      <p className="text-[12px] text-ink-muted">
        Para <b className="text-ink">{client}</b>
        {quote ? ` · ${quote}` : null}
      </p>
      <p className="rounded-lg bg-canvas-sunken px-3.5 py-3 text-[13px] leading-relaxed text-ink" data-testid="guion-say">
        {contact ? (
          <>
            Hola, ¿hablo con <b className={b}>{contact}</b>?
          </>
        ) : (
          "Hola, ¿con quién hablo?"
        )}{" "}
        Le habla <b className={b}>{me}</b>, de ventas de OrigenLab. Le llamo por la cotización{" "}
        {quote ? (
          <>
            <b className={b}>{quote}</b>{" "}
          </>
        ) : null}
        de{" "}
        <b className={b}>{product}</b>
        {sent ? (
          <>
            {" "}que le enviamos el <b className={b}>{sent}</b>
          </>
        ) : (
          " que le enviamos"
        )}
        . ¿Alcanzó a revisarla? ¿Cómo va eso?
      </p>
      <h3 className="text-[12px] font-bold text-ink-muted">Si dice…</h3>
      <ul className="flex list-disc flex-col gap-1.5 pl-[18px] text-[12.5px] text-ink-muted">
        <li><b className="text-ink">«Todavía no»</b>: ¿cuándo le acomoda que lo llame? Anota la fecha en Próximo.</li>
        <li><b className="text-ink">«Está caro»</b>: pregunta contra qué compara y ofrece revisar opciones con Tatiana.</li>
        <li><b className="text-ink">Pregunta técnica</b>: tómala y di que Tatiana le responde hoy por correo.</li>
        <li><b className="text-ink">«Venció»</b>: le mandamos una actualizada hoy mismo.</li>
      </ul>
      <h3 className="text-[12px] font-bold text-ink-muted">Si no contesta</h3>
      <p className="rounded-lg bg-canvas-sunken px-3.5 py-3 text-[12.5px] leading-relaxed text-ink">
        Hola{contact ? ` ${contact}` : ""}, le habla {me} de OrigenLab por la cotización{quote ? ` ${quote}` : ""}. Lo vuelvo a llamar más tarde; si
        prefiere, respóndame este correo.
      </p>
    </aside>
  );
}
