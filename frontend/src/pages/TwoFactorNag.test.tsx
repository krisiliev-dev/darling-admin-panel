import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const state = vi.hoisted(() => ({
  status: { enabled: false, recovery_remaining: 0, mandatory: true } as unknown,
  fail: false,
}))

vi.mock('../lib/api', () => ({
  ApiError: class extends Error {},
  api: {
    get: vi.fn(async () => {
      if (state.fail) throw new Error('backend down')
      return state.status
    }),
  },
}))

import { TwoFactorNag } from './TwoFactorNag'

const renderNag = () => render(<MemoryRouter><TwoFactorNag /></MemoryRouter>)

describe('2FA reminder strip', () => {
  beforeEach(() => {
    state.status = { enabled: false, recovery_remaining: 0, mandatory: true }
    state.fail = false
  })

  it('nags an un-enrolled user and links to Настройки', async () => {
    renderNag()
    expect(await screen.findByTestId('twofa-nag')).toBeInTheDocument()
    expect(screen.getByTestId('twofa-nag-cta')).toHaveAttribute('href', '/settings')
  })

  it('has NO dismiss control — enrolling is the only way to remove it', async () => {
    renderNag()
    await screen.findByTestId('twofa-nag')
    const nag = screen.getByTestId('twofa-nag')
    // no button inside the strip at all (the CTA is a link, not a dismiss)
    expect(nag.querySelectorAll('button').length).toBe(0)
    expect(screen.queryByLabelText(/затвори|скрий/i)).not.toBeInTheDocument()
  })

  it('disappears once 2FA is enabled', async () => {
    state.status = { enabled: true, recovery_remaining: 10, mandatory: true }
    renderNag()
    await waitFor(() => expect(screen.queryByTestId('twofa-nag')).not.toBeInTheDocument())
  })

  it('stays silent when the status call fails — a hiccup must not paint a red strip', async () => {
    state.fail = true
    renderNag()
    await waitFor(() => expect(screen.queryByTestId('twofa-nag')).not.toBeInTheDocument())
  })
})
