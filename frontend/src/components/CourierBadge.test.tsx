import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { courierTone, CourierBadge } from './CourierBadge'

describe('courierTone', () => {
  it('ready → --ready (green), regardless of configured', () => {
    expect(courierTone({ ready: true, configured: false })).toBe('var(--ready)')
    expect(courierTone({ ready: true, configured: true })).toBe('var(--ready)')
  })

  it('configured but not ready → --pending', () => {
    expect(courierTone({ ready: false, configured: true })).toBe('var(--pending)')
  })

  it('neither ready nor configured → --pencil', () => {
    expect(courierTone({ ready: false, configured: false })).toBe('var(--pencil)')
  })
})

describe('CourierBadge label', () => {
  const label = (c: { ready: boolean; configured: boolean }, compact?: boolean) =>
    render(<CourierBadge c={c} compact={compact} />).container.textContent?.trim()

  it('ready → "готов" in both modes', () => {
    expect(label({ ready: true, configured: true })).toBe('● готов')
    expect(label({ ready: true, configured: true }, true)).toBe('● готов')
  })

  it('configured (not ready): full vs compact labels', () => {
    expect(label({ ready: false, configured: true })).toBe('● нужна настройка')
    expect(label({ ready: false, configured: true }, true)).toBe('● настройка')
  })

  it('unconfigured: full vs compact labels', () => {
    expect(label({ ready: false, configured: false })).toBe('● няма ключове')
    expect(label({ ready: false, configured: false }, true)).toBe('● няма ключ')
  })

  it('applies the tone colour as text color', () => {
    const span = render(<CourierBadge c={{ ready: true, configured: true }} />)
      .container.querySelector('span')!
    expect(span.style.color).toBe('var(--ready)')
  })
})
