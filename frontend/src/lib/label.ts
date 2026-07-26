import JsBarcode from 'jsbarcode'

// In-store price/barcode label for a 58 mm thermal printer (HPRT LPQ58). We open a small window with
// a self-contained 58 mm label — product name, a scannable barcode, and the price — and trigger the
// OS print dialog (option 1: the driver + @page sizing, most reliable cross-browser). The barcode
// encodes the product's own barcode when it has one; otherwise our internal code, so imported items
// without a manufacturer barcode still get a scannable label the till/Barsy recognises.
export interface LabelProduct {
  name: string
  price?: number | null
  barcode?: string
  code?: string
}

function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] as string))
}

// Render the barcode to a standalone SVG string. EAN-13 for a 13-digit barcode, else CODE128 (which
// encodes any text — our alnum codes included). Returns '' if the value can't be encoded.
function barcodeSvg(value: string): string {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
  const fmt = /^\d{13}$/.test(value) ? 'EAN13' : 'CODE128'
  try {
    JsBarcode(svg, value, { format: fmt, width: 2, height: 42, fontSize: 12, margin: 4, displayValue: true })
  } catch {
    return ''
  }
  return new XMLSerializer().serializeToString(svg)
}

// Pure: the full self-contained 58 mm label document for a product (barcode of barcode||code, name,
// price). Split out from printLabel so it's unit-testable without a real window.
export function labelHtml(p: LabelProduct): string {
  const value = ((p.barcode || '').trim()) || (p.code || '').trim()
  const svg = value ? barcodeSvg(value) : ''
  const price = p.price != null
    ? p.price.toLocaleString('bg-BG', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' €'
    : ''
  return (
    '<!doctype html><html><head><meta charset="utf-8"><title>Етикет</title><style>' +
    '@page { size: 58mm auto; margin: 2mm }' +
    'html,body { margin:0 }' +
    'body { width: 54mm; font-family: system-ui, "Segoe UI", sans-serif; text-align:center }' +
    '.nm { font-size: 11px; font-weight:600; line-height:1.15; margin: 1mm 0; word-break:break-word }' +
    '.pr { font-size: 17px; font-weight:800; margin: 1mm 0 }' +
    'svg { max-width: 100% }' +
    '.no { font-size: 10px; color:#555 }' +
    '</style></head><body>' +
    '<div class="nm">' + esc((p.name || '').slice(0, 70)) + '</div>' +
    (svg || '<div class="no">без баркод/код</div>') +
    (price ? '<div class="pr">' + esc(price) + '</div>' : '') +
    '</body></html>')
}

export function printLabel(p: LabelProduct): void {
  const w = window.open('', '_blank', 'width=380,height=320')
  if (!w) return   // popup blocked — nothing to do (the click that called us should have allowed it)
  w.document.write(labelHtml(p))
  w.document.close()
  // trigger printing from the opener (no inline script in the child → CSP-safe); give the barcode a
  // moment to lay out, then print and close.
  setTimeout(() => { try { w.focus(); w.print() } catch { /* user closed it */ } }, 250)
}
