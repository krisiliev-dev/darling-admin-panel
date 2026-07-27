import { createContext, useCallback, useContext, useRef, useState } from 'react'

// In-app confirm/alert modal — replaces the browser's native window.confirm/alert (which breaks the
// paper aesthetic, isn't theme-aware, and blocks automation). Usage:
//   const confirm = useConfirm()
//   if (!(await confirm('Прилагане на цените?'))) return
//   await confirm({ title: 'Готово', message: '…', mode: 'alert' })   // OK-only
type ConfirmOpts = {
  title?: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
  mode?: 'confirm' | 'alert'
}
type ConfirmArg = ConfirmOpts | string
type ConfirmFn = (opts: ConfirmArg) => Promise<boolean>

const Ctx = createContext<ConfirmFn>(async () => window.confirm('?'))
export const useConfirm = () => useContext(Ctx)

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<ConfirmOpts | null>(null)
  const resolver = useRef<((v: boolean) => void) | undefined>(undefined)

  const confirm = useCallback<ConfirmFn>((opts) => {
    setState(typeof opts === 'string' ? { message: opts } : opts)
    return new Promise<boolean>((res) => { resolver.current = res })
  }, [])

  const close = (v: boolean) => { setState(null); resolver.current?.(v); resolver.current = undefined }
  const isAlert = state?.mode === 'alert'

  return (
    <Ctx.Provider value={confirm}>
      {children}
      {state && (
        <div role="dialog" aria-modal="true"
          onClick={(e) => { if (e.target === e.currentTarget) close(false) }}
          onKeyDown={(e) => { if (e.key === 'Escape') close(false); if (e.key === 'Enter') close(true) }}
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.45)', display: 'grid', placeItems: 'center', zIndex: 1000 }}>
          <div className="font-type" style={{ background: 'var(--paper)', border: '1px solid var(--red)', borderRadius: 4, padding: '1.1rem 1.3rem', width: 'min(28rem, 92vw)', boxShadow: '0 10px 34px rgba(0,0,0,.35)', display: 'grid', gap: '.7rem' }}>
            {state.title && <div style={{ fontWeight: 700, fontSize: '.95rem' }}>{state.title}</div>}
            <div style={{ fontSize: '.86rem', lineHeight: 1.45, whiteSpace: 'pre-line' }}>{state.message}</div>
            <div style={{ display: 'flex', gap: '.5rem', justifyContent: 'flex-end', marginTop: '.3rem' }}>
              {!isAlert && <button className="btn-paper" onClick={() => close(false)}>{state.cancelLabel || 'Отказ'}</button>}
              <button className="btn-paper btn-red" autoFocus onClick={() => close(true)}>
                {state.confirmLabel || (isAlert ? 'Добре' : 'Потвърди')}
              </button>
            </div>
          </div>
        </div>
      )}
    </Ctx.Provider>
  )
}
