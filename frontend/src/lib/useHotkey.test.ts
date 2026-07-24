import { describe, it, expect } from 'vitest'
import { isTyping } from './useHotkey'

// Build a KeyboardEvent-like object with a chosen target. isTyping only reads
// target.tagName and target.isContentEditable, so a real DOM element is cleanest.
function keyEventOn(target: EventTarget | null): KeyboardEvent {
  const e = new KeyboardEvent('keydown', { key: '/' })
  Object.defineProperty(e, 'target', { value: target, configurable: true })
  return e
}

describe('isTyping', () => {
  it('is true for INPUT, TEXTAREA and SELECT', () => {
    expect(isTyping(keyEventOn(document.createElement('input')))).toBe(true)
    expect(isTyping(keyEventOn(document.createElement('textarea')))).toBe(true)
    expect(isTyping(keyEventOn(document.createElement('select')))).toBe(true)
  })

  it('is true for a contentEditable element', () => {
    const div = document.createElement('div')
    div.contentEditable = 'true'
    // jsdom does not always reflect isContentEditable from the attribute; force it.
    Object.defineProperty(div, 'isContentEditable', { value: true, configurable: true })
    expect(isTyping(keyEventOn(div))).toBe(true)
  })

  it('is false for non-editable elements (button, div, a)', () => {
    expect(isTyping(keyEventOn(document.createElement('button')))).toBe(false)
    expect(isTyping(keyEventOn(document.createElement('div')))).toBe(false)
    expect(isTyping(keyEventOn(document.createElement('a')))).toBe(false)
  })

  it('is false when there is no target', () => {
    expect(isTyping(keyEventOn(null))).toBe(false)
  })
})
