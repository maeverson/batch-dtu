import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from 'react-oidc-context'
import { useMe } from '../api/hooks'
import { isAdmin, roleLabel } from '../rbac'

export function Layout() {
  const auth = useAuth()
  const { data: me } = useMe()

  return (
    <div className="app-shell">
      <nav className="app-nav">
        <div className="app-nav__brand">
          Batch DTU
          <small>Back Office</small>
        </div>
        <NavLink to="/catalogo" className={({ isActive }) => (isActive ? 'active' : '')}>
          Catálogo
        </NavLink>
        <NavLink to="/execucoes" className={({ isActive }) => (isActive ? 'active' : '')}>
          Execuções
        </NavLink>
        <NavLink to="/mudancas" className={({ isActive }) => (isActive ? 'active' : '')}>
          Mudanças de agenda
        </NavLink>
        {isAdmin(me) && (
          <NavLink to="/admin" className={({ isActive }) => (isActive ? 'active' : '')}>
            Administração
          </NavLink>
        )}
        <div className="app-nav__spacer" />
        {/* Com um deploy por ambiente, qual ambiente esta tela opera é a
            informação que evita o operador achar que está em UAT quando está
            em PROD. Fica ao lado do usuário, sempre visível. */}
        {me && (
          <div className={`env-tag env-tag--${me.environment.toLowerCase()}`} title={`host ${me.host}`}>
            {me.environment}
          </div>
        )}
        <div className="topbar-user">
          <div>
            <strong>{me?.display_name ?? me?.subject ?? auth.user?.profile.preferred_username}</strong>
            <div>{me?.roles.map(roleLabel).join(', ') || '—'}</div>
          </div>
        </div>
        <button onClick={() => auth.removeUser().then(() => auth.signoutRedirect())}>
          Sair
        </button>
      </nav>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  )
}
