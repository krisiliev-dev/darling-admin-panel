import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const state = vi.hoisted(() => ({
  status: { enabled: false, recovery_remaining: 0, mandatory: true },
  posts: [] as [string, unknown][],
}))

vi.mock('../lib/api', () => ({
  ApiError: class extends Error {},
  api: {
    get: vi.fn(async () => state.status),
    post: vi.fn(async (p: string, b?: unknown) => {
      state.posts.push([p, b])
      if (p.endsWith('/setup')) {
        return { secret: 'JBSWY3DPEHPK3PXP', otpauth_uri: 'otpauth://totp/x',
                 qr_svg: '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0h1v1H0z"/></svg>' }
      }
      if (p.endsWith('/verify') || p.endsWith('/regenerate')) {
        return { recovery_codes: ['aaaa-1111', 'bbbb-2222'] }
      }
      return {}
    }),
  },
}))

import { TwoFactorPanel } from './TwoFactorPanel'

describe('Сигурност — 2FA enrollment', () => {
  beforeEach(() => {
    state.status = { enabled: false, recovery_remaining: 0, mandatory: true }
    state.posts = []
  })

  it('offers enrollment when 2FA is off', async () => {
    render(<TwoFactorPanel />)
    expect(await screen.findByTestId('totp-begin')).toBeInTheDocument()
    expect(screen.getByText('изключено')).toBeInTheDocument()
  })

  it('setup → QR is rendered as an IMAGE (not injected markup) → verify shows the codes once', async () => {
    render(<TwoFactorPanel />)
    fireEvent.click(await screen.findByTestId('totp-begin'))
    const qr = await screen.findByTestId('totp-qr') as HTMLImageElement
    // the SVG must arrive via a data: URI on an <img>; markup injection would leave no src
    expect(qr.tagName).toBe('IMG')
    expect(qr.src.startsWith('data:image/svg+xml')).toBe(true)
    expect(screen.getByText('JBSWY3DPEHPK3PXP')).toBeInTheDocument()   // manual-entry fallback

    fireEvent.change(screen.getByTestId('totp-code'), { target: { value: '123456' } })
    fireEvent.click(screen.getByTestId('totp-confirm'))
    await waitFor(() => expect(screen.getByTestId('recovery-codes')).toBeInTheDocument())
    expect(screen.getByText('aaaa-1111')).toBeInTheDocument()
    expect(state.posts.map(([p]) => p)).toEqual(['/api/auth/2fa/setup', '/api/auth/2fa/verify'])
    expect(state.posts[1][1]).toEqual({ code: '123456' })
  })

  it('shows status and lets an enrolled user regenerate codes', async () => {
    state.status = { enabled: true, recovery_remaining: 7, mandatory: true }
    render(<TwoFactorPanel />)
    expect(await screen.findByText('✓ включено')).toBeInTheDocument()
    expect(screen.getByText('7')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Нови кодове за възстановяване'))
    await waitFor(() => expect(screen.getByTestId('recovery-codes')).toBeInTheDocument())
    expect(state.posts[0][0]).toBe('/api/auth/2fa/recovery/regenerate')
  })

  it('recovery codes are dismissed only by an explicit "записах ги"', async () => {
    state.status = { enabled: true, recovery_remaining: 10, mandatory: true }
    render(<TwoFactorPanel />)
    fireEvent.click(await screen.findByText('Нови кодове за възстановяване'))
    await screen.findByTestId('recovery-codes')
    fireEvent.click(screen.getByTestId('codes-saved'))
    await waitFor(() => expect(screen.queryByTestId('recovery-codes')).not.toBeInTheDocument())
  })
})
