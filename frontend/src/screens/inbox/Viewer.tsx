/**
 * An attachment open beside its message (111, 162): the list folds to a strip, the message stays
 * as context, and the file fills the rest with its name, size and pages, the page arrows, the
 * download, full screen and close.
 *
 * The file is fetched as bytes and shown from a blob made here with a type chosen here: a PDF in
 * the browser's own PDF viewer, an image as an image. Nothing is ever rendered from the type the
 * file claims, and the download route still serves every file as an attachment (P8b), so a file
 * cannot make the portal run a page of its choosing. Any other kind of file is offered for
 * download only.
 */
import { useEffect, useState } from 'react'

import type { Schemas } from '../../api/client'
import {
  ChevronLeft,
  ChevronRight,
  CloseIcon,
  FullScreenIcon,
  ViewerDownloadIcon,
} from '../../components/icons'
import { fileSize } from '../../lib/vocab'
import { CARD } from './parts'

type Attachment = Schemas['AttachmentOut']

/** The types shown in place, and the type the blob is given for each. */
const SHOWN: Record<string, string> = {
  'application/pdf': 'application/pdf',
  'image/png': 'image/png',
  'image/jpeg': 'image/jpeg',
  'image/gif': 'image/gif',
}

const download = (file: Attachment) => `/api/records/attachments/${file.id}/download`

function ViewerTool({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string
  onClick: () => void
  disabled?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="inline-flex size-7 shrink-0 items-center justify-center rounded-chip hover:bg-hover disabled:opacity-40"
    >
      {children}
    </button>
  )
}

export function Viewer({
  file,
  page,
  onPage,
  onClose,
}: {
  file: Attachment
  page: number
  onPage: (page: number) => void
  onClose: () => void
}) {
  const shownAs = SHOWN[file.mime_type ?? '']
  const pages = shownAs === 'application/pdf' ? (file.page_count ?? 1) : 1
  const [url, setUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!shownAs) return
    let gone = false
    let made: string | null = null
    fetch(download(file), { credentials: 'same-origin' })
      .then((response) => {
        if (!response.ok) throw new Error(String(response.status))
        return response.arrayBuffer()
      })
      .then((bytes) => {
        if (gone) return
        made = URL.createObjectURL(new Blob([bytes], { type: shownAs }))
        setUrl(made)
      })
      .catch(() => {
        if (!gone) setFailed(true)
      })
    return () => {
      gone = true
      if (made) URL.revokeObjectURL(made)
    }
  }, [file, shownAs])

  function fullScreen() {
    const area = document.getElementById(`viewer-${file.id}`)
    void area?.requestFullscreen?.()
  }

  return (
    <section
      aria-label={file.filename}
      className={`${CARD} flex min-w-0 flex-1 flex-col overflow-hidden`}
    >
      <div className="flex items-center gap-2.5 border-b border-hairline bg-surface-toolbar px-4 py-[11px]">
        <span className="min-w-0 truncate text-125 font-semibold">{file.filename}</span>
        <span className="text-115 whitespace-nowrap text-muted">
          {[
            fileSize(file.size_bytes),
            shownAs === 'application/pdf' && file.page_count
              ? `${file.page_count} ${file.page_count === 1 ? 'page' : 'pages'}`
              : '',
          ]
            .filter(Boolean)
            .join(' · ')}
        </span>
        <span className="ml-auto flex items-center gap-0.5">
          {pages > 1 ? (
            <>
              <ViewerTool
                label="Previous page"
                disabled={page <= 1}
                onClick={() => onPage(page - 1)}
              >
                <ChevronLeft size={15} width={1.8} stroke="var(--color-ink-3)" />
              </ViewerTool>
              <span className="min-w-11 text-center text-115 whitespace-nowrap text-muted">
                {page} / {pages}
              </span>
              <ViewerTool
                label="Next page"
                disabled={page >= pages}
                onClick={() => onPage(page + 1)}
              >
                <ChevronRight size={15} width={1.8} stroke="var(--color-ink-3)" />
              </ViewerTool>
              <span className="mx-1.5 h-4 w-px bg-line" />
            </>
          ) : null}
          <ViewerTool label="Download" onClick={() => window.open(download(file), '_blank')}>
            <ViewerDownloadIcon size={15} width={1.8} stroke="var(--color-ink-3)" />
          </ViewerTool>
          {shownAs ? (
            <ViewerTool label="Full screen" onClick={fullScreen}>
              <FullScreenIcon size={15} width={1.8} stroke="var(--color-ink-3)" />
            </ViewerTool>
          ) : null}
          <ViewerTool label="Close" onClick={onClose}>
            <CloseIcon size={15} width={1.8} stroke="var(--color-ink-3)" />
          </ViewerTool>
        </span>
      </div>
      <div
        id={`viewer-${file.id}`}
        className="relative flex flex-[0_0_640px] items-start justify-center overflow-hidden bg-fill-subtle-4 px-10 py-7"
      >
        {!shownAs ? (
          <div className="m-auto text-center text-125 text-ink-2">
            This kind of file is not shown here.{' '}
            <a href={download(file)} className="font-medium">
              Download it
            </a>
            .
          </div>
        ) : failed ? (
          <div className="m-auto text-125 text-ink-2">Couldn’t open the file.</div>
        ) : !url ? (
          <div className="m-auto text-125 text-muted">Opening the file…</div>
        ) : shownAs === 'application/pdf' ? (
          <object
            // A new page is a new address for the PDF viewer; the frame is remade to go there.
            key={page}
            data={`${url}#page=${page}&toolbar=0&navpanes=0`}
            type="application/pdf"
            aria-label={file.filename}
            className="h-full w-full bg-card-white shadow-sm-4"
          >
            <div className="flex h-full items-center justify-center text-125 text-ink-2">
              Your browser does not show PDFs here.&nbsp;
              <a href={download(file)} className="font-medium">
                Download it
              </a>
            </div>
          </object>
        ) : (
          <img
            src={url}
            alt={file.filename}
            className="max-h-full max-w-full bg-card-white object-contain shadow-sm-4"
          />
        )}
      </div>
    </section>
  )
}
