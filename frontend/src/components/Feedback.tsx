import type { ReactNode } from 'react'

export function ErrorBanner({ error }: { error: unknown }) {
  if (!error) return null
  const mensagem = error instanceof Error ? error.message : String(error)
  return <div className="error-banner">{mensagem}</div>
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="muted">
      <span className="spin" aria-hidden>
        ⟳
      </span>{' '}
      {label ?? 'Carregando…'}
    </span>
  )
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="empty-state">{children}</div>
}
