import { describe, expect, it, vi } from 'vitest'

import {
  LETTER_PROTOCOL,
  TEMPLATES_PROTOCOL,
  acceptMessage,
  grownBox,
  letterFrameUrl,
  parseLetterMessage,
  parseTemplatesMessage,
  post,
  signInAgainUrl,
  templatesFramePath,
  templatesPortalPath,
  viewportOf,
} from './fork'

const ID = '0f9e8d7c-1111-4222-8333-444455556666'
const ORIGIN = 'https://portal.example.test'

describe('origin checks', () => {
  const frame = { postMessage: vi.fn() } as unknown as Window
  const other = {} as Window
  it('takes a message only from the frame and the drafting tool', () => {
    const data = { protocol: LETTER_PROTOCOL, type: 'ready', master_id: ID }
    expect(acceptMessage({ origin: ORIGIN, source: frame, data }, ORIGIN, frame)).toBe(data)
    expect(acceptMessage({ origin: 'https://evil.test', source: frame, data }, ORIGIN, frame)).toBe(
      null,
    )
    expect(acceptMessage({ origin: ORIGIN, source: other, data }, ORIGIN, frame)).toBe(null)
    expect(acceptMessage({ origin: ORIGIN, source: frame, data }, '', frame)).toBe(null)
    expect(acceptMessage({ origin: ORIGIN, source: null, data }, ORIGIN, null)).toBe(null)
  })
  it('posts only to the drafting tool, never to every origin', () => {
    post(frame, ORIGIN, { protocol: LETTER_PROTOCOL, type: 'tab', tab: 'chat' })
    expect(frame.postMessage).toHaveBeenCalledWith(
      { protocol: LETTER_PROTOCOL, type: 'tab', tab: 'chat' },
      ORIGIN,
    )
    post(frame, '', { x: 1 })
    expect(frame.postMessage).toHaveBeenCalledTimes(1)
  })
})

describe('sclu-letter/1', () => {
  it('reads each documented message and nothing else', () => {
    expect(parseLetterMessage({ protocol: LETTER_PROTOCOL, type: 'ready', master_id: ID })).toEqual(
      {
        type: 'ready',
        master_id: ID,
      },
    )
    expect(
      parseLetterMessage({
        protocol: LETTER_PROTOCOL,
        type: 'document',
        document_id: ID,
        filename: 'Wave 1 master.docx',
        version_number: 4,
        save: 'saved',
        saved_at: '2026-10-17T12:00:00Z',
        extra: 'dropped',
      }),
    ).toEqual({
      type: 'document',
      document_id: ID,
      filename: 'Wave 1 master.docx',
      version_number: 4,
      save: 'saved',
      saved_at: '2026-10-17T12:00:00Z',
    })
    expect(
      parseLetterMessage({
        protocol: LETTER_PROTOCOL,
        type: 'problem',
        code: 'signed_out',
        document_id: ID,
      }),
    ).toEqual({ type: 'problem', code: 'signed_out', document_id: ID })
    expect(
      parseLetterMessage({
        protocol: LETTER_PROTOCOL,
        type: 'preview',
        key: 'r1',
        status: 'shown',
        unfilled: ['RECIPIENT_OFFICE', 3],
      }),
    ).toEqual({ type: 'preview', key: 'r1', status: 'shown', unfilled: ['RECIPIENT_OFFICE'] })
  })
  it('refuses another protocol, an unknown type, a bad id or value', () => {
    expect(parseLetterMessage({ protocol: TEMPLATES_PROTOCOL, type: 'ready', master_id: ID })).toBe(
      null,
    )
    expect(parseLetterMessage({ protocol: LETTER_PROTOCOL, type: 'run', code: 'x' })).toBe(null)
    expect(parseLetterMessage({ protocol: LETTER_PROTOCOL, type: 'ready', master_id: 'x' })).toBe(
      null,
    )
    expect(
      parseLetterMessage({
        protocol: LETTER_PROTOCOL,
        type: 'document',
        document_id: ID,
        save: 'lost',
      }),
    ).toBe(null)
    expect(parseLetterMessage({ protocol: LETTER_PROTOCOL, type: 'problem', code: 'boom' })).toBe(
      null,
    )
    expect(parseLetterMessage('{"protocol":"sclu-letter/1"}')).toBe(null)
    expect(parseLetterMessage(null)).toBe(null)
  })
})

describe('sclu-templates/1', () => {
  it('reads navigate, modal with room, template and problem', () => {
    expect(
      parseTemplatesMessage({
        protocol: TEMPLATES_PROTOCOL,
        type: 'navigate',
        page: 'template',
        id: ID,
        tab: 'revisions',
      }),
    ).toEqual({ type: 'navigate', page: 'template', id: ID, tab: 'revisions' })
    expect(
      parseTemplatesMessage({
        protocol: TEMPLATES_PROTOCOL,
        type: 'modal',
        open: true,
        room: { left: 70, top: 39, right: 0, bottom: 0 },
      }),
    ).toEqual({ type: 'modal', open: true, room: { left: 70, top: 39, right: 0, bottom: 0 } })
    expect(
      parseTemplatesMessage({
        protocol: TEMPLATES_PROTOCOL,
        type: 'modal',
        open: true,
        room: { left: -1 },
      }),
    ).toEqual({ type: 'modal', open: true })
    expect(
      parseTemplatesMessage({
        protocol: TEMPLATES_PROTOCOL,
        type: 'template',
        template_id: ID,
        name: 'Synthetic letter',
        revision: 2,
        archived: false,
        event: 'saved',
      }),
    ).toEqual({
      type: 'template',
      template_id: ID,
      name: 'Synthetic letter',
      revision: 2,
      archived: false,
      event: 'saved',
    })
    expect(
      parseTemplatesMessage({
        protocol: TEMPLATES_PROTOCOL,
        type: 'problem',
        code: 'signed_out',
        id: null,
      }),
    ).toEqual({ type: 'problem', code: 'signed_out', id: null })
    expect(
      parseTemplatesMessage({ protocol: TEMPLATES_PROTOCOL, type: 'navigate', page: 'x' }),
    ).toBe(null)
    expect(
      parseTemplatesMessage({
        protocol: TEMPLATES_PROTOCOL,
        type: 'template',
        template_id: ID,
        event: 'saved',
      }),
    ).toBe(null)
  })
})

describe('addresses', () => {
  it('builds the frames and maps the Templates pages both ways', () => {
    expect(letterFrameUrl('http://localhost:3000/', ID, [ID, ID])).toBe(
      `http://localhost:3000/embed/letters/${ID}?agencies=${ID},${ID}`,
    )
    expect(templatesFramePath('/templates')).toBe('/embed/templates')
    expect(templatesFramePath('/templates/blocks')).toBe('/embed/templates/blocks')
    expect(templatesFramePath(`/templates/blocks/${ID}`)).toBe(
      `/embed/templates/blocks?block=${ID}`,
    )
    expect(templatesFramePath('/templates/letterheads/new')).toBe(
      '/embed/templates/letterheads/new',
    )
    expect(templatesFramePath(`/templates/letterheads/${ID}`)).toBe(
      `/embed/templates/letterheads/${ID}`,
    )
    expect(templatesFramePath(`/templates/${ID}`, '?tab=citations')).toBe(
      `/embed/templates/${ID}?tab=citations`,
    )
    expect(templatesFramePath('/templates/replies')).toBe(null)
    expect(templatesPortalPath('template', ID, 'revisions')).toBe(`/templates/${ID}?tab=revisions`)
    expect(templatesPortalPath('letterhead', null)).toBe('/templates/letterheads/new')
    expect(templatesPortalPath('blocks', ID)).toBe(`/templates/blocks?block=${ID}`)
    expect(
      signInAgainUrl('https://portal.thesclu.org/draft', 'https://portal.thesclu.org/drafts/1'),
    ).toBe(
      'https://portal.thesclu.org/draft/auth/keycloak?return_to=https%3A%2F%2Fportal.thesclu.org%2Fdrafts%2F1',
    )
  })
  it('grows the box by the room asked for, and back', () => {
    expect(
      grownBox({ width: 792, height: 640 }, { left: 70, top: 39, right: 0, bottom: 0 }),
    ).toEqual({
      left: -70,
      top: -39,
      width: 862,
      height: 679,
    })
    expect(grownBox({ width: 792, height: 640 }, null)).toEqual({
      left: 0,
      top: 0,
      width: 792,
      height: 640,
    })
    expect(viewportOf({ left: 272, top: 56 }, { innerWidth: 1440, innerHeight: 860 })).toEqual({
      protocol: TEMPLATES_PROTOCOL,
      type: 'viewport',
      left: -272,
      top: -56,
      width: 1440,
      height: 860,
    })
  })
})
