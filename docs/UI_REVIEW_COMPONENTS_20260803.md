# Admin panel: reusable form/table component system — audit + proposal

2026-08-03. Scope: `src/` React panel (paper theme). Goal: pages consistent **by construction**,
not by copy-paste discipline. Keep it small — ~8 primitives, no design-system startup.

## 1. Audit — what is actually duplicated

Counts from grep across the panel (23–28 files each):

| Pattern | Count | Where / notes |
|---|---|---|
| raw `<input className="input-paper">` (+ select/textarea) | **134** in 23 files | every page styles width/placeholder ad hoc |
| `btn-paper` / `btn-red` buttons | **143** in 28 files | fine as CSS, but busy/disabled label logic (`{busy ? 'създава…' : …}`) is re-rolled everywhere |
| native `<select className="input-paper">` | **28** in 11 files | units, suppliers, taxonomy values… |
| hand-rolled `<table borderCollapse>` | **17** in 12 files | **3 divergent thead styles**: red (`color:var(--red)`, 10 files), pencil (`Sklad`, Intake SupplierTable), 2px-border no color (`BrandRegistryV2`) |
| local `Field` component, byte-identical | **4** copies: `Intake.tsx:654`, `Deliveries.tsx:442`, `Brands.tsx:126`, `BrandDetail.tsx:294` | plus a 5th variant `Labeled` (`Orders.tsx:392`, pencil label) and a 6th `LabeledNumber` (`Intake.tsx:545`) |
| hand-rolled combobox (input + absolute popover + `onBlur setTimeout`) | **2**: `BrandPicker.tsx`, `TaxonomyPicker` in `Intake.tsx:555` | popover chrome copy-pasted verbatim (`Intake.tsx:576` ≡ `BrandPicker.tsx:36`); blur delays drifted (150 vs 180ms); **no keyboard nav, no ARIA** |
| inline eyebrow label style (`.6/.62rem uppercase letterSpacing`) | **8+** inline objects, 2 color variants | `Intake.tsx:236` even hoists it to a local const |
| inline error/note line (`font-type` + `var(--red)`/`var(--ready)`) | **9+** in 7 files | |
| "търси…" search input above a table | **13** | ad-hoc widths 12–16rem |
| `inputStyle` const re-implementing `.input-paper` inline | `BrandRegistryV2.tsx:281` (used 5×) | already drifted from the CSS class |
| `NumberField`/`toNumeric` (comma-decimal, unit inside) | defined **inside** `Intake.tsx:528` | exported but the standard numeric input is buried in a page file |

Existing shared pieces to keep as-is: `Paper.tsx` (Sheet/Clip/Stamp), `PageHeader.tsx`
(PageHeader/EmptyNote/Loading), `Stat.tsx`, `TableControls.tsx` (FilterChips/usePager/Pager),
`Confirm.tsx` (modal provider — already the right pattern).

## 2. The component set (minimal, driven by the audit)

```
src/components/form/
  Field.tsx        Field, FormRow, useFieldWiring   (kills the 6 local label components)
  inputs.tsx       TextInput, TextArea, Select, Checkbox, SearchInput
  NumberInput.tsx  NumberInput + toNumeric          (moved out of Intake.tsx)
  Combobox.tsx     Combobox (async, keyboard, ARIA) (kills both hand-rolled dropdowns)
  Actions.tsx      ActionBar, Msg                   (save/cancel row + error/ok lines)
src/components/
  DataTable.tsx    DataTable<T>                     (kills the 17 table copies)
  Section.tsx      Section                          (eyebrow heading + block)
```

Not building: Form (React state-per-page works fine at this scale), Chips (FilterChips exists),
Modal (Confirm exists), theme provider (CSS vars already do it).

### Props APIs

```tsx
// Field.tsx — label/for wiring via context, so children stay plain inputs
export function Field(props: {
  label: string
  required?: boolean            // red asterisk + aria-required on the input
  error?: string                // red line under input, aria-describedby + aria-invalid
  hint?: string                 // pencil line under input
  tone?: 'red' | 'pencil'       // red = editor eyebrow (default), pencil = compact (Orders style)
  children: ReactNode
}): JSX.Element
export function FormRow({ children, align = 'end' }: {
  children: ReactNode; align?: 'end' | 'start' | 'center'   // flex+wrap, gap .6rem
}): JSX.Element
// inputs call this internally; custom widgets (Combobox) call it too
export function useFieldWiring(): {
  id?: string; 'aria-invalid'?: true; 'aria-describedby'?: string; 'aria-required'?: true
}

// inputs.tsx — thin wrappers over native elements: className + field wiring, nothing clever
export function TextInput(props: InputHTMLAttributes<HTMLInputElement> & {
  pencil?: boolean              // AI-draft styling (hand font, grey) — Intake's `pencil()` helper
  width?: string
}): JSX.Element
export function TextArea(props: TextareaHTMLAttributes<HTMLTextAreaElement> & { pencil?: boolean }): JSX.Element
export type SelectOption = string | { value: string; label: string }
export function Select({ value, onChange, options, placeholder, width }: {
  value: string; onChange: (v: string) => void
  options: SelectOption[]; placeholder?: string; width?: string   // placeholder = leading "" option
}): JSX.Element
export function Checkbox({ checked, onChange, label }: {
  checked: boolean; onChange: (v: boolean) => void; label: ReactNode
}): JSX.Element
export function SearchInput(props: { value: string; onChange: (v: string) => void
  placeholder?: string; width?: string }): JSX.Element   // type="search", standard 13rem

// NumberInput.tsx — BG comma-decimal, unit pinned inside (Intake's NumberField, promoted)
export function toNumeric(s: string): string
export function NumberInput({ value, onChange, unit, width = '5.5rem', placeholder }: {
  value: string; onChange: (v: string) => void; unit?: string; width?: string; placeholder?: string
}): JSX.Element

// Combobox.tsx — ONE async autocomplete for BrandPicker, TaxonomyPicker and future pickers
export interface ComboOption { id: string; label: string; sub?: string }
export function Combobox({ value, onSelect, load, placeholder, action, debounceMs = 200,
  emptyText, footnote }: {
  value: string                                   // committed display value
  onSelect: (o: ComboOption) => void
  load: (q: string) => Promise<ComboOption[]>     // caller owns the API shape
  placeholder?: string
  action?: (q: string, opts: ComboOption[]) =>    // trailing action row, e.g. „+ Добави нов бранд"
    { label: string; run: () => void | Promise<void> } | null
  debounceMs?: number; emptyText?: string; footnote?: ReactNode  // e.g. "Shopify id: …"
}): JSX.Element

// Actions.tsx
export function ActionBar({ busy, error, note, children }: {
  busy?: boolean; error?: string; note?: string; children: ReactNode }): JSX.Element
export function Msg({ tone, children }: { tone: 'error' | 'ok' | 'pending'; children: ReactNode }): JSX.Element

// DataTable.tsx — presentational; pairs with the existing usePager/FilterChips
export interface Column<T> {
  key: string; header: ReactNode
  render: (row: T) => ReactNode
  align?: 'left' | 'right'; width?: string
}
export function DataTable<T>({ columns, rows, rowKey, head = 'red', onRowClick, rowStyle, empty }: {
  columns: Column<T>[]; rows: T[]; rowKey: (r: T) => string | number
  head?: 'red' | 'pencil'                          // the two legitimate thead variants
  onRowClick?: (r: T) => void                      // rows get cursor + keyboard handling
  rowStyle?: (r: T) => CSSProperties | undefined   // status tinting (Deliveries rowColor)
  empty?: ReactNode                                // renders EmptyNote-style when rows.length === 0
}): JSX.Element

// Section.tsx — eyebrow heading over a block (Intake "Атрибути", "Доставчици"…)
export function Section({ title, tone = 'red', right, children }: {
  title: string; tone?: 'red' | 'pencil'; right?: ReactNode; children: ReactNode }): JSX.Element
```

### CSS: inline objects → index.css classes (tokens untouched)

```css
/* labels + fields */
.field { display: grid; gap: .2rem; }
.field-label { font-family: var(--type); font-size: .62rem; text-transform: uppercase;
  letter-spacing: .06em; color: var(--red); font-weight: 700; }
.field-label--pencil { color: var(--pencil); font-weight: 400; font-size: .6rem; }
.field-error { font-family: var(--type); font-size: .74rem; color: var(--red); }
.field-hint  { font-family: var(--hand); font-size: .74rem; color: var(--pencil); }
.input-pencil { font-family: var(--hand); color: var(--pencil); }        /* AI-draft state */
/* combobox popover (the twice-copied chrome) */
.combo-pop { position: absolute; z-index: 10; width: 100%; max-height: 14rem; overflow-y: auto;
  background: var(--paper); border: 1px solid var(--line); border-radius: 3px;
  box-shadow: 2px 3px 7px rgba(0,0,0,.14); }
.combo-opt { display: block; width: 100%; text-align: left; padding: .35rem .5rem;
  border: 0; border-bottom: 1px solid var(--line); background: transparent;
  cursor: pointer; font-size: .8rem; }
.combo-opt[aria-selected="true"], .combo-opt:hover { background: var(--paper2); color: var(--red); }
/* tables */
.table-paper { width: 100%; border-collapse: collapse; font-size: .8rem; }
.table-paper th { text-align: left; padding: .25rem .35rem; color: var(--red);
  border-bottom: 1px solid var(--line); font-weight: 700; }
.table-paper--pencil th { color: var(--pencil); font-weight: 400; }
.table-paper td { padding: .3rem .35rem; border-bottom: 1px dashed var(--line); }
.table-paper td.num, .table-paper th.num { text-align: right; font-variant-numeric: tabular-nums; }
/* messages */
.msg { font-family: var(--type); font-size: .8rem; }
.msg--error { color: var(--red); } .msg--ok { color: var(--ready); } .msg--pending { color: var(--pending); }
```

## 3. Full example: Field.tsx (the keystone)

```tsx
import { createContext, useContext, useId, type ReactNode } from 'react'

type Wiring = { id?: string; 'aria-invalid'?: true; 'aria-describedby'?: string; 'aria-required'?: true }
const FieldCtx = createContext<Wiring>({})
export const useFieldWiring = () => useContext(FieldCtx)

export function Field({ label, required, error, hint, tone = 'red', children }: {
  label: string; required?: boolean; error?: string; hint?: string
  tone?: 'red' | 'pencil'; children: ReactNode
}) {
  const id = useId()
  const descId = error || hint ? `${id}-d` : undefined
  const wiring: Wiring = { id,
    ...(error ? { 'aria-invalid': true as const } : {}),
    ...(descId ? { 'aria-describedby': descId } : {}),
    ...(required ? { 'aria-required': true as const } : {}) }
  return (
    <div className="field">
      <label htmlFor={id} className={`field-label${tone === 'pencil' ? ' field-label--pencil' : ''}`}>
        {label}{required && <span aria-hidden> *</span>}
      </label>
      <FieldCtx.Provider value={wiring}>{children}</FieldCtx.Provider>
      {error ? <span id={descId} className="field-error" role="alert">{error}</span>
        : hint ? <span id={descId} className="field-hint">{hint}</span> : null}
    </div>
  )
}

export function FormRow({ children, align = 'end' }: { children: ReactNode; align?: 'end' | 'start' | 'center' }) {
  return <div style={{ display: 'flex', gap: '.6rem', flexWrap: 'wrap', alignItems: align }}>{children}</div>
}
```

And the pattern every input follows (`inputs.tsx` excerpt):

```tsx
export function TextInput({ pencil, width, style, ...rest }:
  InputHTMLAttributes<HTMLInputElement> & { pencil?: boolean; width?: string }) {
  const wiring = useFieldWiring()
  return <input {...wiring} {...rest}
    className={`input-paper${pencil ? ' input-pencil' : ''}`} style={{ width, ...style }} />
}
```

## 4. Before / after — Intake „Име" + price row

```tsx
// BEFORE (Intake.tsx today — local Field, inline pencil() style object, raw select)
<Field label="Име">
  <input className="input-paper" style={pencil('name', d.name)} value={d.name}
    onChange={(e) => { mark('name'); setD({ ...d, name: e.target.value }) }} />
</Field>
<div style={{ display: 'flex', gap: '.6rem', flexWrap: 'wrap', alignItems: 'end' }}>
  <Field label="Цена"><NumberField value={d.price || ''} unit="€" onChange={(v) => setD({ ...d, price: v })} /></Field>
  <Field label="Мярка">
    <select className="input-paper" value={d.unit || 'бр'} onChange={(e) => setD({ ...d, unit: e.target.value })}>
      {UNITS.map((u) => <option key={u} value={u}>{u}</option>)}
    </select>
  </Field>
</div>

// AFTER (shared components; label/for + aria wiring comes free)
<Field label="Име" required>
  <TextInput pencil={isDraft('name')} value={d.name}
    onChange={(e) => { mark('name'); setD({ ...d, name: e.target.value }) }} />
</Field>
<FormRow>
  <Field label="Цена" required><NumberInput value={d.price || ''} unit="€" onChange={(v) => setD({ ...d, price: v })} /></Field>
  <Field label="Мярка"><Select value={d.unit || 'бр'} options={UNITS} onChange={(v) => setD({ ...d, unit: v })} /></Field>
</FormRow>
```

BrandPicker becomes a 15-line adapter (same public API, zero call-site churn):

```tsx
export function BrandPicker({ value, onPick }: { value: string; onPick: (b: string) => void }) {
  return <Combobox value={value} placeholder="търси или въведи бранд…"
    load={async (q) => (await api.get<BrandsResp>(`/api/brands?q=${encodeURIComponent(q)}&limit=20`))
      .brands.map((b) => ({ id: b.name, label: b.name }))}
    onSelect={(o) => onPick(o.label)}
    action={(q, opts) => q.trim() && !opts.some((o) => o.label.toLowerCase() === q.trim().toLowerCase())
      ? { label: `+ Добави нов бранд „${q.trim()}“`,
          run: async () => { await api.post('/api/brands', { name: q.trim() }); onPick(q.trim()) } }
      : null} />
}
```

## 5. Accessibility baked in (fixes real gaps found)

- **Label wiring**: `Field` owns `useId` + `htmlFor`; inputs consume via context → no page can
  render an unlabelled control through the system. (Today `TaxonomyAttributes` rows are span+input
  with no association.)
- **Combobox**: full ARIA 1.2 pattern — `role="combobox"` `aria-expanded` `aria-controls` on the
  input, `role="listbox"`/`role="option"` + `aria-activedescendant` in the popover; ArrowUp/Down,
  Enter selects, Escape closes and restores, Tab commits-and-closes. Selection on `click` with a
  `pointerdown` preventDefault — retires both `onBlur setTimeout` hacks (150/180ms races).
- **DataTable**: `<th scope="col">`; `onRowClick` rows get `tabIndex={0}` + Enter/Space (today's
  clickable `<tr>` in BrandRegistryV2 is mouse-only).
- **Errors**: `role="alert"` + `aria-invalid` from the `error` prop; focus stays where it was.
- Existing global focus-visible ring and 24px hit-area CSS keep applying — nothing overrides outline.

## 6. Migration plan — small, CI-safe steps

Principle: **new system lands dark** (no page changes) → convert the actively-developed page →
mechanical deletions → convert the rest on touch. Every PR ships green on its own; no big bang.

| PR | Contents | Size / risk |
|---|---|---|
| 1 | CSS classes above + `form/Field.tsx`, `form/inputs.tsx`, `form/NumberInput.tsx` (move `toNumeric` here, re-export from Intake for now), `Actions.tsx`, `Section.tsx` + unit tests (label wiring, toNumeric). **Zero call sites change.** | ~½ day, no risk |
| 2 | `form/Combobox.tsx` + keyboard tests; rewrite **BrandPicker** on top of it (public API unchanged — Intake untouched). | ~1 day; test on staging with a real brand add |
| 3 | **Convert Intake** (it's the active lane): swap local Field/NumberField/LabeledNumber → shared; `TaxonomyPicker` → Combobox (keeps its `footnote` id line); eyebrow spans → `Section`; error/note lines → `ActionBar`. Delete the local copies. Intake shrinks ~120 lines. | ~1 day; verify the enrich→pencil→save flow end-to-end on staging |
| 4 | `DataTable.tsx`; convert two proof tables: Deliveries `LineTable` (rowStyle tinting) and Intake `SupplierTable` (pencil head + input row via a plain extra `<tr>` slot — keep it simple, inputs-in-tables stay page-owned). | ~1 day |
| 5 | Mechanical deletions: local `Field` in Deliveries/Brands/BrandDetail, `Labeled` in Orders, `inputStyle` in BrandRegistryV2 → shared imports. Grep-verifiable, no behaviour change. | ~½ day total, can be one PR |
| 6+ | On-touch rule: any PR that edits a page converts *that page's* forms/tables. Big static pages (Dashboard, Catalog, Cod) are **not** proactively rewritten. | ongoing |

Ratchet so it sticks (2-person project, no review police): add a CI grep step after PR 3 —
`git grep -l 'className="input-paper"' src/pages/` against an allowlist file; converting a page
removes it from the allowlist, adding a new raw input to a converted page fails CI.

Per project rules: test the RESULT — each conversion PR gets a staging screenshot/walkthrough of
the touched page (Intake: create → enrich → save reaches Barsy probe), not just passing unit tests.
