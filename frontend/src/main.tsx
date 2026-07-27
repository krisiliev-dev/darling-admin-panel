import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
// self-hosted (no Google CDN → GDPR-safe): Nunito, a clean rounded "typed" face for sticky notes
import '@fontsource/nunito/400.css'
import '@fontsource/nunito/600.css'
import '@fontsource/nunito/700.css'
import './index.css'
import App from './App.tsx'
import { AuthProvider } from './lib/auth'
import { ConfirmProvider } from './components/Confirm'

// Apply the saved theme at the root before first paint, so the Login screen (which lives
// outside the Shell where useTheme runs) honours the persisted light/dark preference.
const savedTheme = localStorage.getItem('darling-theme')
if (savedTheme === 'light' || savedTheme === 'dark') {
  document.documentElement.setAttribute('data-theme', savedTheme)
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <div className="grain" />
    <BrowserRouter>
      <AuthProvider>
        <ConfirmProvider>
          <App />
        </ConfirmProvider>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
