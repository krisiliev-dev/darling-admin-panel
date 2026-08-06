import { useState } from 'react'
import { motion } from 'framer-motion'
import { useAuth } from '../lib/auth'
import { Sheet, Stamp } from '../components/Paper'

// One unified login. Routing is by the identifier: an EMAIL → the local owner (break-glass)
// account; anything else → BARSY (staff username + password, verified against Barsy). No mode
// toggle needed — the owner types an email, staff type their Barsy username. First-run shows the
// one-time owner-setup form instead.
//
// 2FA (mandatory for every role, owner decision 2026-08-05): if the account is enrolled, the
// password step returns a short-lived TICKET instead of a session, and this form switches to a
// six-digit code step. Accounts that have not enrolled see exactly the form they always saw.
export function Login() {
  const { needsSetup, barsyLogin, login, twofaLogin, setupOwner } = useAuth()
  const [ident, setIdent] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [showPw, setShowPw] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // second factor
  const [ticket, setTicket] = useState('')
  const [code, setCode] = useState('')
  const [useRecovery, setUseRecovery] = useState(false)

  const backToPassword = (msg: string) => {
    setTicket(''); setCode(''); setUseRecovery(false); setPassword(''); setError(msg)
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setError('')
    try {
      if (needsSetup) {
        await setupOwner(ident, password, fullName)                 // first-run owner (email)
      } else {
        const pending = ident.includes('@')
          ? await login(ident, password)                             // owner / local account
          : await barsyLogin(ident, password)                        // staff (Barsy username)
        if (pending) { setTicket(pending.ticket); setCode('') }      // second factor owed
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Нещо се обърка.')
    } finally {
      setBusy(false)
    }
  }

  const submitCode = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setError('')
    try {
      await twofaLogin(ticket, useRecovery ? '' : code.trim(), useRecovery ? code.trim() : '')
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Грешен код.'
      // an expired/consumed ticket cannot be retried — send them back to the password step
      if (/зтекла|звалидна|зползвана/.test(msg)) backToPassword(msg)
      else { setError(msg); setCode('') }
    } finally {
      setBusy(false)
    }
  }

  const labelSpan = {
    fontSize: '.68rem', color: 'var(--red)', textTransform: 'uppercase' as const, letterSpacing: '.08em',
  }

  return (
    <div style={{ minHeight: '100%', display: 'grid', placeItems: 'center', padding: '2rem 1.4rem', position: 'relative', zIndex: 2 }}>
      <motion.div initial={{ opacity: 0, y: 12, rotate: -1.2 }} animate={{ opacity: 1, y: 0, rotate: -0.5 }}
        transition={{ duration: 0.4, ease: 'easeOut' }} style={{ width: 'min(26rem, 100%)' }}>
        <Sheet clip="left">
          <div className="font-type" style={{ fontSize: '.72rem', color: 'var(--pencil)', borderBottom: '1px solid var(--line)', paddingBottom: '.5rem', marginBottom: '1rem', display: 'flex', justifyContent: 'space-between' }}>
            <span>ДАРЛИНГ · вътрешна докладна</span>
            <Stamp>{needsSetup ? 'нов' : ticket ? 'код' : 'вход'}</Stamp>
          </div>

          {ticket ? (
            <>
              <h1 style={{ fontSize: '1.5rem', marginBottom: '.3rem' }}>Втора стъпка</h1>
              <p style={{ color: 'var(--pencil)', fontSize: '.9rem', margin: '0 0 1.2rem' }}>
                {useRecovery
                  ? 'Въведете един от кодовете за възстановяване. Всеки работи еднократно.'
                  : 'Въведете 6-цифрения код от приложението за удостоверяване.'}
              </p>
              <form onSubmit={submitCode} style={{ display: 'grid', gap: '.7rem' }}>
                <label style={{ display: 'grid', gap: '.25rem' }}>
                  <span className="font-type" style={labelSpan}>
                    {useRecovery ? 'Код за възстановяване' : 'Код'}
                  </span>
                  <input className="input-paper" value={code} data-testid="twofa-code" autoFocus
                    onChange={(e) => setCode(e.target.value)}
                    inputMode={useRecovery ? 'text' : 'numeric'}
                    autoComplete="one-time-code"
                    maxLength={useRecovery ? 40 : 6}
                    placeholder={useRecovery ? 'xxxx-xxxx' : '000000'}
                    aria-label={useRecovery ? 'код за възстановяване' : 'код от приложението'} />
                </label>

                {error && (
                  <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}
                    style={{ color: 'var(--red)', fontFamily: 'var(--type)', fontSize: '.8rem' }}>{error}</motion.div>
                )}

                <button className="btn-paper btn-red" type="submit" data-testid="twofa-submit"
                  disabled={busy || !code.trim()} style={{ marginTop: '.4rem' }}>
                  {busy ? 'проверява…' : 'Продължи'}
                </button>

                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '.6rem', flexWrap: 'wrap' }}>
                  <button type="button" className="font-type" data-testid="use-recovery"
                    onClick={() => { setUseRecovery((v) => !v); setCode(''); setError('') }}
                    style={{ background: 'none', border: 'none', color: 'var(--pencil)', fontSize: '.74rem', cursor: 'pointer', padding: 0, textDecoration: 'underline' }}>
                    {useRecovery ? 'използвай код от приложението' : 'използвай код за възстановяване'}
                  </button>
                  <button type="button" className="font-type"
                    onClick={() => backToPassword('')}
                    style={{ background: 'none', border: 'none', color: 'var(--pencil)', fontSize: '.74rem', cursor: 'pointer', padding: 0, textDecoration: 'underline' }}>
                    назад
                  </button>
                </div>
              </form>
            </>
          ) : (
            <>
              <h1 style={{ fontSize: '1.5rem', marginBottom: '.3rem' }}>
                {needsSetup ? 'Създай акаунт на собственика' : 'Влез в панела'}
              </h1>
              <p style={{ color: 'var(--pencil)', fontSize: '.9rem', margin: '0 0 1.2rem' }}>
                {needsSetup
                  ? 'Акаунт на собственика — прави се веднъж. Влизаш с имейл и парола.'
                  : 'Служители: Barsy потребител + парола. Собственик: имейл + парола.'}
              </p>

              <form onSubmit={submit} style={{ display: 'grid', gap: '.7rem' }}>
                {needsSetup && (
                  <label style={{ display: 'grid', gap: '.25rem' }}>
                    <span className="font-type" style={labelSpan}>Име</span>
                    <input className="input-paper" value={fullName} onChange={(e) => setFullName(e.target.value)} placeholder="Собственик" autoComplete="name" />
                  </label>
                )}
                <label style={{ display: 'grid', gap: '.25rem' }}>
                  <span className="font-type" style={labelSpan}>{needsSetup ? 'Имейл' : 'Потребител или имейл'}</span>
                  <input className="input-paper" type={needsSetup ? 'email' : 'text'} required value={ident}
                    onChange={(e) => setIdent(e.target.value)} autoComplete="username" aria-label="потребител или имейл" />
                </label>
                <label style={{ display: 'grid', gap: '.25rem' }}>
                  <span className="font-type" style={labelSpan}>Парола</span>
                  <div style={{ position: 'relative', display: 'flex' }}>
                    <input className="input-paper" type={showPw ? 'text' : 'password'} required value={password}
                      onChange={(e) => setPassword(e.target.value)} style={{ width: '100%', paddingRight: '3.6rem' }}
                      autoComplete={needsSetup ? 'new-password' : 'current-password'} />
                    <button type="button" onClick={() => setShowPw((v) => !v)}
                      aria-label={showPw ? 'скрий паролата' : 'покажи паролата'}
                      className="font-type"
                      style={{ position: 'absolute', right: '.5rem', top: '50%', transform: 'translateY(-50%)',
                        background: 'none', border: 'none', color: 'var(--pencil)', fontSize: '.72rem', cursor: 'pointer', padding: '.2rem' }}>
                      {showPw ? 'скрий' : 'покажи'}
                    </button>
                  </div>
                </label>

                {error && (
                  <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}
                    style={{ color: 'var(--red)', fontFamily: 'var(--type)', fontSize: '.8rem' }}>{error}</motion.div>
                )}

                <button className="btn-paper btn-red" type="submit" disabled={busy} style={{ marginTop: '.4rem' }}>
                  {busy ? 'момент…' : needsSetup ? 'Създай и влез' : 'Влез'}
                </button>
              </form>
            </>
          )}
        </Sheet>
      </motion.div>
    </div>
  )
}
