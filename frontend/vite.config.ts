import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// The API and the inbound webhook are Django's; in development Vite hands them on, so the browser
// talks to one origin and the session cookie simply works. P25a: so is the sign-in, Keycloak, which
// production serves under /auth on the portal's own address (https://portal.thesclu.org/auth);
// here it is the development Keycloak on 8080 (keycloak/compose.yml), told its address is
// http://localhost:5173/auth, so every sign-in page is on this one origin as it will be live.
const backend = 'http://127.0.0.1:8000'
const keycloak = 'http://127.0.0.1:8080'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // `ws` hands team chat's websocket (/api/core/chat/socket) on as well (P22).
    proxy: {
      '/api': { target: backend, ws: true },
      '/webhooks': backend,
      // The pattern matches the /auth prefix itself, never an SPA path that merely starts with it.
      '^/auth/': { target: keycloak, xfwd: true },
    },
  },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
})
