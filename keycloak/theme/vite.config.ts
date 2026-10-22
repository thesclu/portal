import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { keycloakify } from 'keycloakify/vite-plugin'
import { defineConfig } from 'vite'

// P25a: the sign-in pages (Keycloak's login theme) and the mail Keycloak sends (its email theme,
// plain FreeMarker in src/email), one theme named "sclu", one jar for Keycloak 26 and later.
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    keycloakify({
      themeName: 'sclu',
      accountThemeImplementation: 'none',
      keycloakVersionTargets: {
        '22-to-25': false,
        'all-other-versions': 'sclu-keycloak-theme.jar',
      },
    }),
  ],
})
