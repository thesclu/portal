import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { primeCsrf } from './api/client'
import { App } from './app/providers'
import './design/tokens.css'

// The CSRF cookie has to exist before the first write, which may be the sign-in itself.
void primeCsrf().finally(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
})
