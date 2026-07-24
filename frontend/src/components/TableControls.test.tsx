import { describe, it, expect } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { usePager } from './TableControls'

const range = (n: number) => Array.from({ length: n }, (_, i) => i)

describe('usePager', () => {
  it('slices the first page and reports from/to/total', () => {
    const { result } = renderHook(() => usePager(range(60), 25))
    expect(result.current.slice).toEqual(range(25))
    expect(result.current.page).toBe(0)
    expect(result.current.pages).toBe(3)
    expect(result.current.from).toBe(1)
    expect(result.current.to).toBe(25)
    expect(result.current.total).toBe(60)
  })

  it('slices a middle and the short final page', () => {
    const { result } = renderHook(() => usePager(range(60), 25))
    act(() => result.current.setPage(1))
    expect(result.current.slice).toEqual(range(60).slice(25, 50))
    expect(result.current.from).toBe(26)
    expect(result.current.to).toBe(50)

    act(() => result.current.setPage(2))
    expect(result.current.slice).toEqual(range(60).slice(50, 60))
    expect(result.current.from).toBe(51)
    expect(result.current.to).toBe(60)
  })

  it('reports zeroed from/to and a single empty page for an empty list', () => {
    const { result } = renderHook(() => usePager<number>([], 25))
    expect(result.current.slice).toEqual([])
    expect(result.current.pages).toBe(1)
    expect(result.current.from).toBe(0)
    expect(result.current.to).toBe(0)
    expect(result.current.total).toBe(0)
  })

  it('clamps the page when the list shrinks beneath the current page', () => {
    const { result, rerender } = renderHook(({ items }) => usePager(items, 25), {
      initialProps: { items: range(60) },
    })
    act(() => result.current.setPage(2)) // last page of 3
    expect(result.current.page).toBe(2)

    // list shrinks to a single page — clamped page should fall back to 0
    rerender({ items: range(10) })
    expect(result.current.pages).toBe(1)
    expect(result.current.page).toBe(0)
    expect(result.current.slice).toEqual(range(10))
    expect(result.current.from).toBe(1)
    expect(result.current.to).toBe(10)
  })

  it('defaults to a page size of 25', () => {
    const { result } = renderHook(() => usePager(range(30)))
    expect(result.current.slice).toHaveLength(25)
    expect(result.current.pages).toBe(2)
  })
})
