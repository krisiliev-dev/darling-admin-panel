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
//   • an unlocked code that vanishes longer than GAP_MS restarts its dwell, so a barcode
//     that merely flashed past a moving camera never locks.

export type Detection = { rawValue: string }

export type CodeTrack = {
  first: number          // when the current continuous presence began
  last: number           // last frame this code was seen
  maxInstances: number   // max simultaneous bounding boxes observed while dwelling
  locked: boolean
}

export type Tracker = { codes: Map<string, CodeTrack> }

export const LOCK_MS = 1000
export const GAP_MS = 600

export function createTracker(): Tracker {
  return { codes: new Map() }
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
    if (!c.locked && now - c.last > GAP_MS) {
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
    if (!c.locked && now - c.last <= GAP_MS) out.push(code)
  }
  return out
}

export function resetTracker(t: Tracker): void {
  t.codes.clear()
}
