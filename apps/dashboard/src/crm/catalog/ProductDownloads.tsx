/**
 * «Descargas»: the product's own data sheet when Drive has one, otherwise the price lists of the
 * suppliers that list it. Both open in Google Drive in a new tab; nothing passes through our
 * servers. Price lists print dealer costs, so the API leaves them out for a viewer, and a viewer
 * with no data sheet sees no section at all.
 */
import type { ProductDocuments } from "./catalogApi";
import { fmtDay } from "./catalogApi";
import { Section, buttonClass } from "../ui";

export function ProductDownloads({ documents }: { documents: ProductDocuments }) {
  const sheets = documents.datasheets;
  const lists = documents.price_lists ?? [];
  if (sheets.length === 0 && lists.length === 0) return null;
  return (
    <Section title="Descargas">
      <div className="flex flex-wrap gap-2">
        {sheets.map((d) => (
          <a key={d.id} href={d.url} target="_blank" rel="noopener noreferrer" className={buttonClass("primary")}
             title={d.title}>
            Ficha técnica
          </a>
        ))}
        {lists.map((d) => (
          <a key={d.id} href={d.url} target="_blank" rel="noopener noreferrer" className={buttonClass("secondary")}
             title={d.title}>
            Lista de precios · {d.supplier.display_name}
          </a>
        ))}
      </div>
      <p className="mt-1.5 text-[11px] text-ink-faint">
        {sheets.length === 0 ? "Este producto no tiene ficha propia en Drive. " : ""}
        {lists.length > 0
          ? `Las listas de precios muestran costos de distribuidor${lists[0].as_of ? ` (vigente desde ${fmtDay(lists[0].as_of)})` : ""}.`
          : ""}
      </p>
    </Section>
  );
}
