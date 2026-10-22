/**
 * The new-user setup's pure parts (P20i; v6 006–012): which step comes next, what the welcome
 * says, and the agreement box's scroll (Agree waits for its end; the thumb the design draws).
 */

/** A step of the run, as the server names it; `done` always follows the last. */
export type SetupStep = 'welcome' | 'name' | 'signature' | 'confidentiality' | 'intern' | 'done'

const KNOWN: SetupStep[] = ['welcome', 'name', 'signature', 'confidentiality', 'intern', 'done']

export function isSetupStep(value: string | undefined): value is SetupStep {
  return Boolean(value) && (KNOWN as string[]).includes(value as string)
}

/** The run's steps as the server gave them, keeping only the ones the screens know. */
export function runSteps(steps: readonly string[]): SetupStep[] {
  return steps.filter((s): s is SetupStep => isSetupStep(s) && s !== 'done')
}

/** The step after `step`, or `done` after the last. */
export function nextStep(steps: readonly SetupStep[], step: SetupStep): SetupStep {
  const at = steps.indexOf(step)
  return at >= 0 && at < steps.length - 1 ? steps[at + 1]! : 'done'
}

/** The step before `step`, or null on the first (it has Not you? in place of Back). */
export function previousStep(steps: readonly SetupStep[], step: SetupStep): SetupStep | null {
  const at = steps.indexOf(step)
  return at > 0 ? steps[at - 1]! : null
}

/** "Welcome, Maya": the first word of the name. */
export function firstName(name: string): string {
  return name.trim().split(/\s+/)[0] ?? ''
}

/**
 * "a" or "an" before the organisation's short name, as it is said: an initialism by the sound
 * of its first letter ("an SCLU", "a UC"), a word by its first letter ("an Org", "a Union").
 */
export function articleFor(short: string): 'a' | 'an' {
  const word = short.trim()
  if (!word) return 'a'
  const initialism = word.length > 1 && word === word.toUpperCase() && /^[A-Z]+$/.test(word)
  if (initialism) return 'AEFHILMNORSX'.includes(word[0]!) ? 'an' : 'a'
  // "a Union", "a European …", "a One …": a vowel letter that is not a vowel sound.
  if (/^(uni|use|usu|eu|one)/i.test(word)) return 'a'
  return /^[aeiou]/i.test(word) ? 'an' : 'a'
}

/** Whether a scrolled box has been read to its end (a box that does not scroll has). */
export function readToEnd(scrollTop: number, clientHeight: number, scrollHeight: number): boolean {
  return scrollTop + clientHeight >= scrollHeight - 2
}

/**
 * The scroll thumb 010 draws in place of the browser's: 4 px wide, 7 px in from the right, on a
 * track 12 px in from the box's top and bottom, as long as the share of the text in view.
 */
export function scrollThumb(
  scrollTop: number,
  clientHeight: number,
  scrollHeight: number,
  inset = 12,
  shortest = 24,
): { top: number; height: number } | null {
  if (scrollHeight <= clientHeight + 1) return null
  const track = clientHeight - inset * 2
  const height = Math.max(shortest, Math.round((track * clientHeight) / scrollHeight))
  const travel = scrollHeight - clientHeight
  const top = inset + Math.round(((track - height) * Math.min(scrollTop, travel)) / travel)
  return { top, height }
}

/** The code the API answers with (403) when the new-user setup holds the person (P23a). */
export const SETUP_REQUIRED_CODE = 'setup_required'

/**
 * Whether an API answer is the server holding this person at the setup (P23a: every route but
 * the setup's own is refused until it is finished and every agreement in force is accepted).
 * The client then goes to /setup, unless it is already there.
 */
export function isSetupRefusal(status: number, body: unknown): boolean {
  return (
    status === 403 &&
    typeof body === 'object' &&
    body !== null &&
    (body as { code?: unknown }).code === SETUP_REQUIRED_CODE
  )
}

/** Whether a page address is one of the setup's own (`/setup`, `/setup/<step>`). */
export function inSetup(pathname: string): boolean {
  return pathname === '/setup' || pathname.startsWith('/setup/')
}
