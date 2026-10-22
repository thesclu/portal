/**
 * The portal's own design values, brought into the theme before every build: its tokens
 * (frontend/src/design/tokens.css, less the Tailwind import, which the theme's stylesheet makes),
 * its self-hosted fonts (DM Sans and Libre Franklin; frontend/public/fonts) and its round logo
 * (frontend/public/brand/sclu-logo.png), and for the email theme the SCLU letters
 * (frontend/public/brand/sclu-letters-blue.png). Copied, never edited, so a change to the portal's
 * tokens reaches the sign-in pages at the next `just keycloak-theme`. The copies are not committed
 * (keycloak/theme/.gitignore).
 */
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const theme = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const frontend = resolve(theme, '../../frontend')
const out = join(theme, 'src/portal')
mkdirSync(join(out, 'fonts'), { recursive: true })

const tokens = readFileSync(join(frontend, 'src/design/tokens.css'), 'utf8')
const withoutImport = tokens.replace(/^@import ['"]tailwindcss['"];\s*$/m, '')
if (withoutImport === tokens)
  throw new Error('tokens.css no longer starts with the Tailwind import')
writeFileSync(join(out, 'tokens.css'), withoutImport)

// Only the two faces the sign-in pages draw; the URLs made relative so Vite bundles the files.
const fonts = readFileSync(join(frontend, 'public/fonts/fonts.css'), 'utf8')
  .split('\n')
  .filter((rule) => /font-family:"(DM Sans|Libre Franklin)"/.test(rule))
for (const [, file] of fonts.join('\n').matchAll(/url\(\/fonts\/([^)]+)\)/g)) {
  copyFileSync(join(frontend, 'public/fonts', file), join(out, 'fonts', file))
}
writeFileSync(
  join(out, 'fonts.css'),
  fonts.join('\n').replaceAll('url(/fonts/', 'url(./fonts/') + '\n',
)

copyFileSync(join(frontend, 'public/brand/sclu-logo.png'), join(out, 'sclu-logo.png'))

// The email's SCLU letters (emails.py's LOGO_PATH), served from the email theme's resources.
const emailImg = join(theme, 'src/email/resources/img')
mkdirSync(emailImg, { recursive: true })
copyFileSync(
  join(frontend, 'public/brand/sclu-letters-blue.png'),
  join(emailImg, 'sclu-letters-blue.png'),
)
console.log(`portal-assets: tokens, ${fonts.length} font faces and the logos copied in`)
