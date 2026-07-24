import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { api, ApiError } from './api'

// The error-detail handling lives in the internal `request`; exercise it via api.get.
// Mock fetch to return controlled error bodies.
function mockFetchOnce(opts: {
  ok?: boolean
  status?: number
  statusText?: string
  json?: () => unknown
}) {
  const res = {
    ok: opts.ok ?? false,
    status: opts.status ?? 400,
    statusText: opts.statusText ?? 'Bad Request',
    json: opts.json ?? (() => { throw new Error('no json') }),
  } as unknown as Response
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(res))
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('request error-detail handling', () => {
  it('passes a string detail through verbatim', async () => {
    mockFetchOnce({ status: 409, json: () => ({ detail: 'Вече съществува' }) })
    await expect(api.get('/api/x')).rejects.toMatchObject({
      status: 409,
      message: 'Вече съществува',
    })
  })

  it('joins a FastAPI 422 array of {loc, msg} into an actionable string', async () => {
    mockFetchOnce({
      status: 422,
      json: () => ({
        detail: [
          { loc: ['body', 'email'], msg: 'value is not a valid email address' },
          { loc: ['body', 'price'], msg: 'ensure this value is greater than 0' },
        ],
      }),
    })
    await expect(api.get('/api/x')).rejects.toMatchObject({
      status: 422,
      message: 'email: value is not a valid email address; price: ensure this value is greater than 0',
    })
  })

  it('drops only the first loc segment (body/query) and keeps nested paths', async () => {
    mockFetchOnce({
      status: 422,
      json: () => ({ detail: [{ loc: ['body', 'items', 0, 'qty'], msg: 'required' }] }),
    })
    await expect(api.get('/api/x')).rejects.toMatchObject({
      message: 'items.0.qty: required',
    })
  })

  it('falls back to "Невалидни данни" for an empty/unusable array', async () => {
    mockFetchOnce({ status: 422, json: () => ({ detail: [] }) })
    await expect(api.get('/api/x')).rejects.toMatchObject({
      message: 'Невалидни данни',
    })
  })

  it('uses "Грешка" for a non-string, non-array detail (e.g. object)', async () => {
    mockFetchOnce({ status: 500, json: () => ({ detail: { unexpected: true } }) })
    await expect(api.get('/api/x')).rejects.toMatchObject({
      status: 500,
      message: 'Грешка',
    })
  })

  it('falls back to statusText when the error body is not JSON', async () => {
    mockFetchOnce({
      status: 502,
      statusText: 'Bad Gateway',
      json: () => { throw new Error('not json') },
    })
    await expect(api.get('/api/x')).rejects.toMatchObject({
      status: 502,
      message: 'Bad Gateway',
    })
  })

  it('throws an ApiError instance', async () => {
    mockFetchOnce({ status: 400, json: () => ({ detail: 'boom' }) })
    await expect(api.get('/api/x')).rejects.toBeInstanceOf(ApiError)
  })
})

describe('request success handling', () => {
  it('returns parsed JSON on ok', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true, status: 200, json: () => ({ hello: 'world' }),
    } as unknown as Response))
    await expect(api.get<{ hello: string }>('/api/x')).resolves.toEqual({ hello: 'world' })
  })

  it('returns undefined for 204 No Content', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true, status: 204, json: () => { throw new Error('no body') },
    } as unknown as Response))
    await expect(api.get('/api/x')).resolves.toBeUndefined()
  })
})
