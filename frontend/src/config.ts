// Tudo por variável de ambiente (`.env.example`), mesmo padrão da Platform
// API (`src/platform_api/config.py`): nenhum default aponta para produção.

export const apiBaseUrl: string =
  import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export const oidcConfig = {
  authority: import.meta.env.VITE_OIDC_AUTHORITY ?? 'http://localhost:8080/realms/batch-dtu',
  client_id: import.meta.env.VITE_OIDC_CLIENT_ID ?? 'back-office',
  redirect_uri: import.meta.env.VITE_OIDC_REDIRECT_URI ?? 'http://localhost:5173/callback',
}
