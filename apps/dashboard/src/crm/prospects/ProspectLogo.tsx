import { useState } from "react";
import { initials } from "../ui";
import { prospectLogoUrl } from "./prospectApi";

/**
 * The organization's logo, from the bytes the worker stored (never the organization's own site);
 * initials on a brand tint when there is none or it fails to load. Decorative: the name is beside it.
 */
export function ProspectLogo({ id, name, hasLogo, size = "md" }: { id: string; name: string; hasLogo: boolean; size?: "md" | "lg" }) {
  const [failed, setFailed] = useState(false);
  const box = size === "lg" ? "h-14 w-14 text-base" : "h-11 w-11 text-[13px]";
  if (hasLogo && !failed) {
    return (
      <img
        data-testid="prospect-logo"
        src={prospectLogoUrl(id)}
        alt=""
        loading="lazy"
        onError={() => setFailed(true)}
        className={`${box} shrink-0 rounded-lg border border-line bg-white object-contain p-1`}
      />
    );
  }
  return (
    <span
      data-testid="prospect-initials"
      aria-hidden="true"
      className={`${box} flex shrink-0 items-center justify-center rounded-lg border border-brand-600/20 bg-brand-50 font-semibold text-brand-700`}
    >
      {initials(name)}
    </span>
  );
}
