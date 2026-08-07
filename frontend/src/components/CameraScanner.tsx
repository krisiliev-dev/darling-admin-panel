import { useCallback, useEffect, useRef, useState } from 'react'
import { createTracker, dwellingCodes, GAP_MS, lockedTally, quadToRect, resetTracker, trackFrame } from '../lib/scanTally'

// The phone camera AS the barcode scanner — EMBEDDED, not fullscreen. The first version
// covered the whole page, which the owner's field test exposed as the real bug: Добави
// "did nothing" because everything it did — the message, the rows going green, even the
// unknown-barcode dialog — rendered BEHIND the overlay (the dialog's z-index was below the
// scanner's). Embedding the camera above the delivery table means the page itself is the
// feedback: rows recolour in view, and the commit result also lands in a line inside this
// card. The owner chose the embed over a back button (2026-08-07); ✕ stops the camera.
//
// Detection is unchanged: a code dwells ~1s to lock (amber → green), same-code instances
// count by bounding box, counts are correctable before one atomic Добави. Two decoders
// behind one interface — native BarcodeDetector (Chrome/Android) or zxing-wasm (iPhone),
// lazy-loaded, frames ≤960px, with the wider per-tracker dwell gap slow decoding needs.

type Box = { x: number; y: number; width: number; height: number }
type Detected = { rawValue: string; boundingBox: Box }
type DetectorLike = { kind: 'native' | 'wasm'; detect(v: HTMLVideoElement): Promise<Detected[]> }

type NativeDetected = { rawValue: string; boundingBox: DOMRectReadOnly }
type BarcodeDetectorCtor = new (opts?: { formats?: string[] }) => { detect(source: CanvasImageSource): Promise<NativeDetected[]> }

const FORMATS = ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'itf', 'qr_code']
const DETECT_EVERY_MS = 200

/** An id for one tally, reused across retries so the server can refuse a second apply. */
function newCommitId(): string {
  const c = globalThis.crypto
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  return `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`
}
const WASM_MAX_WIDTH = 960

async function makeDetector(): Promise<DetectorLike | null> {
  const Ctor = (window as unknown as { BarcodeDetector?: BarcodeDetectorCtor }).BarcodeDetector
  if (Ctor) {
    const d = new Ctor({ formats: FORMATS })
    return {
      kind: 'native',
      detect: async (v) => (await d.detect(v)).map((r) => ({
        rawValue: r.rawValue,
        boundingBox: { x: r.boundingBox.x, y: r.boundingBox.y, width: r.boundingBox.width, height: r.boundingBox.height },
      })),
    }
  }
  try {
    const { readBarcodes } = await import('zxing-wasm/reader')
    const canvas = document.createElement('canvas')
    const ctx = canvas.getContext('2d', { willReadFrequently: true })
    if (!ctx) return null
    return {
      kind: 'wasm',
      detect: async (v) => {
        const vw = v.videoWidth, vh = v.videoHeight
        if (!vw || !vh) return []
        const scale = Math.min(1, WASM_MAX_WIDTH / vw)
        const w = Math.round(vw * scale), h = Math.round(vh * scale)
        if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h }
        ctx.drawImage(v, 0, 0, w, h)
        const img = ctx.getImageData(0, 0, w, h)
        // no `formats` filter on purpose: a mistyped format name would throw and read as
        // "camera broken"; reading all symbologies costs a little CPU and no correctness
        const results = await readBarcodes(img, { tryHarder: true, maxNumberOfSymbols: 32 })
        return results.filter((r) => (r.text || '').trim()).map((r) => ({
          rawValue: r.text,
          boundingBox: quadToRect(r.position, scale),
        }))
      },
    }
  } catch {
    return null
  }
}

export function CameraScanner({ onCommit, onClose }: {
  /** apply the tally; an optional returned string is shown inside the card as the result */
  onCommit: (counts: Record<string, number>, commitId?: string) => Promise<{ ok: boolean; message: string }>
  onClose: () => void
}) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const trackerRef = useRef(createTracker())
  const streamRef = useRef<MediaStream | null>(null)
  const detectingRef = useRef(false)
  const msgTimer = useRef<number | undefined>(undefined)
  const commitIdRef = useRef<string>('')

  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [locked, setLocked] = useState<Record<string, number>>({})
  const [dwelling, setDwelling] = useState<string[]>([])
  // worker corrections on top of the observed tally: a number overrides the count, null removes
  const [overrides, setOverrides] = useState<Record<string, number | null>>({})
  const [busy, setBusy] = useState(false)
  const [torchOn, setTorchOn] = useState(false)

  useEffect(() => {
    let interval: number | undefined
    let cancelled = false

    void (async () => {
      const detector = await makeDetector()
      if (cancelled) return
      if (!detector) {
        setErr('Този браузър не може да чете баркодове от камерата (нито вграден детектор, нито WASM декодерът се зареди). Четецът с кабел работи както досега.')
        return
      }
      // the WASM decoder can take several hundred ms per frame on an older phone — widen
      // the dwell gap so two honest consecutive frames never read as "left the view"
      trackerRef.current = createTracker(detector.kind === 'wasm' ? 1500 : GAP_MS)

      let stream: MediaStream
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: 'environment', width: { ideal: 1280 }, height: { ideal: 720 } },
          audio: false,
        })
      } catch {
        setErr('Няма достъп до камерата. Разреши камерата за този сайт (Настройки → Safari/Chrome → Камера) и опитай пак.')
        return
      }
      if (cancelled) { stream.getTracks().forEach((t) => t.stop()); return }
      streamRef.current = stream
      const video = videoRef.current
      if (!video) return
      video.srcObject = stream
      void video.play()

      interval = window.setInterval(() => {
        void (async () => {
          const v = videoRef.current
          if (!v || v.readyState < 2 || detectingRef.current) return
          detectingRef.current = true
          try {
            const dets = await detector.detect(v)
            const now = performance.now()
            trackFrame(trackerRef.current, dets, now)
            setLocked(lockedTally(trackerRef.current))
            setDwelling(dwellingCodes(trackerRef.current, now))
            drawOverlay(v, canvasRef.current, dets, lockedTally(trackerRef.current))
          } catch {
            // a single failed detect frame is noise; the next tick retries
          } finally {
            detectingRef.current = false
          }
        })()
      }, DETECT_EVERY_MS)
    })()

    return () => {
      cancelled = true
      if (interval) window.clearInterval(interval)
      window.clearTimeout(msgTimer.current)
      streamRef.current?.getTracks().forEach((t) => t.stop())
    }
  }, [])

  const toggleTorch = useCallback(() => {
    const track = streamRef.current?.getVideoTracks()[0]
    if (!track) return
    const caps = track.getCapabilities() as MediaTrackCapabilities & { torch?: boolean }
    if (!caps.torch) return
    const next = !torchOn
    track.applyConstraints({ advanced: [{ torch: next } as MediaTrackConstraintSet] })
      .then(() => setTorchOn(next))
      .catch(() => { /* no torch — leave the button inert */ })
  }, [torchOn])

  const effectiveCounts = useCallback((): Record<string, number> => {
    const out: Record<string, number> = {}
    for (const [code, n] of Object.entries(locked)) {
      const ov = overrides[code]
      if (ov === null) continue                    // removed by the worker
      out[code] = Math.max(1, ov ?? n)
    }
    return out
  }, [locked, overrides])

  const commit = async () => {
    const counts = effectiveCounts()
    if (Object.keys(counts).length === 0 || busy) return
    setBusy(true)
    // the SAME id for every retry of this tally, so a lost reply cannot count twice
    if (!commitIdRef.current) commitIdRef.current = newCommitId()
    try {
      const result = await onCommit(counts, commitIdRef.current)
      if (result.message) {
        setMsg(result.message)
        window.clearTimeout(msgTimer.current)
        msgTimer.current = window.setTimeout(() => setMsg(''), 5000)
      }
      // Only a SUCCESSFUL commit clears the tally. Resetting on failure threw away a
      // whole box of green chips every time the warehouse wifi blipped, and the worker
      // had to rescan from scratch — which, on a timeout the server had applied, booked
      // everything twice. Keep it and let them press Добави again.
      if (!result.ok) return
      commitIdRef.current = ''            // a new tally deserves a new id
      resetTracker(trackerRef.current)
      setLocked({}); setOverrides({}); setDwelling([])
    } finally {
      setBusy(false)
    }
  }

  const bump = (code: string, delta: number) => {
    setOverrides((o) => {
      const current = o[code] ?? locked[code] ?? 1
      if (current === null) return o
      return { ...o, [code]: Math.max(1, current + delta) }
    })
  }

  const chips = Object.entries(locked).filter(([code]) => overrides[code] !== null)
  const total = Object.values(effectiveCounts()).reduce((a, n) => a + n, 0)

  return (
    <div className="sheet" style={{ padding: '.6rem', display: 'grid', gap: '.55rem' }}>
      <div style={{ position: 'relative', borderRadius: 4, overflow: 'hidden', background: '#000', height: 'min(42vh, 380px)' }}>
        {/* eslint-disable-next-line jsx-a11y/media-has-caption -- live camera feed, no audio */}
        <video ref={videoRef} playsInline muted style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover' }} />
        <canvas ref={canvasRef} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }} />
        <button className="btn-paper" onClick={onClose} style={{ position: 'absolute', top: '.5rem', left: '.5rem' }}>✕ Затвори</button>
        <button className="btn-paper" onClick={toggleTorch} style={{ position: 'absolute', top: '.5rem', right: '.5rem' }}>
          {torchOn ? '🔦 Изкл.' : '🔦 Фенер'}
        </button>
        {dwelling.length > 0 && (
          <div className="font-type" style={{ position: 'absolute', bottom: '.5rem', left: '50%', transform: 'translateX(-50%)', color: '#e6a817', fontSize: '.78rem', background: 'rgba(0,0,0,.55)', padding: '.15rem .55rem', borderRadius: 3 }}>
            задръж… ({dwelling.length})
          </div>
        )}
        {err && (
          <div className="font-type" style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', padding: '1.2rem', color: '#fff', fontSize: '.85rem', textAlign: 'center', background: 'rgba(0,0,0,.75)' }}>
            {err}
          </div>
        )}
      </div>

      {msg && (
        <div role="status" className="font-type" style={{ fontSize: '.85rem', fontWeight: 700, color: msg.startsWith('⚠') ? 'var(--blocked, #b12f26)' : 'var(--ready, #2f7d4f)' }}>
          {msg}
        </div>
      )}

      {chips.length === 0 ? (
        <span className="font-type" style={{ fontSize: '.78rem', color: 'var(--pencil)' }}>
          Насочи камерата към баркодовете — след секунда се маркират в зелено.
        </span>
      ) : (
        <div style={{ display: 'grid', gap: '.35rem' }}>
          {chips.map(([code, n]) => {
            const count = Math.max(1, overrides[code] ?? n)
            return (
              <div key={code} className="font-type" style={{ display: 'flex', alignItems: 'center', gap: '.5rem', fontSize: '.85rem', border: '1px solid var(--line, #ddd)', borderRadius: 3, padding: '.3rem .5rem' }}>
                <span style={{ color: 'var(--ready, #2f7d4f)', fontWeight: 700 }}>✓</span>
                <span style={{ fontVariantNumeric: 'tabular-nums', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>{code}</span>
                <button className="btn-paper" aria-label={`минус 1 за ${code}`} onClick={() => bump(code, -1)} style={{ padding: '.1rem .55rem' }}>−</button>
                <b style={{ minWidth: '1.6rem', textAlign: 'center', fontVariantNumeric: 'tabular-nums' }}>{count}</b>
                <button className="btn-paper" aria-label={`плюс 1 за ${code}`} onClick={() => bump(code, 1)} style={{ padding: '.1rem .55rem' }}>+</button>
                <button className="btn-paper" aria-label={`премахни ${code}`} onClick={() => setOverrides((o) => ({ ...o, [code]: null }))} style={{ padding: '.1rem .45rem' }}>✕</button>
              </div>
            )
          })}
        </div>
      )}
      <button className="btn-paper btn-red" onClick={() => void commit()} disabled={busy || total === 0} style={{ fontSize: '.95rem', padding: '.55rem' }}>
        {busy ? 'записва…' : `Добави (${total})`}
      </button>
    </div>
  )
}

function drawOverlay(video: HTMLVideoElement, canvas: HTMLCanvasElement | null,
                     dets: Detected[], locked: Record<string, number>) {
  if (!canvas) return
  // canvas buffer in VIDEO pixel space; CSS scales both video and canvas identically
  // (both fill the container), so boxes land on the barcodes they belong to. object-fit:
  // cover crops symmetrically — acceptable drift at the edges, exact in the centre where
  // the worker aims.
  if (canvas.width !== video.videoWidth || canvas.height !== video.videoHeight) {
    canvas.width = video.videoWidth
    canvas.height = video.videoHeight
  }
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  ctx.clearRect(0, 0, canvas.width, canvas.height)
  for (const d of dets) {
    const isLocked = (d.rawValue || '').trim() in locked
    ctx.strokeStyle = isLocked ? '#2f7d4f' : '#e6a817'
    ctx.lineWidth = 4
    const b = d.boundingBox
    ctx.strokeRect(b.x, b.y, b.width, b.height)
    if (isLocked) {
      ctx.fillStyle = '#2f7d4f'
      ctx.font = 'bold 16px sans-serif'
      ctx.fillText('✓', b.x + 4, b.y - 6)
    }
  }
}
