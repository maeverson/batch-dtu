import type { ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from 'react-oidc-context'
import { Layout } from './components/Layout'
import { CatalogPage } from './pages/CatalogPage'
import { JobDetailPage } from './pages/JobDetailPage'
import { ExecutionsPage } from './pages/ExecutionsPage'
import { ExecutionDetailPage } from './pages/ExecutionDetailPage'
import { ChangeRequestsPage } from './pages/ChangeRequestsPage'
import { AdminPage } from './pages/AdminPage'

// Nenhuma ação operacional fora da API (CLAUDE.md do módulo): esta app só
// fala com a Platform API por HTTP — nunca SSH, nunca link de instrução.
export default function App() {
  const auth = useAuth()

  if (auth.isLoading) {
    return <CenteredMessage>Conectando ao Entra ID…</CenteredMessage>
  }

  if (auth.error) {
    return (
      <CenteredMessage>
        Falha na autenticação: {auth.error.message}
        <div style={{ marginTop: 12 }}>
          <button className="primary" onClick={() => auth.signinRedirect()}>
            Tentar novamente
          </button>
        </div>
      </CenteredMessage>
    )
  }

  if (!auth.isAuthenticated) {
    return (
      <CenteredMessage>
        <h1>Batch DTU — Back Office</h1>
        <p className="muted">Entre com sua conta corporativa para continuar.</p>
        <div style={{ marginTop: 16 }}>
          <button className="primary" onClick={() => auth.signinRedirect()}>
            Entrar
          </button>
        </div>
      </CenteredMessage>
    )
  }

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/catalogo" replace />} />
        <Route path="/catalogo" element={<CatalogPage />} />
        <Route path="/jobs/:jobId" element={<JobDetailPage />} />
        <Route path="/execucoes" element={<ExecutionsPage />} />
        <Route path="/execucoes/:executionId" element={<ExecutionDetailPage />} />
        <Route path="/mudancas" element={<ChangeRequestsPage />} />
        <Route path="/admin" element={<AdminPage />} />
        <Route path="/callback" element={<Navigate to="/catalogo" replace />} />
        <Route path="*" element={<Navigate to="/catalogo" replace />} />
      </Route>
    </Routes>
  )
}

function CenteredMessage({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        minHeight: '100svh',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        textAlign: 'center',
        padding: 24,
      }}
    >
      {children}
    </div>
  )
}
