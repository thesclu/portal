import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from 'react'

/**
 * The signature pad's drawing (008; P20f's dialog on 217 and P20i's setup step share it): a
 * pointer, a finger or a pen draws on a canvas as many device pixels as its box, so the line is
 * sharp on any screen; Clear starts again; the drawing is cropped to its strokes with a little
 * room and kept as a PNG with a clear ground. The stroke takes the canvas's own CSS colour.
 */

export const SIGNATURE_STROKE = 2.4
/** The largest image the server takes (core/services/signatures.py: two megabytes). */
export const MAX_SIGNATURE_BYTES = 2 * 1024 * 1024

/** Why a chosen file cannot be a signature, or null when it can. */
export function signatureFileProblem(file: { type: string; size: number }): string | null {
  if (!['image/png', 'image/jpeg'].includes(file.type)) return 'Choose a PNG or JPEG image.'
  if (file.size > MAX_SIGNATURE_BYTES) return 'The image is larger than 2 MB.'
  return null
}

interface Bounds {
  x0: number
  y0: number
  x1: number
  y1: number
}

/** `active`: the pad is on screen (its canvas is mounted and sized when this turns true). */
export function useSignaturePad(active: boolean) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const drawing = useRef<{ x: number; y: number } | null>(null)
  const bounds = useRef<Bounds | null>(null)
  const [drawn, setDrawn] = useState(false)

  useEffect(() => {
    if (!active) return
    const element = canvas.current
    if (!element) return
    const ratio = window.devicePixelRatio || 1
    const box = element.getBoundingClientRect()
    element.width = Math.round(box.width * ratio)
    element.height = Math.round(box.height * ratio)
    const context = element.getContext('2d')
    if (!context) return
    context.scale(ratio, ratio)
    context.lineCap = 'round'
    context.lineJoin = 'round'
    context.lineWidth = SIGNATURE_STROKE
    context.strokeStyle = getComputedStyle(element).color
    context.fillStyle = context.strokeStyle
    bounds.current = null
    setDrawn(false)
  }, [active])

  const grow = (x: number, y: number) => {
    const b = bounds.current
    bounds.current = b
      ? {
          x0: Math.min(b.x0, x),
          y0: Math.min(b.y0, y),
          x1: Math.max(b.x1, x),
          y1: Math.max(b.y1, y),
        }
      : { x0: x, y0: y, x1: x, y1: y }
  }

  function onPointerDown(event: ReactPointerEvent<HTMLCanvasElement>) {
    event.preventDefault()
    try {
      event.currentTarget.setPointerCapture(event.pointerId)
    } catch {
      // A pointer the browser no longer tracks: the stroke still draws while it moves.
    }
    const box = event.currentTarget.getBoundingClientRect()
    const at = { x: event.clientX - box.left, y: event.clientY - box.top }
    drawing.current = at
    const context = event.currentTarget.getContext('2d')
    context?.beginPath()
    context?.arc(at.x, at.y, SIGNATURE_STROKE / 2, 0, Math.PI * 2)
    context?.fill()
    grow(at.x, at.y)
    setDrawn(true)
  }

  function onPointerMove(event: ReactPointerEvent<HTMLCanvasElement>) {
    const from = drawing.current
    if (!from) return
    const context = event.currentTarget.getContext('2d')
    if (!context) return
    // Every point the browser gathered since the last event, for a smooth line.
    const coalesced = event.nativeEvent.getCoalescedEvents?.() ?? []
    const events = coalesced.length > 0 ? coalesced : [event.nativeEvent]
    const box = event.currentTarget.getBoundingClientRect()
    let last = from
    for (const e of events) {
      const at = { x: e.clientX - box.left, y: e.clientY - box.top }
      context.beginPath()
      context.moveTo(last.x, last.y)
      context.lineTo(at.x, at.y)
      context.stroke()
      grow(at.x, at.y)
      last = at
    }
    drawing.current = last
  }

  function onPointerUp() {
    drawing.current = null
  }

  function clear() {
    const element = canvas.current
    const context = element?.getContext('2d')
    if (!element || !context) return
    context.save()
    context.setTransform(1, 0, 0, 1, 0, 0)
    context.clearRect(0, 0, element.width, element.height)
    context.restore()
    bounds.current = null
    setDrawn(false)
  }

  /** The drawing, cropped to its strokes with a little room, as a PNG; null when nothing is
   *  drawn. */
  async function toPng(): Promise<Blob | null> {
    const element = canvas.current
    const b = bounds.current
    if (!element || !b) return null
    const ratio = window.devicePixelRatio || 1
    const pad = 8 + SIGNATURE_STROKE
    const x = Math.max(0, Math.floor((b.x0 - pad) * ratio))
    const y = Math.max(0, Math.floor((b.y0 - pad) * ratio))
    const w = Math.min(element.width - x, Math.ceil((b.x1 - b.x0 + pad * 2) * ratio))
    const h = Math.min(element.height - y, Math.ceil((b.y1 - b.y0 + pad * 2) * ratio))
    const out = document.createElement('canvas')
    out.width = Math.max(1, w)
    out.height = Math.max(1, h)
    out.getContext('2d')?.drawImage(element, x, y, w, h, 0, 0, w, h)
    return new Promise((resolve) => out.toBlob(resolve, 'image/png'))
  }

  // A callback ref, so the component hands the canvas over without reading a ref in its render.
  const attach = useCallback((element: HTMLCanvasElement | null) => {
    canvas.current = element
  }, [])

  return {
    attach,
    drawn,
    clear,
    toPng,
    onPointerDown,
    onPointerMove,
    onPointerUp,
  }
}
