import type { DashboardNavIconName } from "../../lib/dashboardNav";

const ICON_PATHS: Record<DashboardNavIconName, string> = {
  home: "M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1v-9.5Z",
  inbox:
    "M4 6h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1Zm0 2 8 5 8-5",
  pipeline:
    "M4 4h4v16H4V4Zm6 3h4v13h-4V7Zm6 5h4v8h-4v-8Z",
  deals:
    "M4 7h16v12H4V7Zm2 2v2h4V9H6Zm6 0v2h6V9h-6ZM6 13v2h4v-2H6Zm6 0v2h6v-2h-6Z",
  prospectos:
    "M12 4a4 4 0 1 1 0 8 4 4 0 0 1 0-8Zm-7 14a7 7 0 0 1 14 0H5Z",
  contacts:
    "M8 8a4 4 0 1 1 8 0 4 4 0 0 1-8 0Zm-4 12a8 8 0 0 1 16 0H4Z",
  tenders:
    "M6 4h12v3H6V4Zm-2 5h16v2H4V9Zm3 4h10v7H7v-7Z",
  payments:
    "M4 7h16v10H4V7Zm2 2v2h3V9H6Zm0 4v2h12v-2H6Z",
  suppliers:
    "M4 20V8l8-4 8 4v12H4Zm4-2h8v-4H8v4Zm-4-6h3v3H4v-3Z",
  catalog:
    "M4 6h7v7H4V6Zm9 0h7v7h-7V6ZM4 15h7v7H4v-7Zm9 0h7v7h-7v-7Z",
  system:
    "M12 8a4 4 0 1 1 0 8 4 4 0 0 1 0-8Zm8.5 4a8.4 8.4 0 0 1-.2 1.8l2 1.5-2 3.5-2.3-1a8.6 8.6 0 0 1-1.6.9l-.4 2.5H9l-.4-2.5a8.6 8.6 0 0 1-1.6-.9l-2.3 1-2-3.5 2-1.5a8.4 8.4 0 0 1-.2-1.8c0-.6.1-1.2.2-1.8L1.5 10.7l2-3.5 2.3 1c.5-.4 1-.7 1.6-.9l.4-2.5h6l.4 2.5c.6.2 1.1.5 1.6.9l2.3-1 2 3.5-2 1.5c.1.6.2 1.2.2 1.8Z",
};

export function NavIcon({
  name,
  className = "h-5 w-5",
}: {
  name: DashboardNavIconName;
  className?: string;
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      <path d={ICON_PATHS[name]} />
    </svg>
  );
}
