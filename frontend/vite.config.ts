import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Portas fixas de propósito: são as mesmas registradas em `redirectUris` /
// `webOrigins` do cliente `back-office` no realm Keycloak local
// (docker/keycloak/realm-batch-dtu.json) e no CORS da Platform API
// (src/platform_api/config.py, CORS_ALLOW_ORIGINS).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
  },
  preview: {
    port: 3001,
    strictPort: true,
  },
})
