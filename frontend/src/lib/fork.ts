/**
 * The drafting tool's frames (P21c): the two message contracts the fork documents
 * (`apps/mike/SCLU.md` 10.3, 11.3, 12.5, 13.6 c), held to exactly as written. Pure, so tested.
 *
 *  - `sclu-letter/1`: stage 2 frames `/embed/letters/<master id>`; the portal sends `wave`,
 *    `agency`, `active`, `tab` and `preview`; the frame sends `ready`, `active`, `document`,
 *    `problem` and `preview`.
 *  - `sclu-templates/1`: Templates and Settings › Letter templates frame `/embed/templates…`; the
 *    portal sends `viewport`; the frame sends `ready`, `navigate`, `modal` (with `room`, by which
 *    the parent grows the frame's box while a dialog is open), `template` and `problem`.
 *
 * Both ways the origin is checked: a message is taken only from the frame's own window and the
 * drafting tool's origin, and posted only to that origin (`acceptMessage`, `post`).
 */

export const LETTER_PROTOCOL = 'sclu-letter/1'
export const TEMPLATES_PROTOCOL = 'sclu-templates/1'

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

const isId = (value: unknown): value is string => typeof value === 'string' && UUID.test(value)
const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
const text = (value: unknown, max = 500): string | null =>
  typeof value === 'string' ? value.slice(0, max) : null
const num = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

// ---- sclu-letter/1 ---------------------------------------------------------------------------

export type LetterTab = 'chat' | 'suggestions' | 'notes' | 'agency'

export interface AgencyDetails {
  name: string
  jurisdiction?: string
  files_by: string
  filing_address?: string
  letter_address?: string[]
  abbreviation?: string
  contacts?: Array<{ name: string; role?: string; email?: string; phone?: string }>
}

export interface WaveDocument {
  document_id: string
  label: string
  key?: string
}

export type ToLetterFrame =
  | {
      protocol: typeof LETTER_PROTOCOL
      type: 'wave'
      master: WaveDocument
      agencies: WaveDocument[]
      active?: string
    }
  | { protocol: typeof LETTER_PROTOCOL; type: 'active'; document_id: string }
  | {
      protocol: typeof LETTER_PROTOCOL
      type: 'agency'
      document_id: string
      details: AgencyDetails | null
    }
  | { protocol: typeof LETTER_PROTOCOL; type: 'tab'; tab: LetterTab }
  | {
      protocol: typeof LETTER_PROTOCOL
      type: 'preview'
      agency: {
        key?: string
        label: string
        values: Record<string, string | null>
        details?: AgencyDetails | null
      }
    }

export type SaveState = 'saved' | 'saving' | 'unsaved' | 'error'

export type FromLetterFrame =
  | { type: 'ready'; master_id: string }
  | { type: 'active'; document_id: string }
  | {
      type: 'document'
      document_id: string
      filename: string
      version_number: number | null
      save: SaveState
      saved_at: string | null
    }
  | { type: 'problem'; code: 'not_found' | 'failed' | 'signed_out'; document_id: string | null }
  | { type: 'preview'; key: string; status: 'shown' | 'failed'; unfilled: string[] }

const SAVES: SaveState[] = ['saved', 'saving', 'unsaved', 'error']
const LETTER_PROBLEMS = ['not_found', 'failed', 'signed_out'] as const

/** One message from the letter frame, or null for anything that is not exactly one of its
 *  shapes (unknown fields are dropped, text is capped). */
export function parseLetterMessage(data: unknown): FromLetterFrame | null {
  if (!isRecord(data) || data.protocol !== LETTER_PROTOCOL) return null
  switch (data.type) {
    case 'ready':
      return isId(data.master_id) ? { type: 'ready', master_id: data.master_id } : null
    case 'active':
      return isId(data.document_id) ? { type: 'active', document_id: data.document_id } : null
    case 'document': {
      const save = SAVES.find((s) => s === data.save)
      if (!isId(data.document_id) || !save) return null
      return {
        type: 'document',
        document_id: data.document_id,
        filename: text(data.filename, 300) ?? '',
        version_number: num(data.version_number),
        save,
        saved_at: text(data.saved_at, 64),
      }
    }
    case 'problem': {
      const code = LETTER_PROBLEMS.find((c) => c === data.code)
      if (!code) return null
      return {
        type: 'problem',
        code,
        document_id: isId(data.document_id) ? data.document_id : null,
      }
    }
    case 'preview': {
      const status = data.status === 'shown' || data.status === 'failed' ? data.status : null
      if (!status || typeof data.key !== 'string') return null
      const unfilled = Array.isArray(data.unfilled)
        ? data.unfilled.filter((n): n is string => typeof n === 'string').slice(0, 100)
        : []
      return { type: 'preview', key: data.key.slice(0, 200), status, unfilled }
    }
    default:
      return null
  }
}

// ---- sclu-templates/1 ------------------------------------------------------------------------

export type TemplatesPage = 'templates' | 'template' | 'blocks' | 'letterheads' | 'letterhead'
export type TemplateTab = 'letter' | 'citations' | 'revisions'

export interface Room {
  left: number
  top: number
  right: number
  bottom: number
}

export type FromTemplatesFrame =
  | { type: 'ready'; page: TemplatesPage; id: string | null }
  | { type: 'navigate'; page: TemplatesPage; id: string | null; tab?: TemplateTab }
  | { type: 'modal'; open: boolean; room?: Room }
  | {
      type: 'template'
      template_id: string
      name: string
      revision: number | null
      archived: boolean
      event: 'created' | 'saved' | 'archived' | 'unarchived'
    }
  | {
      type: 'problem'
      code: 'not_found' | 'failed' | 'no_organisation' | 'signed_out'
      id: string | null
    }

const PAGES: TemplatesPage[] = ['templates', 'template', 'blocks', 'letterheads', 'letterhead']
const TABS: TemplateTab[] = ['letter', 'citations', 'revisions']
const EVENTS = ['created', 'saved', 'archived', 'unarchived'] as const
const TEMPLATE_PROBLEMS = ['not_found', 'failed', 'no_organisation', 'signed_out'] as const

function room(value: unknown): Room | undefined {
  if (!isRecord(value)) return undefined
  const sides = [value.left, value.top, value.right, value.bottom].map(num)
  if (sides.some((s) => s === null || s < 0 || s > 4000)) return undefined
  const [left, top, right, bottom] = sides as number[]
  return { left, top, right, bottom }
}

export function parseTemplatesMessage(data: unknown): FromTemplatesFrame | null {
  if (!isRecord(data) || data.protocol !== TEMPLATES_PROTOCOL) return null
  const page = PAGES.find((p) => p === data.page)
  const id = isId(data.id) ? data.id : null
  switch (data.type) {
    case 'ready':
      return page ? { type: 'ready', page, id } : null
    case 'navigate': {
      if (!page) return null
      const tab = TABS.find((t) => t === data.tab)
      return tab ? { type: 'navigate', page, id, tab } : { type: 'navigate', page, id }
    }
    case 'modal': {
      if (typeof data.open !== 'boolean') return null
      const grown = data.open ? room(data.room) : undefined
      return grown
        ? { type: 'modal', open: data.open, room: grown }
        : { type: 'modal', open: data.open }
    }
    case 'template': {
      const event = EVENTS.find((e) => e === data.event)
      const name = text(data.name, 200)
      if (!isId(data.template_id) || !event || !name) return null
      return {
        type: 'template',
        template_id: data.template_id,
        name,
        revision: num(data.revision),
        archived: data.archived === true,
        event,
      }
    }
    case 'problem': {
      const code = TEMPLATE_PROBLEMS.find((c) => c === data.code)
      return code ? { type: 'problem', code, id } : null
    }
    default:
      return null
  }
}

// ---- both ways, checked --------------------------------------------------------------------

/** The data of a message only when it came from this frame's window and the drafting tool's
 *  origin; otherwise null (and the caller ignores it). */
export function acceptMessage(
  event: Pick<MessageEvent, 'origin' | 'source' | 'data'>,
  origin: string,
  frame: Window | null | undefined,
): unknown {
  if (!origin || event.origin !== origin) return null
  if (!frame || event.source !== frame) return null
  return event.data
}

/** Post to the frame, to the drafting tool's origin only (never "*"). */
export function post(frame: Window | null | undefined, origin: string, message: object): void {
  if (!frame || !origin) return
  frame.postMessage(message, origin)
}

// ---- addresses -----------------------------------------------------------------------------

const trim = (url: string) => url.replace(/\/+$/, '')

/** `/embed/letters/<master>` with the agencies' documents and the one shown. */
export function letterFrameUrl(web: string, masterId: string, agencies: string[] = []): string {
  const query = agencies.length > 0 ? `?agencies=${agencies.join(',')}` : ''
  return `${trim(web)}/embed/letters/${masterId}${query}`
}

/** The fork's Templates route for a portal address under /templates (null: not a fork page,
 *  such as Saved replies). */
export function templatesFramePath(portalPath: string, search = ''): string | null {
  const rest = portalPath.replace(/^\/templates\/?/, '').replace(/\/+$/, '')
  const params = new URLSearchParams(search)
  const tab = TABS.find((t) => t === params.get('tab'))
  const block = params.get('block')
  if (rest === '') return '/embed/templates'
  if (rest === 'blocks')
    return `/embed/templates/blocks${block && isId(block) ? `?block=${block}` : ''}`
  if (/^blocks\//.test(rest)) {
    const id = rest.slice('blocks/'.length)
    return isId(id) ? `/embed/templates/blocks?block=${id}` : '/embed/templates/blocks'
  }
  if (rest === 'letterheads') return '/embed/templates/letterheads'
  if (rest === 'letterheads/new') return '/embed/templates/letterheads/new'
  if (/^letterheads\//.test(rest)) {
    const id = rest.slice('letterheads/'.length)
    return isId(id) ? `/embed/templates/letterheads/${id}` : '/embed/templates/letterheads'
  }
  if (isId(rest)) return `/embed/templates/${rest}${tab && tab !== 'letter' ? `?tab=${tab}` : ''}`
  return null
}

/** The portal's address for where the Templates frame went (`navigate`). */
export function templatesPortalPath(page: TemplatesPage, id: string | null, tab?: TemplateTab) {
  switch (page) {
    case 'templates':
      return '/templates'
    case 'template':
      return id ? `/templates/${id}${tab && tab !== 'letter' ? `?tab=${tab}` : ''}` : '/templates'
    case 'blocks':
      return id ? `/templates/blocks?block=${id}` : '/templates/blocks'
    case 'letterheads':
      return '/templates/letterheads'
    case 'letterhead':
      return id ? `/templates/letterheads/${id}` : '/templates/letterheads/new'
  }
}

/** Where to send a person whose framed page is signed out (13.6 c, as P25b changes it): the
 *  drafting tool's start of a sign-in through Keycloak, which sends them on to Keycloak at once.
 *  Keycloak knows them already (this portal's session is its session), so they go through without
 *  a screen, or are asked only for the passkey when the tools' re-check is due, and come back
 *  here. */
export function signInAgainUrl(web: string, returnTo: string): string {
  return `${trim(web)}/auth/keycloak?return_to=${encodeURIComponent(returnTo)}`
}

/** The frame's box grown by `room` while a dialog is open (12.5), as CSS offsets. */
export function grownBox(base: { width: number; height: number }, grow: Room | null) {
  const r = grow ?? { left: 0, top: 0, right: 0, bottom: 0 }
  return {
    left: 0 - r.left || 0,
    top: 0 - r.top || 0,
    width: base.width + r.left + r.right,
    height: base.height + r.top + r.bottom,
  }
}

/** The parent's visible area relative to the frame's top-left (`viewport`, 11.3). */
export function viewportOf(
  rect: { left: number; top: number },
  window: { innerWidth: number; innerHeight: number },
) {
  return {
    protocol: TEMPLATES_PROTOCOL,
    type: 'viewport' as const,
    left: 0 - rect.left || 0,
    top: 0 - rect.top || 0,
    width: window.innerWidth,
    height: window.innerHeight,
  }
}
