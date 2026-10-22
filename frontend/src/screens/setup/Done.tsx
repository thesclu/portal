import { useEffect } from 'react'
import { useNavigate } from 'react-router'

import { SetupFrame } from './parts'

/** The design's cycle when the token cannot be read (tokens.css --setup-done-cycle). */
const CYCLE_MS = 3400
/** The card has faded by 78 % of the cycle (the keyframes' last step); Home opens then. */
const FADED_AT = 0.78
/** With reduced motion nothing moves, and Home still opens after 1.5 s. */
const STILL_MS = 1500

function cycleMs(): number {
  const raw = getComputedStyle(document.documentElement).getPropertyValue('--setup-done-cycle')
  const value = Number.parseFloat(raw)
  if (!Number.isFinite(value) || value <= 0) return CYCLE_MS
  return raw.trim().endsWith('ms') ? value : value * 1000
}

/**
 * Done (012): no step and no link. The check pops in and draws, the ring fills over 1.5 s, the
 * card fades, and Home opens. With reduced motion the check is shown finished, nothing moves,
 * and Home opens after 1.5 s.
 */
export function Done({ logoAlt }: { logoAlt: string }) {
  const navigate = useNavigate()
  useEffect(() => {
    const still = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    const wait = still ? STILL_MS : Math.round(cycleMs() * FADED_AT)
    const timer = window.setTimeout(() => void navigate('/', { replace: true }), wait)
    return () => window.clearTimeout(timer)
  }, [navigate])

  return (
    <SetupFrame label="" progress={null} logoAlt={logoAlt} cardClassName="animate-setup-card">
      <div role="status" className="flex flex-col items-center gap-4 py-[110px]">
        <span className="relative inline-flex size-16 items-center justify-center">
          <svg
            aria-hidden
            width="64"
            height="64"
            viewBox="0 0 64 64"
            className="absolute inset-0 -rotate-90"
          >
            <circle
              cx="32"
              cy="32"
              r="30"
              fill="none"
              strokeWidth="2"
              className="stroke-success-track"
            />
            <circle
              cx="32"
              cy="32"
              r="30"
              fill="none"
              strokeWidth="2"
              strokeLinecap="round"
              strokeDasharray="188.5"
              strokeDashoffset="0"
              className="animate-setup-ring stroke-success-ink"
            />
          </svg>
          <span className="inline-flex size-[52px] animate-setup-pop items-center justify-center rounded-pill bg-ok-tint">
            <svg
              aria-hidden
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              strokeWidth="2.4"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="stroke-success-ink"
            >
              <path
                d="M5 12l5 5 9-10"
                strokeDasharray="22"
                strokeDashoffset="0"
                className="animate-setup-check"
              />
            </svg>
          </span>
        </span>
        <h1 className="font-display text-24 font-extrabold tracking-title text-ink">You’re set</h1>
      </div>
    </SetupFrame>
  )
}
