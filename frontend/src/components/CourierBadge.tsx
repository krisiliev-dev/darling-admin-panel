// Courier readiness status pill. The tone+label logic was duplicated between the Поръчки
// courier strip (bordered cards) and the Настройки courier list (compact rows); this centralises
// it. `compact` gives the shorter labels + smaller type the narrow Settings column uses.
type Readiness = { ready: boolean; configured: boolean }

export function courierTone(c: Readiness): string {
  return c.ready ? 'var(--ready)' : c.configured ? 'var(--pending)' : 'var(--pencil)'
}

export function CourierBadge({ c, compact }: { c: Readiness; compact?: boolean }) {
  const label = c.ready
    ? 'готов'
    : c.configured
      ? (compact ? 'настройка' : 'нужна настройка')
      : (compact ? 'няма ключ' : 'няма ключове')
  return (
    <span
      className="font-type"
      style={{
        fontSize: compact ? '.68rem' : '.62rem',
        textTransform: compact ? undefined : 'uppercase',
        color: courierTone(c),
      }}
    >
      ● {label}
    </span>
  )
}
