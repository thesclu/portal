import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, type PointerEvent as ReactPointerEvent } from 'react'

import { api, type Schemas } from '../../api/client'
import type { Me } from '../../app/session'
import { unwrap } from '../../components/ActionDialog'
import { PillChoice } from '../../components/Fields'
import { signatureFileProblem, useSignaturePad } from '../../components/signaturePad'
import { signatureSrc } from '../settings/Signature'
import { SetupActions, SetupTitle } from './parts'

type Stored = Schemas['SignatureOut']

/** A new image waiting to be stored: drawn on the pad, or a chosen file. */
interface Fresh {
  blob: Blob
  name: string
  url: string
}

/**
 * Step 3, the signature (008 draw, 009 upload): stored where Settings → Profile → Your signature
 * keeps it (`/core/users/{id}/signature`, P20f), so the two edit the same thing. A signature the
 * person already has is shown for them to keep (the step only confirms it: Draw shows a drawn one
 * on the pad, Upload an uploaded one with its file name); Clear or Replace changes it. The
 * preview is a letter's closing as 008 draws it: "Sincerely," the signature, the name, the title
 * and the organisation, in the stand-in hand while nothing is drawn or chosen. Next stores
 * whatever is new; it waits while the step shows no signature at all.
 */
export function SignatureStep({
  me,
  stored,
  orgName,
  onBack,
  onNext,
  nextLabel,
}: {
  me: Me
  stored: Stored
  orgName: string
  onBack?: () => void
  onNext: () => Promise<void>
  nextLabel: string
}) {
  const client = useQueryClient()
  const storedUrl = stored.has_image ? signatureSrc(me.id, stored.updated_at) : null
  const [mode, setMode] = useState<'draw' | 'upload'>(
    stored.source === 'uploaded' ? 'upload' : 'draw',
  )
  const pad = useSignaturePad(mode === 'draw')
  const { attach, onPointerMove } = pad
  // The stored drawn signature, kept on the pad until it is cleared or drawn over.
  const [kept, setKept] = useState(stored.has_image && stored.source === 'drawn')
  const [drawing, setDrawing] = useState<Fresh | null>(null)
  const [file, setFile] = useState<Fresh | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => () => (drawing ? URL.revokeObjectURL(drawing.url) : undefined), [drawing])
  useEffect(() => () => (file ? URL.revokeObjectURL(file.url) : undefined), [file])

  const keptUpload = stored.has_image && stored.source === 'uploaded'
  // What the step shows in the open tab, and so what Next keeps.
  const shown: { url: string; fresh: Fresh | null } | null =
    mode === 'draw'
      ? drawing
        ? { url: drawing.url, fresh: drawing }
        : kept && storedUrl
          ? { url: storedUrl, fresh: null }
          : null
      : file
        ? { url: file.url, fresh: file }
        : keptUpload && storedUrl
          ? { url: storedUrl, fresh: null }
          : null

  async function captureDrawing() {
    const png = await pad.toPng()
    setDrawing(png ? { blob: png, name: 'signature.png', url: URL.createObjectURL(png) } : null)
  }

  function startStroke(event: ReactPointerEvent<HTMLCanvasElement>) {
    setKept(false)
    pad.onPointerDown(event)
  }

  function endStroke() {
    pad.onPointerUp()
    void captureDrawing()
  }

  function clear() {
    setKept(false)
    pad.clear()
    setDrawing(null)
  }

  function choose(picked: File | undefined) {
    setError(null)
    if (!picked) return
    const problem = signatureFileProblem(picked)
    if (problem) return setError(problem)
    setFile({ blob: picked, name: picked.name, url: URL.createObjectURL(picked) })
  }

  async function next() {
    if (!shown) return
    setBusy(true)
    setError(null)
    try {
      if (shown.fresh) {
        const payload = new FormData()
        payload.set('file', shown.fresh.blob, shown.fresh.name)
        const options = {
          params: { path: { user_id: me.id } },
          body: payload as never,
          bodySerializer: (body: unknown) => body as FormData,
        }
        unwrap(
          mode === 'draw'
            ? await api.POST('/api/core/users/{user_id}/signature/draw', options)
            : await api.POST('/api/core/users/{user_id}/signature/upload', options),
        )
        await client.invalidateQueries({ queryKey: ['get', '/api/core/users/{user_id}/signature'] })
      }
      await onNext()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Your signature was not saved.')
    } finally {
      setBusy(false)
    }
  }

  const fileName = file?.name ?? (keptUpload ? stored.file_name : null)

  return (
    <>
      <SetupTitle title="Your signature" sub="Goes on your letters and email replies." />
      <PillChoice
        label="Draw or upload"
        size="md"
        options={[
          { value: 'draw', label: 'Draw' },
          { value: 'upload', label: 'Upload' },
        ]}
        value={mode}
        onChange={(value) => {
          setError(null)
          setMode(value as 'draw' | 'upload')
          setDrawing(null)
          setKept(stored.has_image && stored.source === 'drawn')
        }}
      />
      {mode === 'draw' ? (
        <div className="relative h-40 rounded-14 bg-card-white shadow-field">
          <span aria-hidden className="absolute right-7 bottom-[42px] left-7 h-px bg-line-2" />
          {kept && storedUrl ? (
            <img
              src={storedUrl}
              alt="Your signature"
              className="pointer-events-none absolute bottom-[30px] left-11 max-h-[96px] max-w-[70%] object-contain"
            />
          ) : null}
          <canvas
            ref={attach}
            aria-label="Signature pad: draw with a mouse, a finger or a pen"
            className="absolute inset-0 size-full cursor-crosshair touch-none rounded-14 text-signature-ink"
            onPointerDown={startStroke}
            onPointerMove={onPointerMove}
            onPointerUp={endStroke}
            onPointerCancel={endStroke}
          />
          {pad.drawn || kept ? (
            <button
              type="button"
              onClick={clear}
              className="absolute top-3 right-3.5 text-12 font-medium text-ink-2 hover:text-ink"
            >
              Clear
            </button>
          ) : (
            <span className="pointer-events-none absolute top-3 left-3.5 text-12 text-muted">
              Sign above the line
            </span>
          )}
        </div>
      ) : shown ? (
        <div className="relative flex h-[120px] items-center justify-center rounded-14 bg-card-white shadow-field">
          <img
            src={shown.url}
            alt="Your signature"
            className="max-h-20 max-w-[70%] object-contain"
          />
          {fileName ? (
            <span className="absolute top-3 left-3.5 max-w-[55%] truncate text-12 text-muted">
              {fileName}
            </span>
          ) : null}
          <label className="absolute top-3 right-3.5 cursor-pointer text-12 font-medium text-ink-2 hover:text-ink">
            Replace
            <input
              type="file"
              accept="image/png,image/jpeg"
              className="sr-only"
              onChange={(event) => choose(event.target.files?.[0])}
            />
          </label>
        </div>
      ) : (
        <label
          className="relative flex h-[120px] cursor-pointer flex-col items-center justify-center gap-1 rounded-14 bg-card-white text-125 text-ink-2 shadow-field"
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault()
            choose(event.dataTransfer.files[0])
          }}
        >
          <span>
            Drop an image or <span className="font-medium text-brand">browse</span>
          </span>
          <span className="text-12 text-muted">PNG or JPEG, up to 2 MB</span>
          <input
            type="file"
            accept="image/png,image/jpeg"
            aria-label="Signature image"
            className="absolute inset-0 cursor-pointer opacity-0"
            onChange={(event) => choose(event.target.files?.[0])}
          />
        </label>
      )}
      <span className="-mb-3.5 text-12 text-muted">Preview</span>
      <div className="rounded-14 bg-surface-soft px-5 py-[18px] font-letter text-14 leading-[1.5] text-letter-ink">
        Sincerely,
        <div className="mt-0.5 -ml-0.5 flex h-[50px] items-end font-signature text-signature-md leading-none text-signature-ink">
          {shown ? (
            <img src={shown.url} alt="" className="max-h-[50px] max-w-[260px] object-contain" />
          ) : (
            me.name
          )}
        </div>
        {me.name}
        {me.title ? (
          <>
            <br />
            {me.title}
          </>
        ) : null}
        {orgName ? (
          <>
            <br />
            {orgName}
          </>
        ) : null}
      </div>
      <SetupActions
        onBack={onBack}
        nextLabel={nextLabel}
        onNext={() => void next()}
        disabled={!shown}
        busy={busy}
        error={error}
        hint="Draw or upload your signature first"
      />
    </>
  )
}
