import type { Page } from '@playwright/test'

export const OWNER = {
  id: 'u1', email: 'owner@darling.bg', full_name: 'Owner', role: 'owner', is_superuser: true,
  is_sysadmin: false,   // the owner passes system guards on role alone — no grant needed
}

// A Barsy Мениджър: full BUSINESS authority (is_superuser) but NO system access. Use this to prove
// that system surfaces stay hidden from a manager — the owner's 2026-07-30 narrowing.
export const MANAGER = {
  id: 'u2', email: 'mgr@darling.bg', full_name: 'Мария Мениджър', role: 'manager',
  is_superuser: true, is_sysadmin: false,
}

// Route every /api/** call. `routes` maps a path substring → JSON body (first match wins); anything
// else gets a sensible default so no call reaches a real backend and the app renders the auth'd shell.
export async function mockApi(page: Page, routes: Record<string, unknown> = {}) {
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    for (const [pat, body] of Object.entries(routes)) {
      if (path.includes(pat)) return route.fulfill({ json: body as object })
    }
    if (path.endsWith('/api/users/me')) return route.fulfill({ json: OWNER })
    if (path.includes('/api/auth/setup-status')) return route.fulfill({ json: { needs_setup: false } })
    return route.fulfill({ json: {} })   // generic empty for any unmocked list endpoint
  })
}
