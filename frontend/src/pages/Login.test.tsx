import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

// 2FA login step. The claim under test: an enrolled account is asked for a code and is NOT
// logged in until it supplies one — and a burned/expired ticket returns it to the password step
// rather than stranding it on a form that can never succeed.
const state = vi.hoisted(() => ({
  pending: null as null | { twofa_required: true; ticket: string },
  twofaCalls: [] as unknown[],
  twofaError: null as null | Error,
}))

vi.mock('../lib/auth', () => ({
  useAuth: () => ({
    needsSetup: false,
    login: vi.fn(async () => state.pending),
    barsyLogin: vi.fn(async () => state.pending),
    twofaLogin: vi.fn(async (ticket: string, code: string, recovery?: string) => {
      state.twofaCalls.push({ ticket, code, recovery })
      if (state.twofaError) throw state.twofaError
    }),
    setupOwner: vi.fn(),
  }),
}))

import { Login } from './Login'

const signIn = async () => {
  fireEvent.change(screen.getByLabelText('потребител или имейл'), { target: { value: 'kris@x.bg' } })
  fireEvent.change(document.querySelector('input[type=password]')!, { target: { value: 'pw' } })
  fireEvent.click(screen.getByText('Влез'))
}

describe('Login — 2FA step', () => {
  beforeEach(() => {
    state.pending = null; state.twofaCalls = []; state.twofaError = null
  })

  it('un-enrolled account never sees the code step', async () => {
    render(<Login />)
    await signIn()
    await waitFor(() => expect(screen.queryByTestId('twofa-code')).not.toBeInTheDocument())
  })

  it('enrolled account is asked for a code and submits it with the ticket', async () => {
    state.pending = { twofa_required: true, ticket: 'TICKET-1' }
    render(<Login />)
    await signIn()
    const field = await screen.findByTestId('twofa-code')
    expect(screen.getByText('Втора стъпка')).toBeInTheDocument()
    fireEvent.change(field, { target: { value: '123456' } })
    fireEvent.click(screen.getByTestId('twofa-submit'))
    await waitFor(() => expect(state.twofaCalls.length).toBe(1))
    expect(state.twofaCalls[0]).toEqual({ ticket: 'TICKET-1', code: '123456', recovery: '' })
  })

  it('the recovery link swaps which field is sent', async () => {
    state.pending = { twofa_required: true, ticket: 'TICKET-2' }
    render(<Login />)
    await signIn()
    await screen.findByTestId('twofa-code')
    fireEvent.click(screen.getByTestId('use-recovery'))
    fireEvent.change(screen.getByTestId('twofa-code'), { target: { value: 'abcd-1234' } })
    fireEvent.click(screen.getByTestId('twofa-submit'))
    await waitFor(() => expect(state.twofaCalls.length).toBe(1))
    expect(state.twofaCalls[0]).toEqual({ ticket: 'TICKET-2', code: '', recovery: 'abcd-1234' })
  })

  it('an expired ticket returns to the password step instead of a dead end', async () => {
    state.pending = { twofa_required: true, ticket: 'OLD' }
    state.twofaError = new Error('Изтекла или невалидна заявка за вход. Влезте отново.')
    render(<Login />)
    await signIn()
    fireEvent.change(await screen.findByTestId('twofa-code'), { target: { value: '000000' } })
    fireEvent.click(screen.getByTestId('twofa-submit'))
    await waitFor(() => expect(screen.queryByTestId('twofa-code')).not.toBeInTheDocument())
    expect(screen.getByText(/Изтекла/)).toBeInTheDocument()      // told why, back on the password form
  })

  it('a wrong code keeps them on the code step to retry', async () => {
    state.pending = { twofa_required: true, ticket: 'T' }
    state.twofaError = new Error('Грешен код.')
    render(<Login />)
    await signIn()
    fireEvent.change(await screen.findByTestId('twofa-code'), { target: { value: '111111' } })
    fireEvent.click(screen.getByTestId('twofa-submit'))
    await waitFor(() => expect(screen.getByText('Грешен код.')).toBeInTheDocument())
    expect(screen.getByTestId('twofa-code')).toBeInTheDocument()
  })
})
