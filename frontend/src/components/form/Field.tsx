import { createContext, useContext, useId, type ReactNode } from 'react'

// One label component for the whole panel. Before this, six byte-identical `Field`/`Labeled`
// copies lived inside page files and drifted; worse, several inputs had no label association at
// all. `Field` owns the id and hands the wiring to whatever input renders inside it through
// context, so a control rendered through this system CANNOT come out unlabelled.
export type FieldWiring = {
  id?: string
  'aria-invalid'?: true
  'aria-describedby'?: string
  'aria-required'?: true
}

const FieldCtx = createContext<FieldWiring>({})

// Inputs (and custom widgets like Combobox) call this and spread the result onto the element.
export const useFieldWiring = (): FieldWiring => useContext(FieldCtx)

export function Field({
  label,
  required,
  error,
  hint,
  tone = 'red',
  children,
}: {
  label: string
  required?: boolean
  error?: string
  hint?: string
  tone?: 'red' | 'pencil'
  children: ReactNode
}) {
  const id = useId()
  const descId = error || hint ? `${id}-d` : undefined
  const wiring: FieldWiring = {
    id,
    ...(error ? { 'aria-invalid': true as const } : {}),
    ...(descId ? { 'aria-describedby': descId } : {}),
    ...(required ? { 'aria-required': true as const } : {}),
  }
  return (
    <div className="field">
      <label htmlFor={id} className={`field-label${tone === 'pencil' ? ' field-label--pencil' : ''}`}>
        {label}
        {required && <span aria-hidden> *</span>}
      </label>
      <FieldCtx.Provider value={wiring}>{children}</FieldCtx.Provider>
      {error ? (
        <span id={descId} className="field-error" role="alert">
          {error}
        </span>
      ) : hint ? (
        <span id={descId} className="field-hint">
          {hint}
        </span>
      ) : null}
    </div>
  )
}

// The horizontal group used all over the editors (price + unit, weight + checkbox…).
export function FormRow({
  children,
  align = 'end',
}: {
  children: ReactNode
  align?: 'end' | 'start' | 'center'
}) {
  return (
    <div style={{ display: 'flex', gap: '.6rem', flexWrap: 'wrap', alignItems: align }}>{children}</div>
  )
}
