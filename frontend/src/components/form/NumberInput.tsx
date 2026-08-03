import { useFieldWiring } from './Field'

// Digits + a SINGLE comma decimal (dots become commas), so the catalogue is written one way
// (BG comma-decimal, no stray text). Promoted out of Intake.tsx — it is the standard numeric
// input for every measurement and price field, not an Intake detail.
export function toNumeric(s: string): string {
  let v = s.replace(/\./g, ',').replace(/[^\d,]/g, '')
  const i = v.indexOf(',')
  if (i !== -1) v = v.slice(0, i + 1) + v.slice(i + 1).replace(/,/g, '')
  return v
}

export function NumberInput({
  value,
  onChange,
  unit,
  placeholder,
  width = '5.5rem',
  disabled,
}: {
  value: string
  onChange: (v: string) => void
  unit?: string
  placeholder?: string
  width?: string
  disabled?: boolean
}) {
  const wiring = useFieldWiring()
  return (
    <span style={{ position: 'relative', display: 'inline-flex', alignItems: 'center' }}>
      <input
        {...wiring}
        className="input-paper"
        inputMode="decimal"
        style={{ width, paddingRight: unit ? '2.1rem' : undefined }}
        placeholder={placeholder}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(toNumeric(e.target.value))}
      />
      {unit && (
        <span
          style={{
            position: 'absolute',
            right: '.45rem',
            color: 'var(--pencil)',
            fontSize: '.72rem',
            pointerEvents: 'none',
          }}
        >
          {unit}
        </span>
      )}
    </span>
  )
}
