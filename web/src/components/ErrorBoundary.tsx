import { Component, type ReactNode } from 'react'
import { Link } from 'react-router-dom'

interface Props { children: ReactNode; fallback?: ReactNode; resetKey?: unknown }

/**
 * Keeps one bad render (a card in a shape the app doesn't expect) from blanking the whole app: what's
 * inside shows its fallback instead. A new `resetKey` (another page, a newer card) tries again. What
 * it catches is reported to the server (main.tsx's root, errors.ts).
 */
export class ErrorBoundary extends Component<Props, { failed: boolean; key: unknown }> {
  state = { failed: false, key: this.props.resetKey }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  static getDerivedStateFromProps(props: Props, state: { failed: boolean; key: unknown }) {
    return props.resetKey !== state.key ? { failed: false, key: props.resetKey } : null
  }

  render() {
    return this.state.failed ? this.props.fallback ?? <PageError /> : this.props.children
  }
}

/** A page that couldn't be shown: say so, and offer a reload and the way back to Today. */
export function PageError() {
  return (
    <div className="card card-pad stack" role="alert" style={{ gap: 12 }}>
      <span className="title">Something went wrong showing this page.</span>
      <span className="small muted">Reload to try again, or go back to Today.</span>
      <span className="row" style={{ justifyContent: 'flex-start', flexWrap: 'wrap' }}>
        <button type="button" className="btn primary" onClick={() => window.location.reload()}>Reload</button>
        <Link to="/" className="btn">Back to Today</Link>
      </span>
    </div>
  )
}

/** A part of a page that couldn't be shown; the rest of the page still works. */
export function BlockError() {
  return <div className="card empty small" role="alert">This part of the page couldn't be shown.</div>
}
