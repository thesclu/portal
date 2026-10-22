/**
 * The assistant's rail (P19d, design 1e and 2i), the pure part: how a card is titled and which
 * highlight it puts on the page, and how a run's "what was sent" summary reads. No React, no
 * network. The shapes are the generated ones.
 */

import type { Schemas } from '../api/client'

export type AssistantState = 'on' | 'off' | 'privileged'
export type AiCheck = Schemas['AiCheckOut']
export type AiSuggestion = Schemas['AiSuggestionOut']
export type AiNote = Schemas['DraftNoteOut']
export type AiRail = Schemas['AiRailOut']
export type AiAnswer = Schemas['AiAnswerOut']
export type AiPick = Schemas['AiPickOut']
export type AiRun = Schemas['AiRunOut']
export type PickMatch = Schemas['AiPickMatchOut']

// ---- cards -------------------------------------------------------------------------------------

/** The design's two highlights on the page: yellow for a suggestion, pink for a likely fight. */
export type Highlight = 'suggest' | 'fight'

export function highlightOf(kind: string): Highlight {
  return kind === 'likely_fight' ? 'fight' : 'suggest'
}

const KIND_LABEL: Record<string, string> = {
  scope: 'Scope',
  likely_fight: 'Likely fight',
  house_style: 'House style',
  citation: 'Citation',
  wording: 'Wording',
  other: 'Note',
}

/** "Scope", "Likely fight", "House style": a card's kind as the design labels it. */
export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind.replaceAll('_', ' ').replace(/^./, (c) => c.toUpperCase())
}

/** What a piece is called in a card: "Item 3" for an item, "¶2" for the second paragraph. */
export function pieceLabel(
  pieces: ReadonlyArray<{ id: string | null; kind: string; item_number: string }>,
  pieceId: string | null | undefined,
): string | null {
  if (!pieceId) return null
  let paragraph = 0
  for (const piece of pieces) {
    if (piece.kind !== 'item') paragraph += 1
    if (piece.id === pieceId)
      return piece.kind === 'item' ? `Item ${piece.item_number}` : `¶${paragraph}`
  }
  return null
}

/** "Item 3 · Scope", or just "Scope" when the card is about the letter as a whole. */
export function cardHeading(
  pieces: ReadonlyArray<{ id: string | null; kind: string; item_number: string }>,
  card: Pick<AiSuggestion, 'kind' | 'item_id'>,
): string {
  return [pieceLabel(pieces, card.item_id), kindLabel(card.kind)].filter(Boolean).join(' · ')
}

/** Which pieces the open cards put a highlight on; a likely fight wins over a suggestion on the
 *  same piece, since the pink is the warning. */
export function highlightsOf(
  cards: ReadonlyArray<Pick<AiSuggestion, 'kind' | 'item_id' | 'status'>>,
): Map<string, Highlight> {
  const out = new Map<string, Highlight>()
  for (const card of cards) {
    if (card.status !== 'open' || !card.item_id) continue
    const mark = highlightOf(card.kind)
    if (mark === 'fight' || !out.has(card.item_id)) out.set(card.item_id, mark)
  }
  return out
}

/** The rail's words when the assistant cannot be asked. Nothing legal lives here. */
export function assistantUnavailable(state: string): string | null {
  if (state === 'off')
    return 'The assistant is off. An administrator can turn it on under Settings → Organisation → Assistant.'
  if (state === 'privileged')
    return 'This letter is privileged, so nothing about it is sent to the assistant. Notes and checks still work.'
  return null
}

/** The sentence for an answer that was not an answer: the assistant off, a refusal, a failure. */
export function answerProblem(answer: Pick<AiAnswer, 'status' | 'message'>): string | null {
  if (answer.status === 'ok') return null
  if (answer.message) return answer.message
  if (answer.status === 'off') return 'The assistant is off.'
  if (answer.status === 'refused') return 'The assistant declined this one.'
  return 'The assistant could not answer. Try again.'
}

// ---- stage 1 -----------------------------------------------------------------------------------

/** The body `POST /api/drafting/drafts` wants for "Use this" on a match, or null for a kind the
 *  picker does not know. */
export function draftBodyFor(match: Pick<PickMatch, 'kind' | 'id'>): Record<string, string> | null {
  if (match.kind === 'master_letter') return { from_template_id: match.id }
  if (match.kind === 'past_request') return { from_request_id: match.id }
  return null
}

export function matchKindLabel(kind: string): string {
  if (kind === 'master_letter') return 'Master letter'
  if (kind === 'past_request') return 'Past request'
  return kind.replaceAll('_', ' ')
}

// ---- the run log --------------------------------------------------------------------------------

export interface SummaryChip {
  part: string
  chars: number
}

/** `{draft: {chars: 1234, sha256: …}, agency: {chars: 80}}` → the chips "draft: 1,234 chars",
 *  in the order the backend wrote them. A part with no readable size is skipped. */
export function summaryChips(summary: Record<string, unknown> | null | undefined): SummaryChip[] {
  if (!summary || typeof summary !== 'object') return []
  const out: SummaryChip[] = []
  for (const [part, value] of Object.entries(summary)) {
    if (value && typeof value === 'object' && 'chars' in value) {
      const chars = Number((value as { chars: unknown }).chars)
      if (Number.isFinite(chars)) out.push({ part, chars })
    } else if (typeof value === 'number' && Number.isFinite(value)) {
      out.push({ part, chars: value })
    }
  }
  return out
}

export function chipText(chip: SummaryChip): string {
  return `${chip.part}: ${chip.chars.toLocaleString('en-US')} chars`
}

/** "1,200 in · 340 out", or "—" when the run recorded no counts. */
export function tokensLabel(tokensIn: number | null, tokensOut: number | null): string {
  const parts = []
  if (tokensIn != null) parts.push(`${tokensIn.toLocaleString('en-US')} in`)
  if (tokensOut != null) parts.push(`${tokensOut.toLocaleString('en-US')} out`)
  return parts.length ? parts.join(' · ') : '—'
}

/** "gemini · gemini-2.5-flash-lite", or just the provider when no model was recorded. */
export function providerLabel(provider: string, model: string): string {
  return model ? `${provider} · ${model}` : provider
}
