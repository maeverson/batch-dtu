import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { AuthProvider } from 'react-oidc-context'
import App from './App'
import { oidcConfig } from './config'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthProvider
      authority={oidcConfig.authority}
      client_id={oidcConfig.client_id}
      redirect_uri={oidcConfig.redirect_uri}
      scope="openid profile email"
      onSigninCallback={() => {
        // limpa `?code=&state=` da URL depois do redirect de volta do Keycloak
        window.history.replaceState({}, document.title, window.location.pathname)
      }}
    >
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </AuthProvider>
  </StrictMode>,
)
