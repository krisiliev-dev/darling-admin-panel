import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { hoursSince, timeSinceBg, SLA_HOURS, SLA_WARN } from './time'

// Freeze "now" so Date.now()-based math is deterministic.
const NOW = new Date('2026-07-20T12:00:00.000Z')

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(NOW)
})
afterEach(() => {
  vi.useRealTimers()
})

const isoHoursAgo = (h: number) => new Date(NOW.getTime() - h * 3_600_000).toISOString()

describe('hoursSince', () => {
  it('returns 0 for the current instant', () => {
    expect(hoursSince(NOW.toISOString())).toBe(0)
  })

  it('computes fractional and whole hours elapsed', () => {
    expect(hoursSince(isoHoursAgo(1))).toBeCloseTo(1, 6)
    expect(hoursSince(isoHoursAgo(2.5))).toBeCloseTo(2.5, 6)
    expect(hoursSince(isoHoursAgo(48))).toBeCloseTo(48, 6)
  })

  it('returns negative for a future timestamp', () => {
    expect(hoursSince(isoHoursAgo(-3))).toBeCloseTo(-3, 6)
  })

  it('returns 0 for an unparseable date', () => {
    expect(hoursSince('not-a-date')).toBe(0)
    expect(hoursSince('')).toBe(0)
  })
})

describe('timeSinceBg', () => {
  it('says "току-що" under one hour', () => {
    expect(timeSinceBg(NOW.toISOString())).toBe('току-що')
    expect(timeSinceBg(isoHoursAgo(0.5))).toBe('току-що')
    expect(timeSinceBg(isoHoursAgo(0.999))).toBe('току-що')
  })

  it('says "преди Nч" from 1h up to just under 24h (floored)', () => {
    expect(timeSinceBg(isoHoursAgo(1))).toBe('преди 1ч')
    expect(timeSinceBg(isoHoursAgo(1.9))).toBe('преди 1ч')
    expect(timeSinceBg(isoHoursAgo(5))).toBe('преди 5ч')
    expect(timeSinceBg(isoHoursAgo(23.9))).toBe('преди 23ч')
  })

  it('says "преди Nд" at 24h and beyond (floored to days)', () => {
    expect(timeSinceBg(isoHoursAgo(24))).toBe('преди 1д')
    expect(timeSinceBg(isoHoursAgo(47))).toBe('преди 1д')
    expect(timeSinceBg(isoHoursAgo(48))).toBe('преди 2д')
    expect(timeSinceBg(isoHoursAgo(72))).toBe('преди 3д')
  })

  it('falls back to "току-що" for an invalid date (hoursSince → 0)', () => {
    expect(timeSinceBg('not-a-date')).toBe('току-що')
  })
})

describe('SLA constants', () => {
  it('has the expected values with warn before breach', () => {
    expect(SLA_HOURS).toBe(24)
    expect(SLA_WARN).toBe(18)
    expect(SLA_WARN).toBeLessThan(SLA_HOURS)
  })
})
