import { describe, it, expect } from 'vitest'
import { parseSize } from './Intake'

// The bug this locks down (owner 2026-08-04): „Bursev Меламинова овална чиния 17см" had its 17 in
// Височина. The draft attribute reads „17 см (дължина)" — the source said length and the editor
// filed it as height, because the parser took the numbers in order.
describe('parseSize', () => {
  it('believes an explicit label instead of position', () => {
    const p = parseSize('17 см (дължина)')
    expect(p.h).toBe('')          // the actual regression
    expect(p.w).toBe('17')
    expect(p.unplaced).toBe('')
  })

  it('reads a diameter, written as a word or as Ø', () => {
    expect(parseSize('диаметър 22 см').dia).toBe('22')
    expect(parseSize('Ø22 см').dia).toBe('22')
    expect(parseSize('22 см диаметър').dia).toBe('22')
  })

  it('reads height and width when they are labelled', () => {
    const p = parseSize('височина 9 см; широчина 12 см')
    expect(p.h).toBe('9')
    expect(p.w).toBe('12')
  })

  it('maps a triple positionally, because there the order IS the label', () => {
    const p = parseSize('15 x 20,5 x 4,5 см')
    expect([p.h, p.w, p.d]).toEqual(['15', '20,5', '4,5'])
    expect(p.unplaced).toBe('')
  })

  it('accepts the Cyrillic х and the × sign as separators', () => {
    expect(parseSize('10,5х2х33 см').d).toBe('33')
    expect(parseSize('28,5 × 42 × 38 см').w).toBe('42')
  })

  it('normalises a dot decimal to the BG comma', () => {
    expect(parseSize('15 x 20.5 x 4.5 cm').w).toBe('20,5')
  })

  it('REFUSES to place a lone unlabelled number', () => {
    // this is the whole point: a plate's single dimension is not a height, and a guess
    // rendered into a box reads as a confirmed fact
    const p = parseSize('17 см')
    expect([p.h, p.w, p.d, p.dia]).toEqual(['', '', '', ''])
    expect(p.unplaced).toBe('17 см')
  })

  it('is empty for empty input, and never throws on junk', () => {
    expect(parseSize('').unplaced).toBe('')
    expect(parseSize('   ').unplaced).toBe('')
    expect(parseSize('без размер').unplaced).toBe('без размер')
  })

  it('takes the labelled part of a messy multi-source string', () => {
    // a real draft value from the box
    const p = parseSize('20 cm дължина; 15 x 20.5 x 4.5 cm (според Depo61)')
    expect(p.w).toBe('20')
  })
})
