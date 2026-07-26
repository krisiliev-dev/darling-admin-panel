import { describe, it, expect } from 'vitest'

import { labelHtml } from './label'

describe('labelHtml', () => {
  it('includes the product name and formatted € price', () => {
    const html = labelHtml({ name: 'Керамична купа', price: 12.5, barcode: '3800130123456' })
    expect(html).toContain('Керамична купа')
    expect(html).toContain('12,50 €')          // bg-BG formatting, two decimals
    expect(html).toContain('size: 58mm auto')  // sized for the thermal printer
  })

  it('escapes HTML in the name', () => {
    const html = labelHtml({ name: 'A & <b> "x"', code: 'C1' })
    expect(html).toContain('A &amp; &lt;b&gt; &quot;x&quot;')
    expect(html).not.toContain('<b>')
  })

  it('omits the price block when there is no price', () => {
    const html = labelHtml({ name: 'Без цена', code: 'C2' })
    expect(html).not.toContain('€')
  })

  it('falls back to a "no barcode" note when neither barcode nor code is present', () => {
    const html = labelHtml({ name: 'Нищо' })
    expect(html).toContain('без баркод/код')
  })
})
