import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { motion } from 'framer-motion'
import { api } from '../lib/api'
import { useAuth } from '../lib/auth'
import { useTheme } from '../lib/theme'
import { CommandPalette } from './CommandPalette'
import { FloatingAssistant } from './FloatingAssistant'

// Menu is by JOB, not by table (design spec). Packer lands in shipping; owner sees all.
// `hideFor` exists for ONE case: a destination that became a tab somewhere the role can already
// reach. Склад is owner/manager-only, so Доставки and Заявки become tabs there for those two and
// stay top-level entries for staff/packer - the consolidation must not quietly take away a page
// somebody was using.
const NAV: { to: string; label: string; roles?: string[]; hideFor?: string[] }[] = [
  { to: '/', label: 'Начало' },
  { to: '/analytics', label: 'Анализи' },
  // Онлайн = Поръчки · Кошници · Разчети — the whole online channel behind one entry
  { to: '/online', label: 'Онлайн' },
  { to: '/products', label: 'Продукти' },
  { to: '/intake', label: 'Нов продукт' },
  { to: '/purchasing', label: 'Доставки', hideFor: ['owner', 'manager'] },
  { to: '/zayavki', label: 'Заявки', hideFor: ['owner', 'manager'] },
  { to: '/sklad', label: 'Склад', roles: ['owner', 'manager'] },
  { to: '/issues', label: 'Проблеми' },
  // Брандове + Марки (+ Марки v2) are tabs inside Продукти now — one destination, not
  // three near-synonyms in the nav bar.
  { to: '/kancelaria', label: 'Канцелария' },
  { to: '/posta', label: 'Поща' },
  { to: '/clients', label: 'Клиенти', roles: ['owner', 'manager'] },
  { to: '/accounts', label: 'Сметки', roles: ['owner', 'manager'] },
  { to: '/reports', label: 'Справки', roles: ['owner', 'manager'] },
  { to: '/settings', label: 'Настройки', roles: ['owner', 'manager'] },
]

const ROLE_BG: Record<string, string> = {
  owner: 'собственик', manager: 'мениджър', staff: 'екип', packer: 'опаковчик',
}

export function Shell() {
  const { me, logout } = useAuth()
  const { theme, toggle } = useTheme()
  const [badges, setBadges] = useState<Record<string, number>>({})

  // pending-work counts per nav route (refresh periodically for state visibility)
  useEffect(() => {
    const load = () => api.get<Record<string, number>>('/api/summary/badges').then(setBadges).catch(() => {})
    void load()
    const t = setInterval(load, 60000)
    return () => clearInterval(t)
  }, [])

  const visible = NAV.filter((n) => {
    if (n.hideFor && me && n.hideFor.includes(me.role)) return false
    return !n.roles || (me && n.roles.includes(me.role)) || me?.role === 'owner'
  })

  return (
    <div style={{ minHeight: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* sticky desk header */}
      <header
        style={{
          position: 'sticky', top: 0, zIndex: 20, background: 'var(--desk)',
          borderBottom: '1px solid rgba(0,0,0,.3)', color: 'var(--paper)',
        }}>
        <div style={{
          maxWidth: '78rem', margin: '0 auto', padding: '0 1.4rem', height: '3rem',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <div className="font-type" style={{ display: 'flex', gap: '.5rem', alignItems: 'center', fontWeight: 700 }}>
            <span style={{
              width: '.6rem', height: '.6rem', background: 'var(--red)', borderRadius: '50%',
              boxShadow: '0 0 0 2px rgba(255,255,255,.2)',
            }} />
            ДАРЛИНГ · панел
          </div>
          <div style={{ display: 'flex', gap: '.6rem', alignItems: 'center' }}>
            <button className="font-type" title="команди (⌘K)" aria-label="отвори командите"
              onClick={() => window.dispatchEvent(new CustomEvent('darling:open-palette'))}
              style={{ fontSize: '.62rem', opacity: .75, background: 'transparent', color: 'inherit', cursor: 'pointer', border: '1px solid rgba(255,255,255,.25)', borderRadius: 3, padding: '.2rem .4rem' }}>
              ⌘K
            </button>
            {me && (
              <span className="font-type" style={{ fontSize: '.7rem', opacity: .85 }}>
                {me.full_name || me.email} · {ROLE_BG[me.role] ?? me.role}
              </span>
            )}
            <button className="btn-paper" style={{ fontSize: '.7rem', padding: '.3rem .55rem' }}
              onClick={toggle} title="смяна на темата">
              {theme === 'dark' ? '◑ светло' : '◐ тъмно'}
            </button>
            {me && (
              <button className="btn-paper" style={{ fontSize: '.7rem', padding: '.3rem .55rem' }}
                onClick={() => void logout()}>изход</button>
            )}
          </div>
        </div>
      </header>

      {/* task-oriented nav */}
      <nav style={{ background: 'var(--desk)', borderBottom: '1px solid rgba(0,0,0,.25)' }}>
        <div style={{
          maxWidth: '78rem', margin: '0 auto', padding: '0 1.4rem',
          display: 'flex', gap: '.2rem',
        }}>
          {visible.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === '/'}
              style={{ textDecoration: 'none' }}>
              {({ isActive }) => (
                <div className="font-type" style={{
                  position: 'relative', padding: '.55rem .9rem', fontSize: '.78rem',
                  color: isActive ? 'var(--paper)' : 'var(--pencil2)', cursor: 'pointer',
                  display: 'flex', alignItems: 'center', gap: '.35rem',
                }}>
                  {n.label}
                  {(badges[n.to] > 0 || (n.to === '/online' && (badges['/orders'] || 0) + (badges['/cod'] || 0) > 0)) && (
                    <span aria-label={`${badges[n.to]} чакащи`} style={{
                      background: 'var(--red)', color: '#fff', fontSize: '.6rem', fontWeight: 700,
                      borderRadius: 8, padding: '0 .35rem', minWidth: '1.1rem', textAlign: 'center', lineHeight: '1.1rem',
                    }}>{n.to === '/online' ? (badges['/online'] || 0) + (badges['/orders'] || 0) + (badges['/cod'] || 0) : badges[n.to]}</span>
                  )}
                  {isActive && (
                    <motion.div layoutId="nav-underline"
                      style={{
                        position: 'absolute', left: '.6rem', right: '.6rem', bottom: 0,
                        height: 2, background: 'var(--red)',
                      }} />
                  )}
                </div>
              )}
            </NavLink>
          ))}
        </div>
      </nav>

      {/* page */}
      <main style={{ maxWidth: '78rem', margin: '0 auto', padding: '1.4rem', width: '100%', flex: 1, position: 'relative', zIndex: 2 }}>
        <Outlet />
      </main>

      <CommandPalette />
      <FloatingAssistant />
    </div>
  )
}
