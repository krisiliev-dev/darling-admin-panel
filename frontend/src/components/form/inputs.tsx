import type { InputHTMLAttributes, ReactNode, TextareaHTMLAttributes } from 'react'
import { useFieldWiring } from './Field'

// Thin wrappers over the native elements: the shared class + the Field wiring, nothing clever.
// The point is that no page has to remember `className="input-paper"` or re-invent a width.

export function TextInput({
  pencil,
  width,
  style,
  className,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { pencil?: boolean; width?: string }) {
  const wiring = useFieldWiring()
  return (
    <input
      {...wiring}
      {...rest}
      className={`input-paper${pencil ? ' input-pencil' : ''}${className ? ' ' + className : ''}`}
      style={{ ...(width ? { width } : {}), ...style }}
    />
  )
}

export function TextArea({
  pencil,
  className,
  ...rest
}: TextareaHTMLAttributes<HTMLTextAreaElement> & { pencil?: boolean }) {
  const wiring = useFieldWiring()
  return (
    <textarea
      {...wiring}
      {...rest}
      className={`input-paper${pencil ? ' input-pencil' : ''}${className ? ' ' + className : ''}`}
    />
  )
}

export type SelectOption = string | { value: string; label: string }

export function Select({
  value,
  onChange,
  options,
  placeholder,
  width,
  disabled,
}: {
  value: string
  onChange: (v: string) => void
  options: SelectOption[]
  placeholder?: string
  width?: string
  disabled?: boolean
}) {
  const wiring = useFieldWiring()
  return (
    <select
      {...wiring}
      className="input-paper"
      style={width ? { width } : undefined}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    >
      {placeholder !== undefined && <option value="">{placeholder}</option>}
      {options.map((o) => {
        const v = typeof o === 'string' ? o : o.value
        const l = typeof o === 'string' ? o : o.label
        return (
          <option key={v} value={v}>
            {l}
          </option>
        )
      })}
    </select>
  )
}

export function Checkbox({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  label: ReactNode
  disabled?: boolean
}) {
  return (
    <label style={{ display: 'inline-flex', alignItems: 'center', gap: '.4rem', cursor: 'pointer' }}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span style={{ fontSize: '.8rem' }}>{label}</span>
    </label>
  )
}

// The "search above a table" input, 13rem by default so the thirteen copies stop drifting.
export function SearchInput({
  value,
  onChange,
  placeholder,
  width = '13rem',
  onKeyDown,
}: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  width?: string
  onKeyDown?: InputHTMLAttributes<HTMLInputElement>['onKeyDown']
}) {
  const wiring = useFieldWiring()
  return (
    <input
      {...wiring}
      type="search"
      className="input-paper"
      style={{ width }}
      placeholder={placeholder}
      value={value}
      onKeyDown={onKeyDown}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}
