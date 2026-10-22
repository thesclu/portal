/** The portal's chevron (frontend/src/components/icons.tsx), drawn the same way: 24-unit grid, round
 *  caps, ink-3. */
export function ChevronRight({ size = 12 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="var(--color-ink-3)"
      strokeWidth={2.2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className="shrink-0"
    >
      <path d="M9 6l6 6-6 6" />
    </svg>
  )
}
