// Small relative-time helpers for aging / SLA cues on operator queues. Bulgarian output.
export function hoursSince(iso: string): number {
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return 0
  return (Date.now() - t) / 3_600_000
}

export function timeSinceBg(iso: string): string {
  const h = hoursSince(iso)
  if (h < 1) return 'току-що'
  if (h < 24) return `преди ${Math.floor(h)}ч`
  return `преди ${Math.floor(h / 24)}д`
}

// hours an order may sit awaiting a label before the queue visually escalates it
export const SLA_HOURS = 24
// graduated cue: amber "approaching" before the red breach, so it never flips binary at 24h
export const SLA_WARN = 18
