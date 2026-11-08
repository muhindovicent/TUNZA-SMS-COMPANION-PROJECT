import { useEffect, useId, useState } from 'react'
import { loadFoundationStatus } from './api/health'
import type { FoundationStatus } from './api/health'

function BrandMark() {
  return (
    <svg viewBox="0 0 48 48" fill="none" aria-hidden="true">
      <path d="M24 42C11 34 6 25 10 16c4-9 12-8 14-2 2-6 10-7 14 2 4 9-1 18-14 26Z" fill="currentColor" />
      <path d="M24 31V18m0 9-6-5m6 2 6-5" stroke="#fff" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  )
}

function ConnectionCard({
  title,
  description,
  state,
}: {
  title: string
  description: string
  state: 'reachable' | 'connected' | 'unavailable' | 'checking'
}) {
  const titleId = useId()
  const healthy = state === 'reachable' || state === 'connected'
  const label = { reachable: 'Reachable', connected: 'Connected', unavailable: 'Unavailable', checking: 'Checking…' }[state]

  return (
    <article className="connection-card" aria-labelledby={titleId}>
      <div className="card-topline">
        <h3 id={titleId}>{title}</h3>
        <span className={`connection-state ${healthy ? 'is-healthy' : state === 'checking' ? 'is-checking' : 'is-unavailable'}`}>
          <span className="status-dot" aria-hidden="true" />
          {label}
        </span>
      </div>
      <p>{description}</p>
    </article>
  )
}

export default function App() {
  const [status, setStatus] = useState<FoundationStatus | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    void loadFoundationStatus(controller.signal).then((nextStatus) => {
      if (!controller.signal.aborted) setStatus(nextStatus)
    })
    return () => controller.abort()
  }, [attempt])

  function checkAgain() {
    setStatus(null)
    setAttempt((value) => value + 1)
  }

  const connected = status?.api === 'reachable' && status.database === 'connected'

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <header className="site-header">
        <a className="brand" href="#overview" aria-label="TUNZA home">
          <span className="brand-mark"><BrandMark /></span>
          <span><strong>TUNZA</strong><small>by Zest Technologies</small></span>
        </a>
        <nav aria-label="Main navigation">
          <a href="#connection">Connection</a>
          <a href="#scope">Milestone</a>
        </nav>
        <span className="environment-badge">Development foundation</span>
      </header>

      <main id="main-content">
        <section className="hero content-width" id="overview" aria-labelledby="hero-title">
          <div className="eyebrow"><span aria-hidden="true" />Maternal Health SMS Companion</div>
          <h1 id="hero-title">Thoughtful technology.<br /><em>Connected care.</em></h1>
          <p className="hero-description">Building a dependable foundation for maternal health communication, one carefully considered step at a time.</p>
          <div className="hero-meta"><span>Phase 1A</span><span className="meta-divider" />Application foundation</div>
        </section>

        <section className="connection-section content-width" id="connection" aria-labelledby="connection-title">
          <div className="section-heading">
            <div><p className="section-kicker">Built to connect</p><h2 id="connection-title">Foundation connectivity</h2></div>
            <button type="button" className="secondary-button" onClick={checkAgain} disabled={!status}>
              {status ? 'Check connection' : 'Checking connection…'}
            </button>
          </div>
          <div className="connection-grid" role="status" aria-live="polite" aria-atomic="true">
            <ConnectionCard title="Flask API" description="The application service that connects the frontend to the backend." state={status?.api ?? 'checking'} />
            <ConnectionCard title="PostgreSQL" description="A read-only connectivity check against the configured development database." state={status?.database ?? 'checking'} />
          </div>
          <p className="connection-note">
            {!status ? 'Checking the local development services.' : connected ? 'Frontend, API and database are connected. Foundation services are ready.' : 'A development service is unavailable. Check your local API and database setup, then try again.'}
          </p>
        </section>

        <section className="scope-section content-width" id="scope" aria-labelledby="scope-title">
          <div className="scope-symbol" aria-hidden="true">01<span>A</span></div>
          <div>
            <p className="section-kicker">A focused first step</p>
            <h2 id="scope-title">Infrastructure first. Care, thoughtfully next.</h2>
            <p>This milestone establishes the application shell, API and database connection. Authentication, staff dashboards, patient records and SMS workflows belong to later milestones.</p>
            <span className="synthetic-label">Synthetic development data only</span>
          </div>
        </section>
      </main>

      <footer className="site-footer content-width">
        <span><strong>TUNZA</strong> · A Zest Technologies project</span>
        <span>Phase 1A · Local development</span>
      </footer>
    </div>
  )
}
