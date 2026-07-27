import { createContext, useCallback, useContext, useRef, useState } from 'react'

// In-app confirm / alert / prompt modals — replace the browser's native window.confirm/alert/prompt
// (which break the paper aesthetic, aren't theme-aware, and block automation/CDP). Two hooks:
//   const confirm = useConfirm()
//   if (!(await confirm('Прилагане на цените?'))) return
//   await confirm({ title: 'Готово', message: '…', mode: 'alert' })          // OK-only
//   const prompt = usePrompt()
//   const text = await prompt({ message: 'Опиши случая:', minLength: 5, multiline: true })  // string | null
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

type PromptOpts = {
  title?: string
  message: string
  placeholder?: string
  defaultValue?: string
  confirmLabel?: string
  cancelLabel?: string
  minLength?: number
  multiline?: boolean
}
type PromptFn = (opts: PromptOpts) => Promise<string | null>

const ConfirmCtx = createContext<ConfirmFn>(async (o) => window.confirm(typeof o === 'string' ? o : o.message))
const PromptCtx = createContext<PromptFn>(async (o) => window.prompt(o.message, o.defaultValue ?? ''))
export const useConfirm = () => useContext(ConfirmCtx)
export const usePrompt = () => useContext(PromptCtx)

type State =
  | { kind: 'confirm'; opts: ConfirmOpts }
  | { kind: 'prompt'; opts: PromptOpts; value: string }

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<State | null>(null)
  const resolver = useRef<((v: boolean | string | null) => void) | undefined>(undefined)

  const confirm = useCallback<ConfirmFn>((opts) => {
    setState({ kind: 'confirm', opts: typeof opts === 'string' ? { message: opts } : opts })
    return new Promise<boolean>((res) => { resolver.current = res as (v: boolean | string | null) => void })
  }, [])
  const prompt = useCallback<PromptFn>((opts) => {
    setState({ kind: 'prompt', opts, value: opts.defaultValue ?? '' })
    return new Promise<string | null>((res) => { resolver.current = res as (v: boolean | string | null) => void })
  }, [])

  const settle = (v: boolean | string | null) => { setState(null); resolver.current?.(v); resolver.current = undefined }

  const isAlert = state?.kind === 'confirm' && state.opts.mode === 'alert'
  const isPrompt = state?.kind === 'prompt'
  const opts = state?.opts
  const promptValue = state?.kind === 'prompt' ? state.value : ''
  const promptOk = !isPrompt || promptValue.trim().length >= (state?.kind === 'prompt' ? (state.opts.minLength ?? 1) : 1)

  // confirm/alert → true; prompt → the entered string. cancel/escape/backdrop → false (confirm) / null (prompt).
  const accept = () => settle(isPrompt ? promptValue : true)
  const cancel = () => settle(isPrompt ? null : false)

  return (
    <ConfirmCtx.Provider value={confirm}>
    <PromptCtx.Provider value={prompt}>
      {children}
      {state && opts && (
        <div role="dialog" aria-modal="true"
          onClick={(e) => { if (e.target === e.currentTarget) cancel() }}
          onKeyDown={(e) => {
            if (e.key === 'Escape') cancel()
            // Enter accepts, except inside a multiline prompt (there Enter should insert a newline)
            if (e.key === 'Enter' && !(isPrompt && state.opts.multiline)) { if (promptOk) accept() }
          }}
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.45)', display: 'grid', placeItems: 'center', zIndex: 1000 }}>
          <div className="font-type" style={{ background: 'var(--paper)', border: '1px solid var(--red)', borderRadius: 4, padding: '1.1rem 1.3rem', width: 'min(28rem, 92vw)', boxShadow: '0 10px 34px rgba(0,0,0,.35)', display: 'grid', gap: '.7rem' }}>
            {opts.title && <div style={{ fontWeight: 700, fontSize: '.95rem' }}>{opts.title}</div>}
            <div style={{ fontSize: '.86rem', lineHeight: 1.45, whiteSpace: 'pre-line' }}>{opts.message}</div>
            {isPrompt && state.kind === 'prompt' && (
              state.opts.multiline ? (
                <textarea className="input-paper" autoFocus rows={3} placeholder={state.opts.placeholder}
                  value={promptValue} onChange={(e) => setState({ ...state, value: e.target.value })} />
              ) : (
                <input className="input-paper" autoFocus placeholder={state.opts.placeholder}
                  value={promptValue} onChange={(e) => setState({ ...state, value: e.target.value })} />
              )
            )}
            <div style={{ display: 'flex', gap: '.5rem', justifyContent: 'flex-end', marginTop: '.3rem' }}>
              {!isAlert && <button className="btn-paper" onClick={cancel}>{opts.cancelLabel || 'Отказ'}</button>}
              <button className="btn-paper btn-red" autoFocus={!isPrompt} disabled={!promptOk} onClick={accept}>
                {opts.confirmLabel || (isAlert ? 'Добре' : 'Потвърди')}
              </button>
            </div>
          </div>
        </div>
      )}
    </PromptCtx.Provider>
    </ConfirmCtx.Provider>
  )
}
