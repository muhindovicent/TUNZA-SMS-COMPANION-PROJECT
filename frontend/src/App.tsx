import { useCallback, useEffect, useState } from 'react'
import { loadIdentity } from './api/auth'
import type { StaffIdentity } from './api/auth'
import { ApiError } from './api/client'
import ActivationPage from './components/ActivationPage'
import LoginPage from './components/LoginPage'
import StaffShell from './components/StaffShell'

type SessionPhase = 'loading' | 'signed-out' | 'signed-in'

function isActivationRoute(): boolean {
  return window.location.hash.startsWith('#activate')
}

function bootNoticeFor(error: unknown): string {
  if (error instanceof ApiError && error.code === 'network_error') {
    return 'Unable to reach the server to verify your session.'
  }
  return 'Your session could not be verified. Sign in again.'
}

export default function App() {
  const [phase, setPhase] = useState<SessionPhase>('loading')
  const [identity, setIdentity] = useState<StaffIdentity | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [activationRoute, setActivationRoute] = useState<boolean>(() => isActivationRoute())

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const next = await loadIdentity(signal)
      if (signal?.aborted) return
      setIdentity(next)
      setPhase(next ? 'signed-in' : 'signed-out')
      if (next) setNotice(null)
    } catch (error) {
      if (signal?.aborted) return
      setIdentity(null)
      setPhase('signed-out')
      setNotice(bootNoticeFor(error))
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    const task = Promise.resolve().then(() => refresh(controller.signal))
    return () => {
      controller.abort()
      void task
    }
  }, [refresh])

  useEffect(() => {
    function handleHashChange() {
      setActivationRoute(isActivationRoute())
    }
    window.addEventListener('hashchange', handleHashChange)
    return () => window.removeEventListener('hashchange', handleHashChange)
  }, [])

  function handleSignedIn() {
    setNotice(null)
    setActivationRoute(false)
    void refresh()
  }

  function handleSignedOut() {
    setIdentity(null)
    setPhase('signed-out')
    setNotice(null)
  }

  function handleActivated() {
    window.location.hash = ''
    setActivationRoute(false)
    setNotice('Account activated. Sign in with your new password.')
  }

  if (phase === 'loading') {
    return (
      <div className="loading-shell" role="status" aria-live="polite">
        <p>Checking your session…</p>
      </div>
    )
  }

  if (phase === 'signed-out' || !identity) {
    if (activationRoute) {
      return <ActivationPage onActivated={handleActivated} />
    }
    return <LoginPage onSignedIn={handleSignedIn} notice={notice} />
  }

  return <StaffShell identity={identity} onSignedOut={handleSignedOut} />
}
