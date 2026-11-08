import { useCallback, useEffect, useState } from 'react'
import { loadIdentity } from './api/auth'
import type { StaffIdentity } from './api/auth'
import { ApiError } from './api/client'
import LoginPage from './components/LoginPage'
import StaffShell from './components/StaffShell'

type SessionPhase = 'loading' | 'signed-out' | 'signed-in'

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

  function handleSignedIn() {
    setNotice(null)
    void refresh()
  }

  function handleSignedOut() {
    setIdentity(null)
    setPhase('signed-out')
    setNotice(null)
  }

  if (phase === 'loading') {
    return (
      <div className="loading-shell" role="status" aria-live="polite">
        <p>Checking your session…</p>
      </div>
    )
  }

  if (phase === 'signed-out' || !identity) {
    return <LoginPage onSignedIn={handleSignedIn} notice={notice} />
  }

  return <StaffShell identity={identity} onSignedOut={handleSignedOut} />
}
