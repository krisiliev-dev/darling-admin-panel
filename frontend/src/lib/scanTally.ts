// The camera scanner's tracking brain, kept PURE so it is unit-testable without a camera.
//
// The worker points the phone at a table of products. Frames arrive ~5×/second, each a list
// of detected barcodes with bounding boxes. The rules, from the owner's brief:
//   • a code must stay in view ~1 second before it counts ("gives the worker time to get all
//     of the barcodes into view") — that is the DWELL; once satisfied the code LOCKS and the
//     overlay turns green;
//   • several physical items of the same code in one frame are separate bounding boxes, so
//     the per-frame instance count IS a quantity observation — we keep the maximum
//     simultaneously-seen count, never a sum across frames (summing would double-count the
//     same object as it jitters between frames);
//   • a locked code STAYS locked even when it leaves the view (the worker lowers the phone
//     to grab the next box) — only an explicit reset clears it;
//   • an unlocked code that vanishes longer than the tracker's gap restarts its dwell, so a
//     barcode that merely flashed past a moving camera never locks.
//
// The gap is PER TRACKER because the frame cadence depends on the decoder: the native
// BarcodeDetector returns in tens of ms (gap 600 is generous), while the WASM fallback on an
// older iPhone can take several hundred ms per frame — with a fixed 600ms gap the dwell
// would restart between two honest consecutive frames and nothing would ever lock.

export type Detection = { rawValue: string }

export type CodeTrack = {
  first: number          // when the current continuous presence began
  last: number           // last frame this code was seen
  maxInstances: number   // max simultaneous bounding boxes observed while dwelling
  locked: boolean
}

export type Tracker = { codes: Map<string, CodeTrack>; gapMs: number }

export const LOCK_MS = 1000
export const GAP_MS = 600

export function createTracker(gapMs: number = GAP_MS): Tracker {
  return { codes: new Map(), gapMs }
}

/** Feed one frame of detections. `now` is passed in (not read from the clock) for testability. */
export function trackFrame(t: Tracker, detections: Detection[], now: number): void {
  const perCode = new Map<string, number>()
  for (const d of detections) {
    const v = (d.rawValue || '').trim()
    if (v) perCode.set(v, (perCode.get(v) || 0) + 1)
  }
  for (const [code, n] of perCode) {
    const c = t.codes.get(code)
    if (!c) {
      t.codes.set(code, { first: now, last: now, maxInstances: n, locked: false })
      continue
    }
    if (!c.locked && now - c.last > t.gapMs) {
      // dropped out of view before locking → the dwell restarts, and so does the instance
      // observation (the old max belonged to the aborted attempt)
      c.first = now
      c.maxInstances = 0
    }
    c.last = now
    c.maxInstances = Math.max(c.maxInstances, n)
    if (!c.locked && now - c.first >= LOCK_MS) c.locked = true
  }
}

/** The committed-to-be tally: locked codes only, each at least 1. */
export function lockedTally(t: Tracker): Record<string, number> {
  const out: Record<string, number> = {}
  for (const [code, c] of t.codes) {
    if (c.locked) out[code] = Math.max(1, c.maxInstances)
  }
  return out
}

/** Codes currently dwelling (seen recently, not yet locked) — the amber overlay set. */
export function dwellingCodes(t: Tracker, now: number): string[] {
  const out: string[] = []
  for (const [code, c] of t.codes) {
    if (!c.locked && now - c.last <= t.gapMs) out.push(code)
  }
  return out
}

export function resetTracker(t: Tracker): void {
  t.codes.clear()
}

/** A detector's quadrilateral → an axis-aligned box in VIDEO pixel space. The WASM decoder
 *  reports four corner points on a possibly-downscaled frame; the overlay wants x/y/w/h at
 *  full video resolution, so the inverse scale is applied here. Pure, and unit-tested. */
export function quadToRect(
  quad: { topLeft: { x: number; y: number }; topRight: { x: number; y: number };
          bottomRight: { x: number; y: number }; bottomLeft: { x: number; y: number } },
  scale: number,
): { x: number; y: number; width: number; height: number } {
  const s = scale > 0 ? scale : 1
  const xs = [quad.topLeft.x, quad.topRight.x, quad.bottomRight.x, quad.bottomLeft.x].map((v) => v / s)
  const ys = [quad.topLeft.y, quad.topRight.y, quad.bottomRight.y, quad.bottomLeft.y].map((v) => v / s)
  const x = Math.min(...xs)
  const y = Math.min(...ys)
  return { x, y, width: Math.max(...xs) - x, height: Math.max(...ys) - y }
}
