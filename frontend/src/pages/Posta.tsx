import { useCallback, useEffect, useState } from 'react'

import { api } from '../lib/api'
import { Sheet } from '../components/Paper'
import { PageHeader } from '../components/PageHeader'

// Поща — the orders@ mailbox INSIDE the panel (owner decision 2026-08-05: „everything in one
// place" — full read+reply here, not a link-out; supersedes the stage-1 signpost that pointed
// at Roundcube). Backend: /api/mail/* (IMAP/SMTP against our own mail server; staff+).
//
// Mail is HOSTILE INPUT: the backend strips scripts/handlers, and we STILL render HTML bodies
// only inside a sandboxed iframe (no scripts, no top-navigation, no same-origin). Text bodies
// render as plain <pre>. Never innerHTML into the page itself.

interface Folder { name: string; total: number; unread: number }
interface MsgRow { uid: string; subject: string; from: string; to: string; date: string; seen: boolean }
interface MsgFull extends MsgRow {
  cc: string; message_id: string; body_text: string; body_html: string
  attachments: { filename: string; content_type: string; size: number }[]
}
interface MsgList { folder: string; page: number; per: number; total: number; messages: MsgRow[] }

const PER = 25

function fmtDate(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  return isNaN(d.getTime()) ? iso : d.toLocaleString('bg-BG', { dateStyle: 'short', timeStyle: 'short' })
}

function senderEmail(from: string): string {
  const m = from.match(/<([^>]+)>/)
  return (m ? m[1] : from).trim()
}

export function Posta() {
  const [folders, setFolders] = useState<Folder[]>([])
  const [folder, setFolder] = useState('INBOX')
  const [page, setPage] = useState(1)
  const [list, setList] = useState<MsgList | null>(null)
  const [msg, setMsg] = useState<MsgFull | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [compose, setCompose] = useState<{ to: string; subject: string; body: string; replyId: string } | null>(null)
  const [sending, setSending] = useState(false)

  const loadFolders = useCallback(() => {
    api.get<{ folders: Folder[] }>('/api/mail/folders')
      .then((d) => setFolders(d.folders))
      .catch((e) => setErr(e.message || 'Пощата не отговаря.'))
  }, [])

  const loadList = useCallback((f: string, p: number) => {
    setLoading(true)
    setErr('')
    api.get<MsgList>(`/api/mail/messages?folder=${encodeURIComponent(f)}&page=${p}&per=${PER}`)
      .then((d) => setList(d))
      .catch((e) => setErr(e.message || 'Писмата не се заредиха.'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { loadFolders() }, [loadFolders])
  useEffect(() => { loadList(folder, page) }, [folder, page, loadList])

  const open = (uid: string) => {
    setMsg(null)
    api.get<MsgFull>(`/api/mail/messages/${uid}?folder=${encodeURIComponent(folder)}`)
      .then((d) => {
        setMsg(d)
        setList((l) => l ? { ...l, messages: l.messages.map((m) => m.uid === uid ? { ...m, seen: true } : m) } : l)
        loadFolders()
      })
      .catch((e) => setErr(e.message || 'Писмото не се отвори.'))
  }

  const startReply = () => {
    if (!msg) return
    setCompose({
      to: senderEmail(msg.from),
      subject: msg.subject.startsWith('Re:') ? msg.subject : `Re: ${msg.subject}`,
      body: `\n\n––– На ${fmtDate(msg.date)} ${msg.from} написа:\n${(msg.body_text || '').split('\n').map((l) => `> ${l}`).join('\n')}`,
      replyId: msg.message_id,
    })
  }

  const send = () => {
    if (!compose) return
    setSending(true)
    api.post('/api/mail/send', {
      to: compose.to, subject: compose.subject, body: compose.body,
      reply_message_id: compose.replyId,
    })
      .then(() => { setCompose(null); loadFolders() })
      .catch((e) => setErr(e.message || 'Писмото не се изпрати.'))
      .finally(() => setSending(false))
  }

  const pages = list ? Math.max(1, Math.ceil(list.total / PER)) : 1

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <PageHeader title="Поща" subtitle="orders@darlinghome.bg — четене и отговори направо от панела" />
      {err && (
        <Sheet><p style={{ color: 'var(--ink-red, #a33)', margin: 0, fontSize: '.85rem' }}>⚠ {err}</p></Sheet>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '180px 1fr', gap: '1rem', alignItems: 'start' }}>
        {/* folders */}
        <Sheet clip="left">
          <div style={{ display: 'grid', gap: '.25rem' }}>
            {folders.map((f) => (
              <button key={f.name} className="btn-paper" data-testid={`folder-${f.name}`}
                onClick={() => { setFolder(f.name); setPage(1); setMsg(null) }}
                style={{
                  textAlign: 'left', fontWeight: f.name === folder ? 700 : 400,
                  display: 'flex', justifyContent: 'space-between', gap: '.5rem',
                }}>
                <span>{f.name === 'INBOX' ? 'Входящи' : f.name === 'Sent' ? 'Изпратени'
                  : f.name === 'Drafts' ? 'Чернови' : f.name === 'Trash' ? 'Кош'
                  : f.name === 'Junk' ? 'Спам' : f.name}</span>
                {f.unread > 0 && <span style={{ fontSize: '.75rem', color: 'var(--pencil)' }}>{f.unread}</span>}
              </button>
            ))}
            <button className="btn-paper" style={{ marginTop: '.6rem' }}
              onClick={() => setCompose({ to: '', subject: '', body: '', replyId: '' })}>
              ✎ Ново писмо
            </button>
          </div>
        </Sheet>

        {/* list + reader */}
        <div style={{ display: 'grid', gap: '1rem' }}>
          <Sheet>
            {loading && <p className="pencil" style={{ margin: 0, fontSize: '.85rem' }}>зарежда…</p>}
            {!loading && list && list.messages.length === 0 && (
              <p className="pencil" style={{ margin: 0, fontSize: '.85rem' }}>Няма писма в тази папка.</p>
            )}
            {!loading && list && list.messages.length > 0 && (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.85rem' }}>
                <tbody>
                  {list.messages.map((m) => (
                    <tr key={m.uid} onClick={() => open(m.uid)} data-testid={`msg-${m.uid}`}
                      style={{ cursor: 'pointer', borderBottom: '1px solid var(--rule, #ddd)' }}>
                      <td style={{ padding: '.45rem .4rem', fontWeight: m.seen ? 400 : 700, whiteSpace: 'nowrap' }}>
                        {m.from.replace(/<[^>]*>/, '').trim() || m.from}
                      </td>
                      <td style={{ padding: '.45rem .4rem', fontWeight: m.seen ? 400 : 700 }}>{m.subject}</td>
                      <td style={{ padding: '.45rem .4rem', color: 'var(--pencil)', whiteSpace: 'nowrap', textAlign: 'right' }}>
                        {fmtDate(m.date)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {list && pages > 1 && (
              <div style={{ display: 'flex', gap: '.6rem', marginTop: '.7rem', alignItems: 'center' }}>
                <button className="btn-paper" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>← по-нови</button>
                <span className="pencil" style={{ fontSize: '.8rem' }}>стр. {page} от {pages}</span>
                <button className="btn-paper" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>по-стари →</button>
              </div>
            )}
          </Sheet>

          {msg && (
            <Sheet clip="right" tilt="r">
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', alignItems: 'baseline' }}>
                <h3 style={{ fontSize: '1rem', margin: 0 }}>{msg.subject}</h3>
                <div style={{ display: 'flex', gap: '.5rem' }}>
                  <button className="btn-paper" onClick={startReply}>↩ Отговори</button>
                  <button className="btn-paper" onClick={() => setMsg(null)}>✕</button>
                </div>
              </div>
              <p className="pencil" style={{ fontSize: '.8rem', margin: '.3rem 0 .8rem' }}>
                От: {msg.from} · До: {msg.to} {msg.cc ? `· Копие: ${msg.cc}` : ''} · {fmtDate(msg.date)}
              </p>
              {msg.attachments.length > 0 && (
                <p style={{ fontSize: '.8rem', margin: '0 0 .6rem' }}>
                  📎 {msg.attachments.map((a) => `${a.filename} (${Math.round(a.size / 1024)}KB)`).join(', ')}
                  <span className="pencil"> — прикачените файлове се отварят през пощенската кутия засега</span>
                </p>
              )}
              {msg.body_html ? (
                <iframe title="писмо" sandbox="" srcDoc={msg.body_html}
                  style={{ width: '100%', minHeight: '360px', border: '1px solid var(--rule, #ddd)', background: '#fff' }} />
              ) : (
                <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'inherit', fontSize: '.88rem', margin: 0 }}>
                  {msg.body_text || '(празно писмо)'}
                </pre>
              )}
            </Sheet>
          )}
        </div>
      </div>

      {compose && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,.35)', zIndex: 40,
          display: 'grid', placeItems: 'center', padding: '1rem',
        }}>
          <Sheet style={{ width: 'min(680px, 96vw)', maxHeight: '90vh', overflow: 'auto' }}>
            <h3 style={{ fontSize: '1rem', marginTop: 0 }}>{compose.replyId ? 'Отговор' : 'Ново писмо'}</h3>
            <div style={{ display: 'grid', gap: '.6rem' }}>
              <label style={{ fontSize: '.82rem' }}>До
                <input value={compose.to} data-testid="compose-to"
                  onChange={(e) => setCompose({ ...compose, to: e.target.value })}
                  style={{ width: '100%', display: 'block' }} />
              </label>
              <label style={{ fontSize: '.82rem' }}>Тема
                <input value={compose.subject} data-testid="compose-subject"
                  onChange={(e) => setCompose({ ...compose, subject: e.target.value })}
                  style={{ width: '100%', display: 'block' }} />
              </label>
              <textarea value={compose.body} rows={10} data-testid="compose-body"
                onChange={(e) => setCompose({ ...compose, body: e.target.value })}
                style={{ width: '100%', fontFamily: 'inherit', fontSize: '.88rem' }} />
              <div style={{ display: 'flex', gap: '.6rem', justifyContent: 'flex-end' }}>
                <button className="btn-paper" onClick={() => setCompose(null)} disabled={sending}>Отказ</button>
                <button className="btn-paper" data-testid="compose-send" onClick={send}
                  disabled={sending || !compose.to.trim() || !compose.subject.trim() || !compose.body.trim()}>
                  {sending ? 'изпраща…' : '✉ Изпрати'}
                </button>
              </div>
            </div>
          </Sheet>
        </div>
      )}
    </div>
  )
}
