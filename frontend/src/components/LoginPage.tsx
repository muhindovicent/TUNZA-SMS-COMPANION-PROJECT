import { useId, useState, type FormEvent } from 'react'
import { ApiError } from '../api/client'
import { login } from '../api/auth'
import BrandMark from './BrandMark'

interface LoginPageProps {
  onSignedIn: () => void
  notice?: string | null
}

function messageFor(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) {
      return 'Your session could not be verified. Refresh the page and try again.'
    }
    if (error.code === 'network_error') {
      return 'Unable to reach the server. Check your connection and try again.'
    }
    return error.message
  }
  return 'Sign-in could not be completed. Try again.'
}

export default function LoginPage({ onSignedIn, notice }: LoginPageProps) {
  const [identifier, setIdentifier] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const identifierId = useId()
  const passwordId = useId()

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting) return
    setSubmitting(true)
    setError(null)
    try {
      await login(identifier.trim(), password)
      setPassword('')
      onSignedIn()
    } catch (caught) {
      setError(messageFor(caught))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="auth-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <header className="auth-header content-width">
        <span className="brand" aria-label="TUNZA">
          <span className="brand-mark">
            <BrandMark />
          </span>
          <span>
            <strong>TUNZA</strong>
            <small>by Zest Technologies</small>
          </span>
        </span>
        <span className="environment-badge">Development foundation</span>
      </header>

      <main id="main-content" className="auth-main">
        <section className="auth-card" aria-labelledby="sign-in-title">
          <p className="section-kicker">Authorized staff</p>
          <h1 id="sign-in-title">Staff sign in</h1>
          <p className="auth-intro">
            Access is limited to approved TUNZA staff. Every sign-in attempt is recorded.
          </p>
          {notice ? (
            <p className="auth-notice" role="status">
              {notice}
            </p>
          ) : null}
          <form onSubmit={handleSubmit} noValidate>
            <div className="field">
              <label htmlFor={identifierId}>Login identifier</label>
              <input
                id={identifierId}
                name="login_identifier"
                type="text"
                autoComplete="username"
                required
                value={identifier}
                onChange={(event) => setIdentifier(event.target.value)}
                disabled={submitting}
              />
            </div>
            <div className="field">
              <label htmlFor={passwordId}>Password</label>
              <input
                id={passwordId}
                name="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={submitting}
              />
            </div>
            {error ? (
              <p className="auth-error" role="alert">
                {error}
              </p>
            ) : null}
            <button
              type="submit"
              className="primary-button"
              disabled={submitting || !identifier.trim() || !password}
            >
              {submitting ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
          <p className="auth-footnote">
            Synthetic development data only · <a href="#activate">Activate account</a>
          </p>
        </section>
      </main>

      <footer className="site-footer content-width">
        <span>
          <strong>TUNZA</strong> · A Zest Technologies project
        </span>
        <span>Local development</span>
      </footer>
    </div>
  )
}
