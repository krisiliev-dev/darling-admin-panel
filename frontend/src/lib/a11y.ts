import type { KeyboardEvent } from 'react'

// Make a non-<button> clickable element (a table row, a sort header, a div) keyboard-operable:
// it becomes focusable and Enter/Space activates it, matching the mouse click. Spread the returned
// props onto the element and drop the separate onClick.
//   <tr {...clickable(() => open(id))}> … </tr>
export function clickable(onActivate: () => void) {
  return {
    role: 'button' as const,
    tabIndex: 0,
    onClick: onActivate,
    onKeyDown: (e: KeyboardEvent) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        onActivate()
      }
    },
  }
}
