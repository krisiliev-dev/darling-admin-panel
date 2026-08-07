import { describe, expect, it } from 'vitest'
import { createTracker, dwellingCodes, GAP_MS, LOCK_MS, lockedTally, quadToRect, resetTracker, trackFrame, type Tracker } from './scanTally'

const d = (v: string) => ({ rawValue: v })

// The camera delivers detection frames every ~200ms. The tracker's continuity rule (a gap
// over GAP_MS restarts the dwell) is part of the design, so these tests feed frames at the
// REAL cadence rather than teleporting between two timestamps — the first version of this
// file did exactly that and "tested" a camera that skips 999ms between frames.
const STEP = 200

function feed(t: Tracker, codes: string[], from: number, to: number) {
  for (let now = from; now <= to; now += STEP) trackFrame(t, codes.map(d), now)
}

describe('scanTally', () => {
  it('locks a code only after a full dwell of continuous presence', () => {
    const t = createTracker()
    feed(t, ['111'], 0, LOCK_MS - STEP)
    expect(lockedTally(t)).toEqual({})                    // one frame short of the dwell
    expect(dwellingCodes(t, LOCK_MS - STEP)).toEqual(['111'])
    trackFrame(t, [d('111')], LOCK_MS)
    expect(lockedTally(t)).toEqual({ '111': 1 })
  })

  it('a flash-past never locks — a gap over GAP_MS restarts the dwell', () => {
    const t = createTracker()
    trackFrame(t, [d('111')], 0)                          // seen once, then the camera moves
    const back = GAP_MS + 400                             // reappears after too long a gap
    trackFrame(t, [d('111')], back)
    feed(t, ['111'], back + STEP, back + LOCK_MS - STEP)
    expect(lockedTally(t)).toEqual({})                    // the old sighting did not count
    trackFrame(t, [d('111')], back + LOCK_MS)
    expect(lockedTally(t)).toEqual({ '111': 1 })          // a fresh full dwell does
  })

  it('counts simultaneous instances as quantity, taking the max across frames, never a sum', () => {
    const t = createTracker()
    trackFrame(t, [d('111'), d('111'), d('111')], 0)      // three identical mugs in view
    trackFrame(t, [d('111'), d('111')], STEP)             // one drifted out of frame
    feed(t, ['111', '111', '111'], STEP * 2, LOCK_MS)
    expect(lockedTally(t)).toEqual({ '111': 3 })          // max seen at once — never 3+2+3…
  })

  it('a gap before locking also resets the instance observation', () => {
    const t = createTracker()
    trackFrame(t, [d('111'), d('111'), d('111')], 0)      // aborted attempt saw 3
    const back = GAP_MS + 400
    feed(t, ['111'], back, back + LOCK_MS)                // the fresh dwell only ever sees 1
    expect(lockedTally(t)).toEqual({ '111': 1 })          // the stale 3 must not survive
  })

  it('locked codes stay locked when the camera moves away', () => {
    const t = createTracker()
    feed(t, ['111'], 0, LOCK_MS)
    expect(lockedTally(t)).toEqual({ '111': 1 })
    trackFrame(t, [d('222')], LOCK_MS + 5000)             // long after 111 left the view
    expect(lockedTally(t)).toEqual({ '111': 1 })
    expect(dwellingCodes(t, LOCK_MS + 5000)).toEqual(['222'])
  })

  it('tracks several codes independently in the same frames', () => {
    const t = createTracker()
    trackFrame(t, [d('111'), d('222')], 0)                // 222 appears once, then vanishes
    feed(t, ['111'], STEP, LOCK_MS)
    expect(lockedTally(t)).toEqual({ '111': 1 })
    const back = LOCK_MS + GAP_MS + 400
    feed(t, ['222'], back, back + LOCK_MS)                // 222 returns and dwells properly
    expect(lockedTally(t)).toEqual({ '111': 1, '222': 1 })
  })

  it('ignores blank raw values and resets cleanly', () => {
    const t = createTracker()
    trackFrame(t, [d(''), d('  ')], 0)
    expect(lockedTally(t)).toEqual({})
    feed(t, ['111'], 0, LOCK_MS)
    expect(lockedTally(t)).toEqual({ '111': 1 })
    resetTracker(t)
    expect(lockedTally(t)).toEqual({})
  })
})

describe('scanTally on a slow decoder (the WASM/iPhone path)', () => {
  it('a widened per-tracker gap keeps slow honest frames dwelling instead of restarting', () => {
    // WASM decode on an older phone can take ~700ms/frame — over the native GAP_MS, so a
    // default tracker would restart the dwell every frame and never lock. The wasm tracker
    // is created with a wider gap and the same frames lock normally.
    const strict = createTracker()                        // native gap (600)
    const wide = createTracker(1500)                      // the wasm tracker
    for (const t of [strict, wide]) {
      trackFrame(t, [d('111')], 0)
      trackFrame(t, [d('111')], 700)
      trackFrame(t, [d('111')], 1400)
    }
    expect(lockedTally(strict)).toEqual({})               // kept restarting — by design
    expect(lockedTally(wide)).toEqual({ '111': 1 })
  })
})

describe('quadToRect', () => {
  it('boxes a quadrilateral and rescales decoder coordinates back to video pixels', () => {
    // decoded on a half-scale frame → the overlay box must be twice as large
    const r = quadToRect({
      topLeft: { x: 10, y: 20 }, topRight: { x: 50, y: 22 },
      bottomRight: { x: 52, y: 40 }, bottomLeft: { x: 12, y: 38 },
    }, 0.5)
    expect(r).toEqual({ x: 20, y: 40, width: (52 - 10) * 2, height: (40 - 20) * 2 })
  })

  it('a zero scale falls back to 1 instead of dividing by zero', () => {
    const q = { topLeft: { x: 1, y: 1 }, topRight: { x: 3, y: 1 },
                bottomRight: { x: 3, y: 2 }, bottomLeft: { x: 1, y: 2 } }
    expect(quadToRect(q, 0)).toEqual({ x: 1, y: 1, width: 2, height: 1 })
  })
})
