import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'

import { api } from '../../api/client'
import { $api } from '../../api/query'
import { useMe } from '../../app/session'
import { unwrap } from '../../components/ActionDialog'
import { Dialog } from '../../components/Dialog'
import { PillChoice, RaisedInput } from '../../components/Fields'
import { signatureFileProblem, useSignaturePad } from '../../components/signaturePad'
import { Button, cx } from '../../components/ui'

/**
 * Each person's own signature (217; the pad is 008/009's): drawn with a pointer, a finger or a
 * pen on a pad, or uploaded as an image, and kept per person on the server. Nothing uses it yet:
 * putting it on letters is a later part. The organisation's letterhead never carries one.
 */

const signatureKey = (userId: string) =>
  ['get', '/api/core/users/{user_id}/signature', { params: { path: { user_id: userId } } }] as const

export function useSignature(userId: string, enabled = true) {
  return $api.useQuery(
    'get',
    '/api/core/users/{user_id}/signature',
    { params: { path: { user_id: userId } } },
    { enabled },
  )
}

/** The stored image's address; `version` (its last change) makes a replaced one load anew. */
export function signatureSrc(userId: string, version?: string | null): string {
  return `/api/core/users/${userId}/signature/image${version ? `?v=${encodeURIComponent(version)}` : ''}`
}

/** The signature, 28 px high, as 217 draws the "Signature image" field. */
export function SignatureThumb({ userId, version }: { userId: string; version?: string | null }) {
  return (
    <img
      src={signatureSrc(userId, version)}
      alt="Your signature"
      className="inline-block h-7 w-auto max-w-[180px] object-contain align-middle"
    />
  )
}

/** What is waiting to be saved: a drawing from the pad, or a file. */
interface Pending {
  file: Blob
  name: string
  source: 'drawn' | 'uploaded'
  url: string
}

const LABEL = 'text-12 font-medium text-ink-2'
const BOX =
  'box-border flex h-[35px] items-center rounded-12 px-3 text-13 text-ink shadow-field bg-card-white'

/**
 * Settings → Profile → Your signature (217): the signature image (Draw or Upload open the pad),
 * the name and title it is signed with (the title since P20i), and a preview of how it sits
 * under a letter. Save signature stores what was drawn or uploaded and the name and title if they
 * changed; Cancel goes back to Profile.
 */
export function Signature() {
  const me = useMe()
  const navigate = useNavigate()
  const client = useQueryClient()
  const signature = useSignature(me.id)
  const org = $api.useQuery('get', '/api/core/settings', {}, { select: orgName })
  const [pending, setPending] = useState<Pending | null>(null)
  const [pad, setPad] = useState<'draw' | 'upload' | null>(null)
  const [name, setName] = useState(me.name)
  // P20i: the title the person signs with (217's "Title"), first confirmed in the setup (007).
  const [title, setTitle] = useState(me.title)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => () => (pending ? URL.revokeObjectURL(pending.url) : undefined), [pending])

  const stored = signature.data?.has_image ? signatureSrc(me.id, signature.data.updated_at) : null
  const image = pending?.url ?? stored
  const changedName = name.trim() !== me.name && name.trim() !== ''
  const changedTitle = title.trim() !== me.title

  async function save() {
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      if (pending) {
        const payload = new FormData()
        payload.set('file', pending.file, pending.name)
        const options = {
          params: { path: { user_id: me.id } },
          body: payload as never,
          bodySerializer: (body: unknown) => body as FormData,
        }
        unwrap(
          pending.source === 'drawn'
            ? await api.POST('/api/core/users/{user_id}/signature/draw', options)
            : await api.POST('/api/core/users/{user_id}/signature/upload', options),
        )
        setPending(null)
        await client.invalidateQueries({ queryKey: signatureKey(me.id).slice(0, 2) })
      }
      if (changedName || changedTitle) {
        const updated = unwrap(
          await api.PATCH('/api/core/auth/me', {
            body: { ...(changedName ? { name: name.trim() } : {}), title },
          }),
        )
        client.setQueryData(['get', '/api/core/auth/me', {}], updated)
      }
      setSaved(true)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'It was not saved.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="flex flex-col gap-4 rounded-16 bg-card px-[22px] py-5 shadow-card">
      <h2 className="text-md-lg font-semibold text-ink">Your signature</h2>
      <div className="flex items-start gap-7">
        <div className="flex min-w-0 flex-1 flex-col gap-3.5">
          <div className="flex flex-col gap-1.5">
            <span className={LABEL}>Signature image</span>
            <span className="flex min-h-7 items-center gap-3">
              {image ? (
                <img
                  src={image}
                  alt="Your signature"
                  className="h-7 w-auto max-w-[180px] object-contain"
                />
              ) : signature.isPending ? null : (
                <span className="text-13 text-muted">None yet</span>
              )}
              <button
                type="button"
                onClick={() => setPad('draw')}
                className="text-125 font-medium text-brand hover:text-brand-dark"
              >
                Draw
              </button>
              <button
                type="button"
                onClick={() => setPad('upload')}
                className="text-125 font-medium text-brand hover:text-brand-dark"
              >
                Upload
              </button>
            </span>
          </div>
          <div className="grid grid-cols-2 gap-3.5">
            <label className="flex min-w-0 flex-col gap-1.5">
              <span className={LABEL}>Name</span>
              <RaisedInput
                className="h-[35px]! px-[10.5px]"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <label className="flex min-w-0 flex-col gap-1.5">
              <span className={LABEL}>Title</span>
              <RaisedInput
                className="h-[35px]! px-[10.5px]"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
              />
            </label>
            <div className="flex min-w-0 flex-col gap-1.5">
              <span className={LABEL}>Email</span>
              <span className={cx(BOX, 'bg-page text-ink-2 shadow-none')}>
                <span className="truncate">{me.email}</span>
              </span>
            </div>
          </div>
          {signature.data?.updated_at && !pending ? (
            <p className="text-12 text-muted">
              {signature.data.source === 'uploaded' ? 'Uploaded' : 'Drawn'}
              {signature.data.file_name && signature.data.source === 'uploaded'
                ? ` from ${signature.data.file_name}`
                : ''}
              {signature.data.updated_by_name && signature.data.updated_by_name !== me.name
                ? ` by ${signature.data.updated_by_name}`
                : ''}
            </p>
          ) : null}
          {pending ? <p className="text-12 text-muted">Not saved yet.</p> : null}
          {error ? (
            <p role="alert" className="text-12 text-danger">
              {error}
            </p>
          ) : saved ? (
            <p role="status" className="text-12 text-ok">
              Saved.
            </p>
          ) : null}
        </div>
        <div className="box-content flex w-[290px] shrink-0 flex-col gap-2 rounded-6 bg-card-white px-[26px] pt-[26px] pb-[18px] font-letter shadow-raised-strong">
          {['w-full', 'w-full', 'w-full', 'w-[55%]'].map((width, index) => (
            <span
              key={index}
              aria-hidden
              className={cx('block h-[5px] rounded-xs bg-fill-muted', width)}
            />
          ))}
          <div className="h-3" />
          <div className="-ml-0.5 mt-0.5 flex h-[42px] items-end">
            {image ? (
              <img src={image} alt="" className="max-h-[42px] w-auto max-w-full object-contain" />
            ) : (
              // The stand-in hand, until one is drawn or uploaded.
              <span className="font-signature text-[38px] leading-none text-signature-ink">
                {name.trim() || me.name}
              </span>
            )}
          </div>
          <div className="text-12 leading-[1.45] text-letter-ink">
            {name.trim() || me.name}
            {title.trim() ? (
              <>
                <br />
                {title.trim()}
              </>
            ) : null}
            {org.data ? (
              <>
                <br />
                {org.data}
              </>
            ) : null}
            <br />
            {me.email}
          </div>
        </div>
      </div>
      <div className="flex justify-end gap-1.5">
        <button
          type="button"
          onClick={() => void navigate('/settings/profile')}
          className="rounded-9 px-3 py-[9px] text-13 font-medium text-ink-2 hover:bg-hover"
        >
          Cancel
        </button>
        <button
          type="button"
          disabled={busy || (!pending && !changedName && !changedTitle)}
          aria-busy={busy || undefined}
          onClick={() => void save()}
          className="rounded-9 bg-brand px-4 py-[9px] text-13 font-semibold text-white hover:bg-brand-dark disabled:opacity-55"
        >
          Save signature
        </button>
      </div>
      {pad ? (
        <SignaturePad
          start={pad}
          onClose={() => setPad(null)}
          onUse={(next) => {
            setPending(next)
            setSaved(false)
            setPad(null)
          }}
        />
      ) : null}
    </section>
  )
}

function orgName(settings: Array<{ key: string; value: unknown }>): string {
  const value = settings.find((s) => s.key === 'org.name')?.value
  return typeof value === 'string' ? value : ''
}

// ---- The pad ------------------------------------------------------------------------------------

/**
 * Draw or Upload (008, 009): the pad takes a pointer, a finger or a pen; Clear starts again. The
 * drawing is cropped to what was drawn and kept as a PNG with a clear ground
 * (`components/signaturePad.ts`, shared with the new-user setup). An upload is a PNG or JPEG
 * image, at most 2 MB (the server's limit too).
 */
function SignaturePad({
  start,
  onClose,
  onUse,
}: {
  start: 'draw' | 'upload'
  onClose: () => void
  onUse: (pending: Pending) => void
}) {
  const [mode, setMode] = useState(start)
  const { attach, drawn, clear, toPng, onPointerDown, onPointerMove, onPointerUp } =
    useSignaturePad(mode === 'draw')
  const [file, setFile] = useState<{ file: File; url: string } | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => () => (file ? URL.revokeObjectURL(file.url) : undefined), [file])

  function choose(picked: File | undefined) {
    setError(null)
    if (!picked) return
    const problem = signatureFileProblem(picked)
    if (problem) return setError(problem)
    setFile({ file: picked, url: URL.createObjectURL(picked) })
  }

  async function use() {
    setError(null)
    if (mode === 'draw') {
      const png = await toPng()
      if (!png) return setError('Draw your signature first.')
      onUse({ file: png, name: 'signature.png', source: 'drawn', url: URL.createObjectURL(png) })
    } else {
      if (!file) return setError('Choose an image first.')
      onUse({
        file: file.file,
        name: file.file.name,
        source: 'uploaded',
        url: URL.createObjectURL(file.file),
      })
    }
  }

  return (
    <Dialog
      title="Your signature"
      subtitle="Draw it, or upload an image of it."
      open
      onClose={onClose}
      width={520}
      top={90}
      footer={
        <>
          <Button kind="text" pill onClick={onClose}>
            Cancel
          </Button>
          <Button
            kind="primary"
            pill
            disabled={mode === 'draw' ? !drawn : !file}
            onClick={() => void use()}
          >
            Use this signature
          </Button>
        </>
      }
    >
      <PillChoice
        label="Draw or upload"
        size="md"
        options={[
          { value: 'draw', label: 'Draw' },
          { value: 'upload', label: 'Upload' },
        ]}
        value={mode}
        onChange={(value) => setMode(value as 'draw' | 'upload')}
      />
      {mode === 'draw' ? (
        <div className="relative h-40 rounded-14 bg-card-white shadow-field">
          <span aria-hidden className="absolute right-7 bottom-[42px] left-7 h-px bg-line-2" />
          <canvas
            ref={attach}
            aria-label="Signature pad: draw with a mouse, a finger or a pen"
            className="absolute inset-0 size-full cursor-crosshair touch-none rounded-14 text-signature-ink"
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
          />
          {drawn ? (
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
      ) : file ? (
        <div className="relative flex h-[120px] items-center justify-center rounded-14 bg-card-white shadow-field">
          <img src={file.url} alt="" className="max-h-[80px] max-w-[80%] object-contain" />
          <span className="absolute top-3 left-3.5 max-w-[60%] truncate text-12 text-muted">
            {file.file.name}
          </span>
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
        <label className="relative block cursor-pointer rounded-10 border-[1.5px] border-dashed border-line-control bg-surface-row p-[18px] text-center text-125 text-ink-2">
          Drop an image or <span className="font-medium text-brand">browse</span>
          <br />
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
      {error ? (
        <p
          role="alert"
          className="rounded-10 bg-danger-tint px-3.5 py-[11px] text-125 text-danger-2"
        >
          {error}
        </p>
      ) : null}
    </Dialog>
  )
}
