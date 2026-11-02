/** The end of a list drawn in slices: scrolling it into view draws more; the button does the same by hand. */
export function GrowingListEnd({ hidden, onMore, endRef }: { hidden: number; onMore: () => void; endRef: (el: Element | null) => void }) {
  if (hidden <= 0) return null;
  return (
    <button
      ref={endRef}
      type="button"
      onClick={onMore}
      className="w-full rounded-md px-2 py-1.5 text-center text-[11px] text-ink-muted hover:bg-canvas-sunken hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
    >
      Mostrar más ({hidden})
    </button>
  );
}
