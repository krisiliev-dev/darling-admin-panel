import { useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { AssistantChat } from '../pages/Assistant'

// A minimizable assistant, docked bottom-right on EVERY page. Click the pen bubble to open a chat
// panel; minimise it back to the bubble. Keeps its conversation while minimised (stays mounted).
export function FloatingAssistant() {
  const [open, setOpen] = useState(false)
  return (
    <>
      {/* the launcher bubble — hidden while the panel is open */}
      <AnimatePresence>
        {!open && (
          <motion.button
            initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0, opacity: 0 }}
            onClick={() => setOpen(true)} title="Асистент — питай на български" aria-label="Отвори асистента"
            className="no-print"
            style={{
              position: 'fixed', right: '1.1rem', bottom: '1.1rem', zIndex: 60,
              width: 54, height: 54, borderRadius: '50%', cursor: 'pointer',
              background: 'var(--red)', color: '#fff', border: 'none',
              boxShadow: '0 4px 14px rgba(0,0,0,.3)', fontSize: '1.4rem', lineHeight: 1,
            }}>✎</motion.button>
        )}
      </AnimatePresence>

      {/* the chat panel — stays mounted (display toggled) so the conversation survives minimise */}
      <div style={{ display: open ? 'block' : 'none' }}>
        <div className="no-print" style={{
          position: 'fixed', right: '1.1rem', bottom: '1.1rem', zIndex: 60,
          width: 'min(24rem, calc(100vw - 2rem))', maxHeight: 'min(34rem, calc(100vh - 2rem))',
        }}>
          <div className="sheet" style={{ display: 'flex', flexDirection: 'column', height: 'min(34rem, calc(100vh - 2rem))', padding: '.8rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '.5rem' }}>
              <span className="font-type" style={{ fontWeight: 700, color: 'var(--red)' }}>✎ Асистент</span>
              <button onClick={() => setOpen(false)} title="Минимизирай" aria-label="Минимизирай асистента"
                style={{ border: 'none', background: 'transparent', cursor: 'pointer', color: 'var(--pencil)', fontFamily: 'var(--type)', fontSize: '1.1rem', lineHeight: 1 }}>—</button>
            </div>
            <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
              <AssistantChat bare minHeight="100%" />
            </div>
          </div>
        </div>
      </div>
    </>
  )
}
