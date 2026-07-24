import { Component, type ErrorInfo, type ReactNode } from 'react'

// A render throw shouldn't white-screen the operator with no way back (Nielsen #9: help users
// recover). This catches it and offers a reload on a paper sheet.
interface State { error: Error | null }

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error('UI error boundary caught:', error, info)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div style={{ minHeight: '60vh', display: 'grid', placeItems: 'center', padding: '2rem' }}>
        <div className="sticky-note" style={{ transform: 'rotate(-1deg)', maxWidth: '30rem', textAlign: 'center' }}>
          <div style={{ fontWeight: 700, marginBottom: '.4rem' }}>Нещо се обърка.</div>
          <div style={{ fontSize: '.85rem', marginBottom: '.8rem' }}>
            Панелът срещна грешка при показването. Данните ти са в безопасност — презареди страницата.
          </div>
          <button className="btn-paper btn-red" onClick={() => window.location.reload()}>Презареди</button>
        </div>
      </div>
    )
  }
}
