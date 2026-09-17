// Tudo por variável de ambiente (`.env.example`), mesmo padrão da Platform
// API (`src/platform_api/config.py`): nenhum default aponta para produção.

export const apiBaseUrl: string =
  import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export const oidcConfig = {
  authority: import.meta.env.VITE_OIDC_AUTHORITY ?? 'http://localhost:8080/realms/batch-dtu',
  client_id: import.meta.env.VITE_OIDC_CLIENT_ID ?? 'back-office',
  redirect_uri: import.meta.env.VITE_OIDC_REDIRECT_URI ?? 'http://localhost:5173/callback',
  // O default serve ao Keycloak local, onde a audiência `platform-api` vem de
  // um protocol mapper do realm. **No Entra ID isso não basta**: sem pedir o
  // escopo da API (`api://<client-id-da-api>/access_as_user`), o access token
  // sai com `aud` do Microsoft Graph e a Platform API recusa TODA chamada com
  // 401 — com o login funcionando normalmente, que é o que torna esse erro
  // caro de diagnosticar. Ver `deploy/back-office/`.
  scope: import.meta.env.VITE_OIDC_SCOPE ?? 'openid profile email',
}
