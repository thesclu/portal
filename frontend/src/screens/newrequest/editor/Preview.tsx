/**
 * The server's own rendering of the letter: as HTML for "Preview as", and as the PDF. `base` is
 * the letter's API path (`/api/drafting/drafts/{id}` or `/api/drafting/templates/{id}`); the
 * same `/html` and `/pdf` hang off both.
 */
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'

import { messageOf } from '../../../api/query'
import { Notice } from '../../../components/ui'
import { Loading } from '../../../components/States'

/**
 * Fit an 8.5in page into a column narrower than it: the column and the page are measured, the
 * page is scaled from its top-left corner, and the box around it takes the scaled size so what
 * follows stays in place. With `target` the page is drawn at most that wide (045: 784 px, the
 * letter at the left of its column; 049: 510 px in stage 4's packet), as the screens draw a US
 * Letter page at 1in margins, scaled. The scale is also given to the page as `--page-scale`, so
 * the chrome drawn on it (chips, handles, the page count) keeps the screen's pixel sizes.
 */
export function useFitScale(target?: number, center = false) {
  const [column, setColumn] = useState<HTMLDivElement | null>(null)
  const [page, setPage] = useState<HTMLDivElement | null>(null)
  const [fit, setFit] = useState({ scale: 1, height: 0, width: 0 })
  useEffect(() => {
    if (!column || !page) return
    const observer = new ResizeObserver(() => {
      const room = column.clientWidth
      const width = target ? Math.min(target, room) : room
      const pageWidth = page.offsetWidth
      const height = page.offsetHeight
      const scale = pageWidth > 0 && width > 0 ? Math.min(1, width / pageWidth) : 1
      setFit((f) =>
        f.scale === scale && f.height === height && f.width === pageWidth
          ? f
          : { scale, height, width: pageWidth },
      )
    })
    observer.observe(column)
    observer.observe(page)
    return () => observer.disconnect()
  }, [column, page, target])
  const columnStyle: CSSProperties = { overflow: 'hidden' }
  const boxStyle: CSSProperties = {
    height: fit.height * fit.scale,
    width: target && fit.width ? fit.width * fit.scale : undefined,
    margin: center ? '0 auto' : undefined,
  }
  const pageStyle = {
    width: '8.5in',
    margin: target ? 0 : '0 auto',
    transform: fit.scale < 1 ? `scale(${fit.scale})` : undefined,
    transformOrigin: 'top left',
    '--page-scale': fit.scale,
  } as CSSProperties
  return { column: setColumn, page: setPage, scale: fit.scale, columnStyle, boxStyle, pageStyle }
}

/** Children laid out as an 8.5in page, scaled to fit where they are put (at most `width`). */
export function FitPage({
  children,
  width,
  center = false,
  className,
}: {
  children: ReactNode
  width?: number
  /** In the middle of the room it is given (049), not at its left (045). */
  center?: boolean
  className?: string
}) {
  const { column, page, columnStyle, boxStyle, pageStyle } = useFitScale(width, center)
  return (
    <div ref={column} style={columnStyle} className={className}>
      <div style={boxStyle}>
        <div ref={page} style={pageStyle}>
          {children}
        </div>
      </div>
    </div>
  )
}

/** The server's page with its sheet height let go, for showing on screen: the screens draw the
 *  page as tall as the letter (045, 046, 049); the PDF keeps its sheets. */
export function screenPage(html: string): string {
  const rule = '<style>.page { min-height: 0 !important; }</style>'
  return html.includes('</head>') ? html.replace('</head>', `${rule}</head>`) : rule + html
}

/** A fetch that rejects the way the API client does, so `messageOf` reads the API's words. */
async function fetchOk(url: string): Promise<Response> {
  const response = await fetch(url, { credentials: 'include' })
  if (!response.ok) {
    let detail: string | undefined
    try {
      detail = (await response.json()).detail
    } catch {
      detail = undefined
    }
    throw { status: response.status, detail }
  }
  return response
}

function query(agencyId: string, version: number): string {
  const params = new URLSearchParams()
  if (agencyId) params.set('agency_id', agencyId)
  params.set('v', String(version))
  return params.toString()
}

export function pdfUrl(base: string, agencyId: string, version: number): string {
  return `${base}/pdf?${query(agencyId, version)}`
}

/** How many pages the PDF has, read from its `X-Page-Count` header after each save. */
export function usePageCount(base: string, version: number): number | null {
  const [count, setCount] = useState<number | null>(null)
  useEffect(() => {
    let on = true
    fetchOk(pdfUrl(base, '', version))
      .then((response) => {
        void response.body?.cancel()
        const n = Number(response.headers.get('X-Page-Count'))
        if (on && Number.isFinite(n) && n > 0) setCount(n)
      })
      .catch(() => {})
    return () => {
      on = false
    }
  }, [base, version])
  return count
}

/** The page as the server renders it for one agency, in a frame sized to its content. */
export function HtmlPreview({
  base,
  agencyId,
  version,
  width,
}: {
  base: string
  agencyId: string
  version: number
  /** At most this wide (045: 784 px). */
  width?: number
}) {
  const [page, setPage] = useState<{ html: string | null; error: string | null }>({
    html: null,
    error: null,
  })
  const [height, setHeight] = useState(1056)
  const frame = useRef<HTMLIFrameElement>(null)
  useEffect(() => {
    let on = true
    fetchOk(`${base}/html?${query(agencyId, version)}`)
      .then((response) => response.text())
      .then((html) => {
        if (on) setPage({ html, error: null })
      })
      .catch((failure: unknown) => {
        if (on) setPage({ html: null, error: messageOf(failure) })
      })
    return () => {
      on = false
    }
  }, [base, agencyId, version])
  if (page.error) return <Notice tone="danger">{page.error}</Notice>
  if (page.html === null) return <Loading what="Rendering the letter" />
  return (
    <FitPage width={width}>
      <iframe
        ref={frame}
        title="The letter as this agency reads it"
        sandbox="allow-same-origin"
        srcDoc={screenPage(page.html)}
        style={{ height }}
        onLoad={() => {
          const doc = frame.current?.contentDocument
          if (doc) setHeight(doc.documentElement.offsetHeight + 2)
        }}
        className="block w-full border-0 bg-card-white shadow-page"
      />
    </FitPage>
  )
}

export function PdfView({
  base,
  agencyId = '',
  version,
}: {
  base: string
  agencyId?: string
  version: number
}) {
  const url = pdfUrl(base, agencyId, version)
  // The frame's `load` is the only sign the browser could show the PDF; after a while without
  // it the link out is the way.
  const [loadedUrl, setLoadedUrl] = useState<string | null>(null)
  const [stalledUrl, setStalledUrl] = useState<string | null>(null)
  useEffect(() => {
    const wait = window.setTimeout(() => setStalledUrl(url), 5000)
    return () => window.clearTimeout(wait)
  }, [url])
  const loaded = loadedUrl === url
  const stalled = !loaded && stalledUrl === url
  return (
    <div>
      <iframe
        title="The letter as a PDF"
        src={url}
        onLoad={() => setLoadedUrl(url)}
        className="block w-full rounded-10 border border-line bg-card-white"
        style={{ height: 'calc(100vh - 15rem)', minHeight: 600 }}
      />
      {stalled ? (
        <Notice tone="warn">
          Your browser could not show the PDF here.{' '}
          <a href={url} target="_blank" rel="noreferrer" className="font-semibold underline">
            Open the PDF in a new tab
          </a>
        </Notice>
      ) : (
        <p className="mt-2 text-center text-12 text-muted">
          <a href={url} target="_blank" rel="noreferrer" className="underline">
            Open the PDF in a new tab
          </a>
        </p>
      )}
    </div>
  )
}
