import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../lib/api'

// The 2FA reminder strip (owner decision 2026-08-06, replacing the planned blocking wall):
// nobody is ever locked out of their work — an un-enrolled account simply carries a notice on
// Начало that cannot be dismissed. The only way to remove it is to enroll, which is the point.
//
// Deliberately has NO close button and NO localStorage "snooze": a dismissible nag is a nag you
// stop seeing on day two. It renders nothing at all once 2FA is on, and it stays silent if the
// status call fails — a backend hiccup must not paint a scary red strip across the cockpit.

interface Status { enabled: boolean; recovery_remaining: number; mandatory: boolean }

export function TwoFactorNag() {
  const [status, setStatus] = useState<Status | null>(null)

  useEffect(() => {
    let alive = true
    api.get<Status>('/api/auth/2fa/status')
      .then((s) => { if (alive) setStatus(s) })
      .catch(() => { if (alive) setStatus(null) })
    return () => { alive = false }
  }, [])

  if (!status || status.enabled) return null

  return (
    <div data-testid="twofa-nag" role="status" style={{
      display: 'flex', alignItems: 'center', gap: '.9rem', flexWrap: 'wrap',
      border: '1px solid var(--red)', borderLeft: '5px solid var(--red)',
      background: 'var(--paper2)', padding: '.85rem 1.1rem', borderRadius: 3,
    }}>
      <span aria-hidden style={{ fontSize: '1.15rem', lineHeight: 1 }}>🔐</span>
      <div style={{ flex: '1 1 20rem', minWidth: '14rem' }}>
        <strong style={{ display: 'block', fontSize: '.92rem' }}>
          Включете двуфакторно влизане
        </strong>
        <span style={{ fontSize: '.84rem', color: 'var(--pencil)' }}>
          Втора стъпка при вход — код от приложение на телефона ви. Отнема минута и пази
          панела дори ако паролата ви изтече навън. Този надпис изчезва, щом го включите.
        </span>
      </div>
      <Link to="/settings" className="btn-paper btn-red" data-testid="twofa-nag-cta"
        style={{ textDecoration: 'none', whiteSpace: 'nowrap' }}>
        Включи сега
      </Link>
    </div>
  )
}
