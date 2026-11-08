import { useEffect, useId, useState } from 'react'
import { loadFoundationStatus } from '../api/health'
import type { FoundationStatus } from '../api/health'
import { logout } from '../api/auth'
import type { StaffIdentity } from '../api/auth'
import BrandMark from './BrandMark'

interface StaffShellProps {
  identity: StaffIdentity
  onSignedOut: () => void
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
  const label = {
    reachable: 'Reachable',
    connected: 'Connected',
    unavailable: 'Unavailable',
    checking: 'Checking…',
  }[state]

  return (
    <article className="connection-card" aria-labelledby={titleId}>
      <div className="card-topline">
        <h3 id={titleId}>{title}</h3>
        <span
          className={`connection-state ${
            healthy ? 'is-healthy' : state === 'checking' ? 'is-checking' : 'is-unavailable'
          }`}
        >
          <span className="status-dot" aria-hidden="true" />
          {label}
        </span>
      </div>
      <p>{description}</p>
    </article>
  )
}

function uniqueCodes(values: (string | null)[]): string[] {
  return [...new Set(values.filter((value): value is string => Boolean(value)))]
}

export default function StaffShell({ identity, onSignedOut }: StaffShellProps) {
  const [status, setStatus] = useState<FoundationStatus | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [signingOut, setSigningOut] = useState(false)
  const [signOutError, setSignOutError] = useState<string | null>(null)

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

  async function handleSignOut() {
    if (signingOut) return
    setSigningOut(true)
    setSignOutError(null)
    try {
      await logout()
      onSignedOut()
    } catch {
      setSignOutError('Sign-out could not be completed. Try again.')
    } finally {
      setSigningOut(false)
    }
  }

  const connected = status?.api === 'reachable' && status.database === 'connected'
  const roleCodes = uniqueCodes(identity.bindings.map((binding) => binding.role_code))
  const facilityCodes = uniqueCodes(identity.facilities.map((facility) => facility.code))

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <header className="site-header">
        <a className="brand" href="#overview" aria-label="TUNZA home">
          <span className="brand-mark">
            <BrandMark />
          </span>
          <span>
            <strong>TUNZA</strong>
            <small>by Zest Technologies</small>
          </span>
        </a>
        <nav aria-label="Main navigation">
          <a href="#connection">Connection</a>
          <a href="#scope">Milestone</a>
        </nav>
        <div className="user-cluster">
          <span className="user-summary">
            <span className="user-name">{identity.display_name}</span>
            <span className="badge-row">
              {roleCodes.map((code) => (
                <span className="role-badge" key={`role-${code}`}>
                  {code}
                </span>
              ))}
              {facilityCodes.map((code) => (
                <span className="facility-badge" key={`facility-${code}`}>
                  {code}
                </span>
              ))}
            </span>
          </span>
          <button
            type="button"
            className="secondary-button"
            onClick={handleSignOut}
            disabled={signingOut}
          >
            {signingOut ? 'Signing out…' : 'Sign out'}
          </button>
        </div>
        {signOutError ? (
          <span className="signout-error" role="alert">
            {signOutError}
          </span>
        ) : null}
      </header>

      <main id="main-content">
        <section className="hero content-width" id="overview" aria-labelledby="hero-title">
          <div className="eyebrow">
            <span aria-hidden="true" />
            Maternal Health SMS Companion
          </div>
          <h1 id="hero-title">
            Thoughtful technology.
            <br />
            <em>Connected care.</em>
          </h1>
          <p className="hero-description">
            Building a dependable foundation for maternal health communication, one carefully
            considered step at a time.
          </p>
          <div className="hero-meta">
            <span>Phase 1B-1</span>
            <span className="meta-divider" />
            Staff authentication and facility access
          </div>
        </section>

        <section
          className="connection-section content-width"
          id="connection"
          aria-labelledby="connection-title"
        >
          <div className="section-heading">
            <div>
              <p className="section-kicker">Built to connect</p>
              <h2 id="connection-title">Foundation connectivity</h2>
            </div>
            <button
              type="button"
              className="secondary-button"
              onClick={checkAgain}
              disabled={!status}
            >
              {status ? 'Check connection' : 'Checking connection…'}
            </button>
          </div>
          <div className="connection-grid" role="status" aria-live="polite" aria-atomic="true">
            <ConnectionCard
              title="Flask API"
              description="The application service that connects the frontend to the backend."
              state={status?.api ?? 'checking'}
            />
            <ConnectionCard
              title="PostgreSQL"
              description="A read-only connectivity check against the configured development database."
              state={status?.database ?? 'checking'}
            />
          </div>
          <p className="connection-note">
            {!status
              ? 'Checking the local development services.'
              : connected
                ? 'Frontend, API and database are connected. Foundation services are ready.'
                : 'A development service is unavailable. Check your local API and database setup, then try again.'}
          </p>
        </section>

        <section className="scope-section content-width" id="scope" aria-labelledby="scope-title">
          <div className="scope-symbol" aria-hidden="true">
            01<span>B</span>
          </div>
          <div>
            <p className="section-kicker">A focused next step</p>
            <h2 id="scope-title">Secure sign-in first. Care, thoughtfully next.</h2>
            <p>
              This milestone establishes staff login, server-side sessions, role-based permissions
              and facility-scoped access. Patient records, staff dashboards and SMS workflows belong
              to later milestones.
            </p>
            <span className="synthetic-label">Synthetic development data only</span>
          </div>
        </section>
      </main>

      <footer className="site-footer content-width">
        <span>
          <strong>TUNZA</strong> · A Zest Technologies project
        </span>
        <span>Phase 1B-1 · Local development</span>
      </footer>
    </div>
  )
}
