import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { KcPage } from './kc.gen'

// `npm run dev` shows a page from its mock context: set VITE_KC_PAGE (e.g. login.ftl).
if (import.meta.env.DEV && window.kcContext === undefined) {
  const { getKcContextMock } = await import('./login/mock')
  window.kcContext = getKcContextMock({
    pageId: (import.meta.env.VITE_KC_PAGE ?? 'login.ftl') as 'login.ftl',
    overrides: {},
  })
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>{window.kcContext ? <KcPage kcContext={window.kcContext} /> : null}</StrictMode>,
)
