import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Vitest config kept separate from vite.config.ts so the app build/dev config stays untouched.
// jsdom for DOM-dependent tests (hooks, KeyboardEvent); jest-dom matchers via setup file.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    // jsdom + jest-dom init is slow on this Windows host (~30s/worker); running files in
    // parallel spawns several such workers at once and blows the pool-startup timeout.
    // Serial execution reuses one warm worker across files — deterministic and reliable.
    fileParallelism: false,
  },
})
