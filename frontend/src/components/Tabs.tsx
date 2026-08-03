import type { ReactNode } from 'react'

// Folder tabs (owner 2026-08-03: "tabs can be displayed as if a filing folder"). One component
// for every tabbed hub page, so Продукти and Склад stop inventing their own chip rows.
//
// The active tab is drawn joined to the sheet below it: same paper, no bottom border, pulled down
// 1px over the sheet's top edge. Wrap the content in <FolderBody> to get that edge.
export interface FolderTab {
  key: string
  label: string
  hint?: string
}

export function FolderTabs({
  tabs,
  active,
  onSelect,
  right,
}: {
  tabs: FolderTab[]
  active: string
  onSelect: (key: string) => void
  right?: ReactNode
}) {
  return (
    <div className="folder-tabs" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.key}
          role="tab"
          type="button"
          id={`tab-${t.key}`}
          aria-selected={t.key === active}
          aria-controls={`panel-${t.key}`}
          tabIndex={t.key === active ? 0 : -1}
          title={t.hint}
          className="folder-tab"
          onClick={() => onSelect(t.key)}
          onKeyDown={(e) => {
            // Left/Right move between tabs, per the ARIA tabs pattern — the old chip rows were
            // mouse-only.
            if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
            e.preventDefault()
            const i = tabs.findIndex((x) => x.key === active)
            const next = tabs[(i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length]
            onSelect(next.key)
            document.getElementById(`tab-${next.key}`)?.focus()
          }}
        >
          {t.label}
        </button>
      ))}
      {right && <span className="folder-tabs-right">{right}</span>}
    </div>
  )
}

export function FolderBody({ tabKey, children }: { tabKey: string; children: ReactNode }) {
  return (
    <div className="folder-body" role="tabpanel" id={`panel-${tabKey}`} aria-labelledby={`tab-${tabKey}`}>
      {children}
    </div>
  )
}
