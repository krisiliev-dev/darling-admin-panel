import { useCallback, useEffect, useRef, useState } from 'react'
import { createTracker, dwellingCodes, lockedTally, resetTracker, trackFrame } from '../lib/scanTally'

// The phone camera AS the barcode scanner (Доставки). The worker opens a box, points the
// phone, and every barcode in view is detected at once — several items of the same code count
// as separate instances (separate bounding boxes). A code must dwell ~1s before it locks
// (amber → green overlay), so a barcode flashing past a moving camera never books anything.
// Locked codes gather into a tally the worker can correct (a closed кашон of 12 is scanned
// once and typed, not unpacked), then one Добави commits the whole tally atomically to
// /scan-batch. The tracking rules live in lib/scanTally.ts, pure and unit-tested.
//
// Detection uses the native BarcodeDetector API (Chrome/Android — the warehouse phone).
// Where it is missing (iOS Safari, Firefox) the panel says so honestly and the HID-scanner
// path keeps working — this component is an upgrade, not a replacement.

type DetectedBarcode = { rawValue: string; boundingBox: DOMRectReadOnly }
type BarcodeDetectorLike = { detect(source: CanvasImageSource): Promise<DetectedBarcode[]> }
type BarcodeDetectorCtor = new (opts?: { formats?: string[] }) => BarcodeDetectorLike

const FORMATS = ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'itf', 'qr_code']
const DETECT_EVERY_MS = 200

function getDetectorCtor(): BarcodeDetectorCtor | undefined {
  return (window as unknown as { BarcodeDetector?: BarcodeDetectorCtor }).BarcodeDetector
}

export function CameraScanner({ onCommit, onClose }: {
  onCommit: (counts: Record<string, number>) => Promise<void>
  onClose: () => void
}) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const trackerRef = useRef(createTracker())
  const streamRef = useRef<MediaStream | null>(null)
  const detectingRef = useRef(false)

  const [err, setErr] = useState('')
  const [locked, setLocked] = useState<Record<string, number>>({})
  const [dwelling, setDwelling] = useState<string[]>([])
  // worker corrections on top of the observed tally: a number overrides the count, null removes
  const [overrides, setOverrides] = useState<Record<string, number | null>>({})
  const [busy, setBusy] = useState(false)
  const [torchOn, setTorchOn] = useState(false)

  useEffect(() => {
    const Ctor = getDetectorCtor()
    if (!Ctor) {
      setErr('Този браузър няма вграден баркод детектор. Отвори страницата с Chrome на Android — или продължи с четеца, той работи както досега.')
      return
    }
    let interval: number | undefined
    let cancelled = false
    const detector = new Ctor({ formats: FORMATS })

    navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'environment', width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false,
    }).then((stream) => {
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
    }).catch(() => {
      setErr('Няма достъп до камерата. Разреши камерата за този сайт и опитай пак.')
    })

    return () => {
      cancelled = true
      if (interval) window.clearInterval(interval)
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
    try {
      await onCommit(counts)
      // stay open — the worker continues with the next box; the tally starts clean
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
    <div style={{ position: 'fixed', inset: 0, zIndex: 60, background: '#000', display: 'grid', gridTemplateRows: '1fr auto' }}>
      <div style={{ position: 'relative', overflow: 'hidden' }}>
        {/* eslint-disable-next-line jsx-a11y/media-has-caption -- live camera feed, no audio */}
        <video ref={videoRef} playsInline muted style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover' }} />
        <canvas ref={canvasRef} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }} />
        <button className="btn-paper" onClick={onClose} style={{ position: 'absolute', top: '.7rem', left: '.7rem' }}>✕ Затвори</button>
        <button className="btn-paper" onClick={toggleTorch} style={{ position: 'absolute', top: '.7rem', right: '.7rem' }}>
          {torchOn ? '🔦 Изкл.' : '🔦 Фенер'}
        </button>
        {dwelling.length > 0 && (
          <div className="font-type" style={{ position: 'absolute', bottom: '.6rem', left: '50%', transform: 'translateX(-50%)', color: '#e6a817', fontSize: '.8rem', background: 'rgba(0,0,0,.55)', padding: '.2rem .6rem', borderRadius: 3 }}>
            задръж… ({dwelling.length})
          </div>
        )}
        {err && (
          <div className="font-type" style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', padding: '2rem', color: '#fff', fontSize: '.9rem', textAlign: 'center', background: 'rgba(0,0,0,.75)' }}>
            {err}
          </div>
        )}
      </div>

      <div style={{ background: 'var(--paper, #fff)', padding: '.7rem .8rem', display: 'grid', gap: '.55rem', maxHeight: '45vh', overflowY: 'auto' }}>
        {chips.length === 0 ? (
          <span className="font-type" style={{ fontSize: '.8rem', color: 'var(--pencil)' }}>
            Насочи камерата към баркодовете — след секунда се маркират в зелено.
          </span>
        ) : (
          <div style={{ display: 'grid', gap: '.35rem' }}>
            {chips.map(([code, n]) => {
              const count = Math.max(1, overrides[code] ?? n)
              return (
                <div key={code} className="font-type" style={{ display: 'flex', alignItems: 'center', gap: '.5rem', fontSize: '.85rem', border: '1px solid var(--line, #ddd)', borderRadius: 3, padding: '.3rem .5rem' }}>
                  <span style={{ color: 'var(--ready, #2f7d4f)', fontWeight: 700 }}>✓</span>
                  <span style={{ fontVariantNumeric: 'tabular-nums', flex: 1 }}>{code}</span>
                  <button className="btn-paper" aria-label={`минус 1 за ${code}`} onClick={() => bump(code, -1)} style={{ padding: '.1rem .55rem' }}>−</button>
                  <b style={{ minWidth: '1.6rem', textAlign: 'center', fontVariantNumeric: 'tabular-nums' }}>{count}</b>
                  <button className="btn-paper" aria-label={`плюс 1 за ${code}`} onClick={() => bump(code, 1)} style={{ padding: '.1rem .55rem' }}>+</button>
                  <button className="btn-paper" aria-label={`премахни ${code}`} onClick={() => setOverrides((o) => ({ ...o, [code]: null }))} style={{ padding: '.1rem .45rem' }}>✕</button>
                </div>
              )
            })}
          </div>
        )}
        <button className="btn-paper btn-red" onClick={() => void commit()} disabled={busy || total === 0} style={{ fontSize: '1rem', padding: '.6rem' }}>
          {busy ? 'записва…' : `Добави в доставката (${total})`}
        </button>
      </div>
    </div>
  )
}

function drawOverlay(video: HTMLVideoElement, canvas: HTMLCanvasElement | null,
                     dets: DetectedBarcode[], locked: Record<string, number>) {
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
