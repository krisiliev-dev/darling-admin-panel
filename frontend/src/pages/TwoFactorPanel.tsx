import { useCallback, useEffect, useState } from 'react'

import { api } from '../lib/api'
import { Sheet } from '../components/Paper'

// Сигурност — per-user TOTP enrollment (owner decision 2026-08-05: 2FA mandatory for every role).
// Lives in Настройки and is deliberately NOT behind the system-access check: every user must be
// able to enroll, including staff who can see nothing else on this page.
//
// The QR arrives from the backend as an SVG STRING. It is rendered through an <img src="data:…">
// rather than injected as markup: an SVG loaded as an image cannot run script, so even if the
// generator were ever compromised the worst case is a broken picture, not code in the panel.

interface Status { enabled: boolean; recovery_remaining: number; mandatory: boolean }
interface SetupResp { secret: string; otpauth_uri: string; qr_svg: string }

const svgSrc = (svg: string) => `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`

export function TwoFactorPanel() {
  const [status, setStatus] = useState<Status | null>(null)
  const [setup, setSetup] = useState<SetupResp | null>(null)
  const [code, setCode] = useState('')
  const [codes, setCodes] = useState<string[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [saved, setSaved] = useState(false)

  const load = useCallback(() => {
    api.get<Status>('/api/auth/2fa/status').then(setStatus).catch(() => setStatus(null))
  }, [])
  useEffect(() => { load() }, [load])

  const begin = async () => {
    setBusy(true); setErr('')
    try { setSetup(await api.post<SetupResp>('/api/auth/2fa/setup')) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Неуспешно стартиране.') }
    finally { setBusy(false) }
  }

  const confirm = async () => {
    setBusy(true); setErr('')
    try {
      const r = await api.post<{ recovery_codes: string[] }>('/api/auth/2fa/verify', { code: code.trim() })
      setCodes(r.recovery_codes); setSetup(null); setCode(''); load()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Грешен код.')
    } finally { setBusy(false) }
  }

  const regenerate = async () => {
    setBusy(true); setErr(''); setSaved(false)
    try {
      const r = await api.post<{ recovery_codes: string[] }>('/api/auth/2fa/recovery/regenerate')
      setCodes(r.recovery_codes); load()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Неуспешно.')
    } finally { setBusy(false) }
  }

  const label = { fontSize: '.68rem', color: 'var(--red)', textTransform: 'uppercase' as const, letterSpacing: '.08em' }

  // the one-time recovery codes — shown once, never retrievable again
  if (codes) {
    return (
      <Sheet clip="left" tilt="l">
        <h3 style={{ fontSize: '1rem', margin: '0 0 .3rem' }}>Кодове за възстановяване</h3>
        <p style={{ fontSize: '.85rem', color: 'var(--pencil)', margin: '0 0 .8rem' }}>
          Запишете ги <strong>сега</strong> — показват се само този път. Всеки код работи
          еднократно и влиза в употреба, ако изгубите телефона си.
        </p>
        <div data-testid="recovery-codes" style={{
          display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(9rem, 1fr))', gap: '.4rem',
          fontFamily: 'var(--type)', fontSize: '.95rem', background: 'var(--paper2)',
          border: '1px solid var(--line)', padding: '.8rem', borderRadius: 3, marginBottom: '.9rem',
        }}>
          {codes.map((c) => <span key={c}>{c}</span>)}
        </div>
        <div style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap' }}>
          <button className="btn-paper" onClick={() => navigator.clipboard?.writeText(codes.join('\n'))}>
            Копирай
          </button>
          <button className="btn-paper" onClick={() => window.print()}>Разпечатай</button>
          <button className="btn-paper btn-red" data-testid="codes-saved"
            onClick={() => { setCodes(null); setSaved(true) }}>
            Записах ги
          </button>
        </div>
      </Sheet>
    )
  }

  return (
    <Sheet clip="left" tilt="l">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '1rem', flexWrap: 'wrap' }}>
        <h3 style={{ fontSize: '1rem', margin: 0 }}>Сигурност · двуфакторно влизане</h3>
        {status && (
          <span className="font-type" style={{ fontSize: '.72rem', color: status.enabled ? 'var(--green, #2c6e49)' : 'var(--red)' }}>
            {status.enabled ? '✓ включено' : 'изключено'}
          </span>
        )}
      </div>

      {err && <p style={{ color: 'var(--red)', fontSize: '.82rem', margin: '.6rem 0 0' }}>{err}</p>}
      {saved && !err && (
        <p style={{ fontSize: '.82rem', color: 'var(--pencil)', margin: '.6rem 0 0' }}>
          Готово. От следващото влизане панелът ще иска код от приложението.
        </p>
      )}

      {status?.enabled ? (
        <>
          <p style={{ fontSize: '.85rem', color: 'var(--pencil)', margin: '.5rem 0 .8rem' }}>
            Влизането иска код от приложението ви.
            Остават <strong>{status.recovery_remaining}</strong> кода за възстановяване.
          </p>
          <button className="btn-paper" onClick={regenerate} disabled={busy}>
            {busy ? 'момент…' : 'Нови кодове за възстановяване'}
          </button>
        </>
      ) : setup ? (
        <div style={{ display: 'grid', gap: '.7rem', marginTop: '.7rem' }}>
          <p style={{ fontSize: '.85rem', color: 'var(--pencil)', margin: 0 }}>
            1. Сканирайте кода с приложение за удостоверяване (Google Authenticator, Aegis, 1Password…).
            2. Въведете 6-цифрения код, който то показва.
          </p>
          <img src={svgSrc(setup.qr_svg)} alt="QR код за приложението"
            data-testid="totp-qr"
            style={{ width: 190, height: 190, background: '#fff', padding: '.4rem', border: '1px solid var(--line)' }} />
          <p style={{ fontSize: '.75rem', color: 'var(--pencil)', margin: 0 }}>
            Ако камерата не работи, въведете ключа ръчно:{' '}
            <code style={{ fontFamily: 'var(--type)', wordBreak: 'break-all' }}>{setup.secret}</code>
          </p>
          <label style={{ display: 'grid', gap: '.25rem', maxWidth: '12rem' }}>
            <span className="font-type" style={label}>Код от приложението</span>
            <input className="input-paper" value={code} data-testid="totp-code"
              onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code"
              maxLength={6} placeholder="000000" />
          </label>
          <div style={{ display: 'flex', gap: '.5rem' }}>
            <button className="btn-paper btn-red" onClick={confirm} disabled={busy || code.trim().length < 6}
              data-testid="totp-confirm">
              {busy ? 'проверява…' : 'Потвърди и включи'}
            </button>
            <button className="btn-paper" onClick={() => { setSetup(null); setCode(''); setErr('') }}>Отказ</button>
          </div>
        </div>
      ) : (
        <>
          <p style={{ fontSize: '.85rem', color: 'var(--pencil)', margin: '.5rem 0 .8rem' }}>
            Втора стъпка при влизане — код от приложение на телефона ви. Задължително е за всички
            акаунти. Отнема минута.
          </p>
          <button className="btn-paper btn-red" onClick={begin} disabled={busy} data-testid="totp-begin">
            {busy ? 'момент…' : 'Включи двуфакторно влизане'}
          </button>
        </>
      )}
    </Sheet>
  )
}
