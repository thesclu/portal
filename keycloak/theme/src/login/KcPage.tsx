import './styles.css'

import DefaultPage from 'keycloakify/login/DefaultPage'
import type { TemplateProps } from 'keycloakify/login/TemplateProps'
import { lazy } from 'react'

import type { KcContext } from './KcContext'
import { useI18n, type I18n } from './i18n'
import ErrorPage from './pages/ErrorPage'
import Info from './pages/Info'
import Login from './pages/Login'
import LoginConfigTotp from './pages/LoginConfigTotp'
import LoginOtp from './pages/LoginOtp'
import LoginRecoveryAuthnCodeConfig from './pages/LoginRecoveryAuthnCodeConfig'
import LoginRecoveryAuthnCodeInput from './pages/LoginRecoveryAuthnCodeInput'
import LoginResetPassword from './pages/LoginResetPassword'
import LoginUpdatePassword from './pages/LoginUpdatePassword'
import SelectAuthenticator from './pages/SelectAuthenticator'
import { DeleteCredential, LoginPageExpired, LogoutConfirm, WebauthnError } from './pages/Small'
import WebauthnAuthenticate from './pages/WebauthnAuthenticate'
import WebauthnRegister from './pages/WebauthnRegister'
import { Card, Message, Text } from './ui'

const UserProfileFormFields = lazy(() => import('keycloakify/login/UserProfileFormFields'))

/**
 * Every page Keycloak can show the portal's people, drawn as the portal draws its signed-out
 * screens (design 001–005). A page the realm never reaches (registration, terms, identity
 * providers: all off) falls back to Keycloakify's own markup inside the same card.
 */
export default function KcPage({ kcContext }: { kcContext: KcContext }) {
  const { i18n } = useI18n({ kcContext })
  switch (kcContext.pageId) {
    case 'login.ftl':
      return <Login kcContext={kcContext} i18n={i18n} />
    case 'login-reset-password.ftl':
      return <LoginResetPassword kcContext={kcContext} i18n={i18n} />
    case 'login-update-password.ftl':
      return <LoginUpdatePassword kcContext={kcContext} i18n={i18n} />
    case 'webauthn-register.ftl':
      return <WebauthnRegister kcContext={kcContext} i18n={i18n} />
    case 'webauthn-authenticate.ftl':
      return <WebauthnAuthenticate kcContext={kcContext} i18n={i18n} />
    case 'webauthn-error.ftl':
      return <WebauthnError kcContext={kcContext} i18n={i18n} />
    case 'select-authenticator.ftl':
      return <SelectAuthenticator kcContext={kcContext} i18n={i18n} />
    case 'login-recovery-authn-code-input.ftl':
      return <LoginRecoveryAuthnCodeInput kcContext={kcContext} i18n={i18n} />
    case 'login-recovery-authn-code-config.ftl':
      return <LoginRecoveryAuthnCodeConfig kcContext={kcContext} i18n={i18n} />
    case 'login-config-totp.ftl':
      return <LoginConfigTotp kcContext={kcContext} i18n={i18n} />
    case 'login-otp.ftl':
      return <LoginOtp kcContext={kcContext} i18n={i18n} />
    case 'info.ftl':
      return <Info kcContext={kcContext} i18n={i18n} />
    case 'error.ftl':
      return <ErrorPage kcContext={kcContext} i18n={i18n} />
    case 'login-page-expired.ftl':
      return <LoginPageExpired kcContext={kcContext} i18n={i18n} />
    case 'delete-credential.ftl':
      return <DeleteCredential kcContext={kcContext} i18n={i18n} />
    case 'logout-confirm.ftl':
      return <LogoutConfirm kcContext={kcContext} i18n={i18n} />
    default:
      return (
        <DefaultPage
          kcContext={kcContext}
          i18n={i18n}
          Template={FallbackTemplate}
          doUseDefaultCss={false}
          UserProfileFormFields={UserProfileFormFields}
          doMakeUserConfirmPassword={true}
        />
      )
  }
}

/** Keycloakify's page frame for the pages above do not draw: the card, its title line, the page. */
function FallbackTemplate({
  kcContext,
  headerNode,
  displayMessage = true,
  children,
}: TemplateProps<KcContext, I18n>) {
  const { message } = kcContext
  return (
    <Card kcContext={kcContext} title="SCLU Portal">
      <Text ink>{headerNode}</Text>
      {displayMessage && message ? (
        <Text role={message.type === 'error' ? 'alert' : 'status'}>
          <Message summary={message.summary} />
        </Text>
      ) : null}
      <div className="flex flex-col gap-4 text-13 text-ink">{children}</div>
    </Card>
  )
}
