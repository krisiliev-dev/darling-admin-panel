import { useEffect, useRef, useState } from 'react'

// A KPI on a small paper chit. The number counts up — motion that says "just tallied".
export function Stat({
  label, value, suffix = '', tone, hint, money = false, instant = false,
}: {
  label: string
  value: number | string
  suffix?: string
  tone?: 'ready' | 'blocked' | 'pending'
  hint?: string
  money?: boolean // format as euro amount (2 decimals) — Bulgaria is on the euro since 2026
  instant?: boolean // skip the count-up — for money/at-risk tiles where the exact figure must be instant
}) {
  const numeric = typeof value === 'number'
  const [shown, setShown] = useState<number | string>(numeric && !instant ? 0 : value)
  const raf = useRef(0)

  useEffect(() => {
    if (!numeric || instant) { setShown(value); return }
    const target = value as number
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduce || target === 0) { setShown(target); return }

    const duration = 450  // brief tally, not a delay before the number is readable (Doherty)
    let start = 0
    const tick = (t: number) => {
      if (!start) start = t
      const p = Math.min((t - start) / duration, 1)
      const eased = 1 - Math.pow(1 - p, 3) // easeOutCubic
      setShown(Math.round(target * eased))
      if (p < 1) raf.current = requestAnimationFrame(tick)
      else setShown(target) // land exactly on the real number
    }
    raf.current = requestAnimationFrame(tick)
    // Safety net: rAF is paused in hidden/background tabs, so guarantee the real
    // number lands even if the animation never runs. setTimeout still fires there.
    const settle = window.setTimeout(() => setShown(target), duration + 120)
    return () => { cancelAnimationFrame(raf.current); clearTimeout(settle) }
  }, [value, numeric, instant])

  // Conditional salience: a "problem" tone (blocked/pending) must go quiet at zero, or a dashboard
  // that's always amber trains operators to ignore amber (Few). 'ready' is affirmative → always shown.
  const problemTone = tone === 'blocked' || tone === 'pending'
  const muted = problemTone && numeric && value === 0
  const toneColor = tone && !muted ? `var(--${tone})` : 'var(--ink)'
  return (
    <div style={{
      background: 'var(--paper2)', border: '1px solid var(--line)', borderRadius: 2,
      padding: '.85rem 1rem', boxShadow: '2px 3px 7px rgba(0,0,0,.13)', position: 'relative',
    }}>
      <div className="font-type" style={{
        fontSize: '.62rem', letterSpacing: '.1em', textTransform: 'uppercase',
        color: 'var(--red)', marginBottom: '.25rem',
      }}>{label}</div>
      <div className="font-type" style={{ fontSize: '1.7rem', fontWeight: 700, color: toneColor, lineHeight: 1.1 }}>
        {typeof shown === 'number'
          ? (money
              ? shown.toLocaleString('bg-BG', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' €'
              : shown.toLocaleString('bg-BG'))
          : shown}{money ? '' : suffix}
      </div>
      {hint && <div style={{ fontSize: '.74rem', color: 'var(--pencil)', marginTop: '.15rem' }}>{hint}</div>}
    </div>
  )
}
