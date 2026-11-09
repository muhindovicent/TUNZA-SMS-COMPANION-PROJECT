import { useId, useState, type FormEvent } from 'react'
import { ApiError } from '../api/client'
import { activateAccount } from '../api/auth'
import BrandMark from './BrandMark'

interface ActivationPageProps {
  onActivated: () => void
}

const MIN_PASSWORD_LENGTH = 12

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
  return 'Activation could not be completed. Try again.'
}

export default function ActivationPage({ onActivated }: ActivationPageProps) {
  const [token, setToken] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const tokenId = useId()
  const passwordId = useId()
  const confirmId = useId()

  const passwordsMatch = password === confirm
  const passwordStrong = password.length >= MIN_PASSWORD_LENGTH
  const canSubmit = token.trim().length > 0 && passwordStrong && passwordsMatch && !submitting

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canSubmit) return
    setSubmitting(true)
    setError(null)
    try {
      await activateAccount(token.trim(), password)
      setPassword('')
      setConfirm('')
      setToken('')
      onActivated()
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
        <section className="auth-card" aria-labelledby="activation-title">
          <p className="section-kicker">Account activation</p>
          <h1 id="activation-title">Activate your staff account</h1>
          <p className="auth-intro">
            Enter the one-time activation token from your administrator and choose a password. The
            token can be used only once.
          </p>
          <form onSubmit={handleSubmit} noValidate>
            <div className="field">
              <label htmlFor={tokenId}>Activation token</label>
              <input
                id={tokenId}
                name="token"
                type="password"
                autoComplete="one-time-code"
                required
                value={token}
                onChange={(event) => setToken(event.target.value)}
                disabled={submitting}
              />
            </div>
            <div className="field">
              <label htmlFor={passwordId}>New password</label>
              <input
                id={passwordId}
                name="new_password"
                type="password"
                autoComplete="new-password"
                required
                minLength={MIN_PASSWORD_LENGTH}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={submitting}
              />
            </div>
            <div className="field">
              <label htmlFor={confirmId}>Confirm password</label>
              <input
                id={confirmId}
                name="confirm_password"
                type="password"
                autoComplete="new-password"
                required
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
                disabled={submitting}
              />
            </div>
            {password && confirm && !passwordsMatch ? (
              <p className="auth-error" role="alert">
                Passwords must match.
              </p>
            ) : null}
            {password && !passwordStrong ? (
              <p className="auth-error" role="alert">
                Password must be at least {MIN_PASSWORD_LENGTH} characters.
              </p>
            ) : null}
            {error ? (
              <p className="auth-error" role="alert">
                {error}
              </p>
            ) : null}
            <button type="submit" className="primary-button" disabled={!canSubmit}>
              {submitting ? 'Activating…' : 'Activate account'}
            </button>
          </form>
          <p className="auth-footnote">Synthetic development data only</p>
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
