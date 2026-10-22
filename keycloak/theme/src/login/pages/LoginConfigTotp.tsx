import { useEffect, useRef, useState } from 'react'

import { Card, Checkbox, ErrorLine, Field, Input, Pill, QuietButton, QuietLink, Text } from '../ui'
import type { PageProps } from './props'

/**
 * Turning on an authenticator app (login-config-totp.ftl; design 004 left), while an admin allows
 * authenticator apps (Settings; on by default since P25c): the QR code, the code it shows, Turn on.
 * A phone that cannot scan its own screen gets the key in words instead. It is the first second
 * step of someone who chose the app on the passkey page, or one added from Settings.
 */
export default function LoginConfigTotp({ kcContext }: PageProps<'login-config-totp.ftl'>) {
  const { url, totp, mode, messagesPerField, isAppInitiatedAction } = kcContext
  const [busy, setBusy] = useState(false)
  const manual = mode === 'manual'
  const adding = isAppInitiatedAction === true
  const codeInvalid = messagesPerField.existsError('totp')
  const labelInvalid = messagesPerField.existsError('userLabel')
  // Keycloak wants a name only when the person already has an app, to tell the two apart.
  const needsName = totp.otpCredentials.length > 0
  return (
    <Card kcContext={kcContext} title="Set up an authenticator app">
      <form
        action={url.loginAction}
        method="post"
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          const submitter = (event.nativeEvent as SubmitEvent).submitter
          if (submitter?.getAttribute('name') !== 'cancel-aia') setBusy(true)
        }}
      >
        {manual ? (
          <>
            <Text>Enter this key in your authenticator app, then enter the code it shows.</Text>
            <p className="rounded-8 bg-page p-2.5 text-center text-13 tracking-[.06em] break-all text-ink select-all">
              {totp.totpSecretEncoded}
            </p>
          </>
        ) : (
          <>
            <Text>Scan this with an authenticator app, then enter the code it shows.</Text>
            <QrCode png={totp.totpSecretQrCode} />
          </>
        )}
        <Field label="Code">
          <Input
            name="totp"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9]*"
            required
            autoFocus
            invalid={codeInvalid}
            aria-describedby={codeInvalid ? 'totp-error' : undefined}
          />
        </Field>
        {codeInvalid ? <ErrorLine id="totp-error">{messagesPerField.get('totp')}</ErrorLine> : null}
        {needsName ? (
          <>
            <Field label="Name for this app">
              <Input name="userLabel" autoComplete="off" required invalid={labelInvalid} />
            </Field>
            {labelInvalid ? <ErrorLine>{messagesPerField.get('userLabel')}</ErrorLine> : null}
          </>
        ) : null}
        <input type="hidden" name="totpSecret" value={totp.totpSecret} />
        {mode ? <input type="hidden" name="mode" value={mode} /> : null}
        {adding ? (
          <Checkbox name="logout-sessions" value="on" label="Sign out other devices" />
        ) : null}
        <Pill busy={busy}>{busy ? 'Turning on…' : 'Turn on'}</Pill>
        {manual ? (
          <QuietLink href={totp.qrUrl}>Scan a QR code instead</QuietLink>
        ) : (
          <QuietLink href={totp.manualUrl}>Cannot scan? Type a key instead</QuietLink>
        )}
        {adding ? (
          <QuietButton type="submit" name="cancel-aia" value="true" formNoValidate>
            Cancel
          </QuietButton>
        ) : null}
      </form>
    </Card>
  )
}

/**
 * The QR code as 004 left draws it: 140 px inside a 6 px white border and a 1.5 px line. Keycloak's
 * image carries a wide white margin of its own, so the code is cut out of it and drawn to fill the
 * box (pixel for pixel, never smoothed); the white border and the card around it are its margin.
 */
function QrCode({ png }: { png: string }) {
  const canvas = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const image = new Image()
    image.onload = () => {
      const target = canvas.current?.getContext('2d')
      const source = document.createElement('canvas').getContext('2d', { willReadFrequently: true })
      if (!target || !source) return
      const { width, height } = image
      source.canvas.width = width
      source.canvas.height = height
      source.drawImage(image, 0, 0)
      const { data } = source.getImageData(0, 0, width, height)
      let [left, top, right, bottom] = [width, height, -1, -1]
      for (let y = 0; y < height; y += 1) {
        for (let x = 0; x < width; x += 1) {
          if (data[(y * width + x) * 4] < 128) {
            left = Math.min(left, x)
            right = Math.max(right, x)
            top = Math.min(top, y)
            bottom = Math.max(bottom, y)
          }
        }
      }
      if (right < 0) [left, top, right, bottom] = [0, 0, width - 1, height - 1]
      target.imageSmoothingEnabled = false
      target.fillStyle = '#ffffff'
      target.fillRect(0, 0, target.canvas.width, target.canvas.height)
      target.drawImage(
        image,
        left,
        top,
        right - left + 1,
        bottom - top + 1,
        0,
        0,
        target.canvas.width,
        target.canvas.height,
      )
    }
    image.src = `data:image/png;base64,${png}`
  }, [png])
  return (
    <canvas
      ref={canvas}
      width={280}
      height={280}
      role="img"
      aria-label="QR code for your authenticator app"
      className="box-content size-[140px] self-center rounded-10 border-[6px] border-card-white bg-card-white outline-[1.5px] outline-line outline-solid"
    />
  )
}
