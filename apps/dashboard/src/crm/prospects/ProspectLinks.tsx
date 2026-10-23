import { ExternalLink } from "../ui";
import type { ProspectLinks as Links } from "./prospectApi";

const LABELS: [keyof Links, string][] = [
  ["website", "Sitio web"],
  ["linkedin_person", "LinkedIn persona"],
  ["linkedin_org", "LinkedIn institución"],
  ["orcid", "ORCID"],
  ["scholar", "Scholar"],
];

/** Only the links that were found, each opening in a new tab (LinkedIn only from ordinary search results). */
export function ProspectLinkRow({ links }: { links: Links }) {
  const found = LABELS.filter(([key]) => Boolean(links[key]));
  if (found.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-x-3 gap-y-1 text-xs" aria-label="Enlaces">
      {found.map(([key, label]) => (
        <li key={key}>
          <ExternalLink href={links[key] as string}>{label}</ExternalLink>
        </li>
      ))}
    </ul>
  );
}
