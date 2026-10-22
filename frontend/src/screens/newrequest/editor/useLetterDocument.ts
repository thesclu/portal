/**
 * What a letter editor holds and how it saves: the pieces and citations as local rows, each
 * piece's latest document, and one PUT 800 ms after the last change. A draft (DraftEditor) and a
 * template (the template editor in Settings) differ only in where the PUT goes, so both run on
 * this one hook.
 */
import type { Editor, JSONContent } from '@tiptap/react'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { Schemas } from '../../../api/client'
import { messageOf } from '../../../api/query'
import {
  agoLabel,
  demotePiece,
  insertPiece,
  localCitations,
  localKey,
  localPieces,
  movePiece,
  nextMarker,
  promotePiece,
  removePiece,
  reorderPieces,
  savePayload,
  withServerIds,
  type LetterSave,
  type LocalCitation,
  type LocalPiece,
  type PieceKind,
} from './letter'

type Letter = Schemas['LetterOut']

export const SAVE_DELAY = 800

export interface SaveState {
  saving: boolean
  savedAt: number | null
  error: string | null
}

export interface LetterDocumentOptions {
  letter: Letter
  /** Nothing may change, and nothing is ever sent. */
  frozen?: boolean
  /** The PUT. Resolves to the API's answer, data or error, the way `api.PUT` does. */
  put: (body: LetterSave) => Promise<{ data?: Letter; error?: unknown }>
  /** After each save that went through. */
  onSaved?: (saved: Letter) => void
  /**
   * Keep each piece's document in the pieces themselves as it is typed. The draft editor shows
   * every piece at once and keeps the documents out of React state so typing re-renders only the
   * one editor; the template editor shows one piece's editor at a time and needs the rest to
   * remember what was typed when the editor moves on.
   */
  live?: boolean
}

export function useLetterDocument({
  letter,
  frozen = false,
  put,
  onSaved,
  live = false,
}: LetterDocumentOptions) {
  // ---- what the editor holds ------------------------------------------------------------------
  const [subject, setSubjectState] = useState(letter.subject)
  const [pieces, setPieces] = useState<LocalPiece[]>(() => localPieces(letter.pieces))
  const [citations, setCitations] = useState<LocalCitation[]>(() =>
    localCitations(letter.citations, pieces),
  )
  // Each piece's editor owns its document; the latest one is kept here, outside React state.
  const docs = useRef<Record<string, JSONContent>>({})
  const latest = useRef({ subject, pieces, citations })
  useEffect(() => {
    latest.current = { subject, pieces, citations }
  })
  const [active, setActive] = useState<{ key: string; editor: Editor } | null>(null)
  const putRef = useRef(put)
  const onSavedRef = useRef(onSaved)
  useEffect(() => {
    putRef.current = put
    onSavedRef.current = onSaved
  })

  // ---- autosave --------------------------------------------------------------------------------
  const [save, setSave] = useState<SaveState>({ saving: false, savedAt: null, error: null })
  const timer = useRef<number | undefined>(undefined)
  const inFlight = useRef<Promise<void> | null>(null)
  const dirty = useRef(false)

  // `schedule` is stable and reaches the latest `saveNow` through a ref, so a save that finds
  // more changes waiting can queue the next one without the two depending on each other.
  const saveRef = useRef<() => Promise<void>>(async () => {})
  const schedule = useCallback(() => {
    if (frozen) return
    dirty.current = true
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => void saveRef.current(), SAVE_DELAY)
  }, [frozen])

  const saveNow = useCallback(async () => {
    if (inFlight.current || frozen) return
    dirty.current = false
    setSave((s) => ({ ...s, saving: true }))
    const current = latest.current
    const sentPieces = current.pieces.map((p) => ({ ...p, doc: docs.current[p.key] ?? p.doc }))
    const pieceKeys = sentPieces.map((p) => p.key)
    const citationKeys = current.citations.map((c) => c.key)
    const body = savePayload(current.subject, sentPieces, current.citations)
    const work = (async () => {
      const result = await putRef.current(body)
      if (result.error || !result.data) {
        // Nothing local is lost: the next change tries again.
        dirty.current = true
        setSave((s) => ({ ...s, saving: false, error: messageOf(result.error) }))
        return
      }
      const saved = result.data
      setPieces((ps) => withServerIds(ps, pieceKeys, saved.pieces))
      setCitations((cs) => withServerIds(cs, citationKeys, saved.citations))
      setSave({ saving: false, savedAt: Date.now(), error: null })
      onSavedRef.current?.(saved)
      // A citation on an item that had no id yet was saved without it; now the item has one.
      const orphan = current.citations.some(
        (c) => c.item_key && !sentPieces.find((p) => p.key === c.item_key)?.id,
      )
      if (dirty.current || orphan) schedule()
    })()
    inFlight.current = work
    try {
      await work
    } finally {
      inFlight.current = null
    }
  }, [frozen, schedule])
  useEffect(() => {
    saveRef.current = saveNow
  }, [saveNow])

  /** Saves whatever is waiting, now; true once nothing is left unsaved, false when the save
   *  was refused (the error is in `save.error`). */
  const flush = useCallback(async (): Promise<boolean> => {
    window.clearTimeout(timer.current)
    if (inFlight.current) await inFlight.current
    if (dirty.current) await saveRef.current()
    return !dirty.current
  }, [])

  useEffect(
    () => () => {
      // Leaving with a change still waiting: save it on the way out.
      window.clearTimeout(timer.current)
      if (dirty.current) void saveRef.current()
    },
    [],
  )

  // "saved Ns ago" ticks.
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!save.savedAt) return
    const tick = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(tick)
  }, [save.savedAt])
  const saveLabel = save.saving
    ? 'saving…'
    : save.error
      ? 'not saved'
      : save.savedAt
        ? agoLabel((now - save.savedAt) / 1000)
        : null

  // ---- edits -----------------------------------------------------------------------------------
  const setSubject = useCallback(
    (value: string) => {
      setSubjectState(value)
      schedule()
    },
    [schedule],
  )
  const onDoc = useCallback(
    (key: string, doc: JSONContent) => {
      docs.current[key] = doc
      if (live) setPieces((ps) => ps.map((p) => (p.key === key ? { ...p, doc } : p)))
      schedule()
    },
    [schedule, live],
  )
  const onFocus = useCallback((key: string, editor: Editor) => setActive({ key, editor }), [])
  /** A whole document for a piece from outside its editor (the assistant's wording, once a
   *  person applies it): held as if typed, and saved the same way. The caller shows it in the
   *  piece's editor, which does not re-read `piece.doc` once mounted. */
  const replaceDoc = useCallback(
    (key: string, doc: JSONContent) => {
      docs.current[key] = doc
      setPieces((ps) => ps.map((p) => (p.key === key ? { ...p, doc } : p)))
      schedule()
    },
    [schedule],
  )
  const change = useCallback(
    (fn: (pieces: LocalPiece[]) => LocalPiece[]) => {
      setPieces(fn)
      schedule()
    },
    [schedule],
  )
  const onDemote = useCallback((key: string) => change((ps) => demotePiece(ps, key)), [change])
  const onPromote = useCallback((key: string) => change((ps) => promotePiece(ps, key)), [change])
  const onDelete = useCallback(
    (key: string) => {
      change((ps) => removePiece(ps, key))
      delete docs.current[key]
      setActive((a) => (a?.key === key ? null : a))
    },
    [change],
  )
  /** A new piece at `index`; its key, so a caller can select it. */
  const add = useCallback(
    (index: number, kind: PieceKind): string => {
      const next = insertPiece(latest.current.pieces, index, kind)
      const at = Math.max(0, Math.min(index, latest.current.pieces.length))
      change(() => next)
      return next[at]!.key
    },
    [change],
  )
  const move = useCallback(
    (from: number, to: number) => change((ps) => movePiece(ps, from, to)),
    [change],
  )
  const reorder = useCallback((keys: string[]) => change((ps) => reorderPieces(ps, keys)), [change])

  // ---- citations -------------------------------------------------------------------------------
  /** A new citation, on `itemKey`'s piece when given; with an editor, its reference goes in at
   *  the caret. Returns the marker it got. */
  const addCitation = useCallback(
    (
      text: string,
      itemKey: string | null,
      options: { marker?: string; editor?: Editor | null } = {},
    ): string => {
      const marker = options.marker?.trim() || nextMarker(latest.current.citations)
      setCitations((cs) => [
        ...cs,
        { key: localKey(), id: null, marker, citation_text: text, item_key: itemKey },
      ])
      options.editor?.chain().focus().insertFootnoteRef(marker).run()
      schedule()
      return marker
    },
    [schedule],
  )
  const editCitation = useCallback(
    (key: string, text: string) => {
      setCitations((cs) => cs.map((c) => (c.key === key ? { ...c, citation_text: text } : c)))
      schedule()
    },
    [schedule],
  )
  const removeCitation = useCallback(
    (key: string) => {
      setCitations((cs) => cs.filter((c) => c.key !== key))
      schedule()
    },
    [schedule],
  )

  return {
    subject,
    setSubject,
    pieces,
    citations,
    docs,
    active,
    setActive,
    save,
    saveLabel,
    /** Changes after every save; what the server renders is keyed on it. */
    version: save.savedAt ?? 0,
    schedule,
    flush,
    change,
    onDoc,
    onFocus,
    replaceDoc,
    onDemote,
    onPromote,
    onDelete,
    add,
    move,
    reorder,
    addCitation,
    editCitation,
    removeCitation,
  }
}

export type LetterDocument = ReturnType<typeof useLetterDocument>
