import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { api, ApiError } from '../lib/api'
import type { AssistantResponse, AssistantSuggestion } from '../lib/api'
import { Sheet } from '../components/Paper'
import { PageHeader } from '../components/PageHeader'

// Асистент — the in-panel command box. Ask in Bulgarian; the answer is grounded in the panel's
// live numbers. Suggested actions are human-confirmed (the model proposes; you click).
interface Msg { role: 'user' | 'assistant' | 'error'; text: string; model?: string; suggestions?: AssistantSuggestion[] }

const EXAMPLES = [
  'Колко продукта са готови за старт?',
  'Има ли поръчки за изпращане днес?',
  'Колко наложен платеж чака изплащане?',
  'Как да добавя нов продукт?',
]

export function Assistant() {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()

  // scroll to newest after render (rAF is paused in backgrounded tabs; an effect isn't)
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [msgs])

  const runAction = async (s: AssistantSuggestion) => {
    if (s.kind === 'goto') { navigate(s.to); return }
    if (s.kind === 'sync_tracking') {
      setMsgs((m) => [...m, { role: 'assistant', text: 'Синхронизирам проследяването…' }])
      try {
        const r = await api.post<{ delivered: number }>('/api/cod/sync-tracking')
        setMsgs((m) => [...m, { role: 'assistant', text: `Готово — ${r.delivered} нови доставени.` }])
      } catch (e) {
        setMsgs((m) => [...m, { role: 'error', text: e instanceof ApiError ? e.message : 'Действието не успя.' }])
      }
    }
  }

  const send = async (text: string) => {
    const question = text.trim()
    if (!question || busy) return
    setMsgs((m) => [...m, { role: 'user', text: question }])
    setQ(''); setBusy(true)
    try {
      const r = await api.post<AssistantResponse>('/api/assistant/ask', { question })
      setMsgs((m) => [...m, { role: 'assistant', text: r.answer, model: r.model, suggestions: r.suggestions }])
    } catch (e) {
      const text = e instanceof ApiError ? e.message : 'Асистентът не отговори.'
      setMsgs((m) => [...m, { role: 'error', text }])
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <PageHeader title="Асистент" subtitle="питай на български за числата и работата на панела" />

      <Sheet clip="left" style={{ display: 'flex', flexDirection: 'column', minHeight: '26rem' }}>
        <div ref={scrollRef} style={{ flex: 1, overflowY: 'auto', display: 'grid', gap: '.7rem', maxHeight: '30rem', paddingRight: '.3rem' }}>
          {msgs.length === 0 && (
            <div style={{ display: 'grid', gap: '.6rem', placeContent: 'center', textAlign: 'center', padding: '1.5rem 0' }}>
              <p className="font-hand" style={{ color: 'var(--pencil)', fontSize: '1.05rem', margin: 0 }}>
                Питай ме нещо за магазина — отговарям с реалните числа от панела.
              </p>
              <div style={{ display: 'flex', gap: '.4rem', flexWrap: 'wrap', justifyContent: 'center' }}>
                {EXAMPLES.map((ex) => (
                  <button key={ex} className="font-type" onClick={() => void send(ex)}
                    style={{ fontSize: '.72rem', padding: '.3rem .6rem', border: '1px dashed var(--line)', borderRadius: 2, background: 'var(--paper2)', color: 'var(--pencil)', cursor: 'pointer' }}>
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          )}
          {msgs.map((m, i) => <Bubble key={i} msg={m} onAction={runAction} />)}
          {busy && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}
              className="font-hand" style={{ color: 'var(--pencil)', fontSize: '.95rem' }}>
              мисля…
            </motion.div>
          )}
        </div>

        <form onSubmit={(e) => { e.preventDefault(); void send(q) }}
          style={{ display: 'flex', gap: '.5rem', marginTop: '1rem', borderTop: '1px solid var(--line)', paddingTop: '.8rem' }}>
          <input className="input-paper" style={{ flex: 1 }} placeholder="напиши въпрос…"
            aria-label="въпрос към асистента" value={q} onChange={(e) => setQ(e.target.value)} disabled={busy} />
          <button className="btn-paper btn-red" type="submit" disabled={busy || !q.trim()}>Питай</button>
        </form>
      </Sheet>

      <p className="pencil" style={{ fontSize: '.78rem', margin: 0 }}>
        Асистентът чете реалните данни на панела, отговаря и може да предлага действия —
        които ти потвърждаваш, преди да се изпълнят.
      </p>
    </div>
  )
}

function Bubble({ msg, onAction }: { msg: Msg; onAction: (s: AssistantSuggestion) => void }) {
  const isUser = msg.role === 'user'
  const isError = msg.role === 'error'
  return (
    <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
      style={{ display: 'flex', flexDirection: 'column', alignItems: isUser ? 'flex-end' : 'flex-start' }}>
      <div style={{
        maxWidth: '80%', padding: '.6rem .8rem', borderRadius: 4,
        background: isUser ? 'var(--sticky)' : isError ? 'transparent' : 'var(--paper2)',
        border: isUser ? 'none' : `1px solid ${isError ? 'var(--blocked)' : 'var(--line)'}`,
        boxShadow: isUser ? '2px 3px 7px rgba(0,0,0,.14)' : 'none',
        fontFamily: isUser ? 'var(--hand)' : 'var(--serif)',
        color: isUser ? '#2e2909' : isError ? 'var(--blocked)' : 'var(--ink)',
        fontSize: '.9rem', lineHeight: 1.5, whiteSpace: 'pre-wrap',
      }}>
        {msg.text}
        {msg.model && (
          <div className="font-type" style={{ fontSize: '.6rem', color: 'var(--pencil)', marginTop: '.35rem' }}>
            ✎ {msg.model}
          </div>
        )}
      </div>
      {msg.suggestions && msg.suggestions.length > 0 && (
        <div style={{ display: 'flex', gap: '.4rem', marginTop: '.4rem', flexWrap: 'wrap' }}>
          {msg.suggestions.map((s, i) => (
            <button key={i} className="font-type" onClick={() => onAction(s)}
              style={{ fontSize: '.72rem', padding: '.3rem .6rem', border: '1px solid var(--line)', borderRadius: 2, background: 'var(--paper)', color: 'var(--red)', cursor: 'pointer' }}>
              → {s.label}
            </button>
          ))}
        </div>
      )}
    </motion.div>
  )
}
