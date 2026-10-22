/**
 * Templates in the drafting tool (P21c; DECISIONS 30e, 49): the portal's shell around the fork's
 * `/embed/templates…` pages, which draw everything in their box, the page title included. They
 * talk `sclu-templates/1` (apps/mike SCLU.md 11.3, 12.5, 13.6 c), origin-checked both ways:
 *
 *  - `viewport` (the parent's visible area relative to the frame) after `ready` and whenever the
 *    frame moves or the window changes, so the frame centres its dialogs where the design does;
 *  - `navigate`: the frame moved (or, with `?nav=parent`, asks the portal to move): the portal's
 *    address follows, without reloading the frame it came from;
 *  - `modal`: while a dialog is open the portal dims everything outside the frame and grows the
 *    frame's box by the `room` asked for (the Settings box: 70 px left, 39 above), then sends
 *    `viewport` again; on close the box goes back;
 *  - `template`: the portal's mirror of a template made or changed, so stage 1 offers it;
 *  - `problem`: signed out sends the person to the drafting tool's sign-in through the portal.
 */
import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'

import { api } from '../../api/client'
import { setPageLabel } from '../../app/presence'
import { Callout } from '../../components/ui'
import {
  acceptMessage,
  grownBox,
  parseTemplatesMessage,
  post,
  signInAgainUrl,
  templatesFramePath,
  templatesPortalPath,
  viewportOf,
  type Room,
} from '../../lib/fork'

const SCRIM = 'rgba(11,31,58,.28)'

export function TemplatesFrame({
  web,
  origin,
  path,
  navParent = false,
  height,
  title,
}: {
  web: string
  origin: string
  /** The fork's route, e.g. `/embed/templates/<id>?tab=citations`. */
  path: string
  /** The frame asks the portal to move instead of moving (Settings frames the list only). */
  navParent?: boolean
  height: string
  /** The page's name for the browser tab and who's online, when no portal header names it (the
   *  frame draws the title itself). */
  title?: string
}) {
  const navigate = useNavigate()
  const client = useQueryClient()
  const box = useRef<HTMLDivElement>(null)
  const frame = useRef<HTMLIFrameElement>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  const [room, setRoom] = useState<Room | null>(null)
  const [dimmed, setDimmed] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  // The page the frame shows. It changes when the portal's address is moved from outside the
  // frame (the sidebar, a link); a move the frame itself reported does not reload it.
  const [shown, setShown] = useState(path)
  const reported = useRef(path)
  useEffect(() => {
    if (!title) return
    document.title = `${title} · SCLU Portal`
    setPageLabel(title)
    return () => setPageLabel('')
  }, [title])
  useEffect(() => {
    if (path !== reported.current) {
      reported.current = path
      setShown(path)
    }
  }, [path])
  const src = `${web.replace(/\/+$/, '')}${shown}${navParent ? `${shown.includes('?') ? '&' : '?'}nav=parent` : ''}`

  const sendViewport = useCallback(() => {
    const at = frame.current?.getBoundingClientRect()
    if (!at) return
    post(frame.current?.contentWindow, origin, viewportOf(at, window))
  }, [origin])

  useEffect(() => {
    const element = box.current
    if (!element) return
    const observe = new ResizeObserver(([entry]) =>
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height }),
    )
    observe.observe(element)
    window.addEventListener('resize', sendViewport)
    document.addEventListener('scroll', sendViewport, true)
    return () => {
      observe.disconnect()
      window.removeEventListener('resize', sendViewport)
      document.removeEventListener('scroll', sendViewport, true)
    }
  }, [sendViewport])
  // After the box grows or shrinks for a dialog, the frame is told where the parent's area is.
  useEffect(() => {
    const later = window.requestAnimationFrame(sendViewport)
    return () => window.cancelAnimationFrame(later)
  }, [room, size, sendViewport])

  useEffect(() => {
    function onMessage(event: MessageEvent) {
      const message = parseTemplatesMessage(
        acceptMessage(event, origin, frame.current?.contentWindow),
      )
      if (!message) return
      switch (message.type) {
        case 'ready':
          setProblem(null)
          sendViewport()
          break
        case 'navigate': {
          const portal = templatesPortalPath(message.page, message.id, message.tab)
          const [pathname, search = ''] = portal.split('?')
          if (!navParent) reported.current = templatesFramePath(pathname, `?${search}`) ?? shown
          void navigate(portal)
          break
        }
        case 'modal':
          setDimmed(message.open)
          setRoom(message.open ? (message.room ?? null) : null)
          break
        case 'template':
          void api
            .POST('/api/drafting/fork/templates', {
              body: {
                template_id: message.template_id,
                name: message.name,
                archived: message.archived,
                event: message.event,
              },
            })
            .then(() => client.invalidateQueries({ queryKey: ['get', '/api/drafting/templates'] }))
          break
        case 'problem':
          if (message.code === 'signed_out') {
            window.location.assign(signInAgainUrl(web, window.location.href))
            return
          }
          setProblem(
            message.code === 'no_organisation'
              ? 'The drafting tool has no organisation for you yet. Sign in to it once through the portal.'
              : message.code === 'not_found'
                ? 'The drafting tool cannot find that page, or you cannot open it.'
                : 'The drafting tool could not show this page. Reload to try again.',
          )
          break
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [origin, web, navigate, navParent, client, sendViewport, shown])

  const grown = grownBox(size, room)
  return (
    <>
      {problem ? (
        <div className="pb-3">
          <Callout tone="danger" role="alert">
            {problem}
          </Callout>
        </div>
      ) : null}
      {dimmed ? (
        <div aria-hidden className="fixed inset-0 z-[60]" style={{ background: SCRIM }} />
      ) : null}
      <div ref={box} className="relative w-full" style={{ height }}>
        <iframe
          ref={frame}
          src={src}
          title="Templates in the drafting tool"
          className="absolute block border-0 bg-transparent"
          style={{
            left: grown.left,
            top: grown.top,
            width: grown.width || '100%',
            height: grown.height || '100%',
            zIndex: dimmed ? 61 : undefined,
          }}
          allow="clipboard-write"
        />
      </div>
    </>
  )
}
