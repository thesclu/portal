import { i18nBuilder } from 'keycloakify/login'

import type { ThemeName } from '../kc.gen'

/**
 * The words Keycloak writes into a page itself (an error under a field, a notice), in the house's
 * voice: plain, short, sentence case. Keycloakify writes these into the theme's
 * messages_en.properties, so the server uses them too. No ASCII apostrophes: Keycloak reads these
 * as message formats, where one quotes text. Every other word on the pages is in the pages.
 */
const { useI18n, ofTypeI18n } = i18nBuilder
  .withThemeName<ThemeName>()
  .withCustomTranslations({
    en: {
      // Signing in (001). Every refusal reads the same, so the page never says whether an
      // address has an account. A lockout after wrong passwords too: telling someone they are
      // locked out would confirm the account exists (QUESTIONS 66).
      invalidUserMessage: 'Email or password is incorrect.',
      invalidUsernameMessage: 'Email or password is incorrect.',
      invalidUsernameOrEmailMessage: 'Email or password is incorrect.',
      invalidPasswordMessage: 'Email or password is incorrect.',
      accountTemporarilyDisabledMessage: 'Email or password is incorrect.',
      accountPermanentlyDisabledMessage:
        'Too many sign-in attempts. Ask an admin to unlock your account.',
      accountDisabledMessage: 'This account is turned off. Ask an admin for help.',
      missingUsernameMessage: 'Enter your email.',
      missingPasswordMessage: 'Enter your password.',
      expiredCodeMessage: 'Your sign-in timed out. Sign in again.',
      loginTimeout: 'Your sign-in timed out. Start again.',
      cookieNotFoundMessage:
        'This browser blocked the sign-in. Allow cookies for this site and sign in again.',
      // Forgot password (002, 003).
      emailSentMessage: 'If an account uses that email, a reset link is on its way.',
      emailSendErrorMessage: 'The email could not be sent. Try again later.',
      invalidEmailMessage: 'Enter a valid email.',
      // A new password (002 left, 003 right).
      invalidPasswordMinLengthMessage: 'Use at least {0} characters.',
      invalidPasswordMaxLengthMessage: 'Use at most {0} characters.',
      invalidPasswordNotUsernameMessage: 'Choose a password that is not your email.',
      invalidPasswordNotContainsUsernameMessage:
        'Choose a password that does not contain your email.',
      invalidPasswordNotEmailMessage: 'Choose a password that is not your email.',
      invalidPasswordBlacklistedMessage: 'That password is too common. Choose another.',
      invalidPasswordHistoryMessage: 'Choose a password you have not used before.',
      notMatchPasswordMessage: 'The two passwords differ.',
      invalidPasswordConfirmMessage: 'The two passwords differ.',
      // The second step (004).
      // A lockout on the code page reads as a wrong code, as on the password page (QUESTIONS 66).
      invalidTotpMessage: 'That code is not right.',
      accountTemporarilyDisabledMessageTotp: 'That code is not right.',
      accountPermanentlyDisabledMessageTotp:
        'Too many attempts. Ask an admin to unlock your account.',
      'recovery-codes-error-invalid': 'That recovery code is not right.',
      'webauthn-unsupported-browser-text': 'This browser cannot use passkeys. Try another browser.',
      'webauthn-error-registration': 'The passkey was not added.',
      'webauthn-error-api-get': 'The passkey was not used.',
      'webauthn-error-different-user': 'That passkey belongs to someone else.',
      'webauthn-error-auth-verification': 'The passkey check failed.',
      'webauthn-error-register-verification': 'The passkey could not be added.',
      'webauthn-error-user-not-found': 'That passkey is not registered here.',
      // Keycloak 26.8's own words for a passkey prompt that went wrong (not in Keycloakify's set).
      'webauthn-error-api-not-allowed':
        'The passkey was not used. The prompt was closed or timed out.',
      'webauthn-error-api-invalid-state': 'This passkey is already added.',
      'webauthn-error-api-security': 'The passkey could not be used on this page.',
      'webauthn-error-user-not-verified': 'Your device did not confirm it is you.',
      successLogout: 'You are signed out.',
      reauthenticate: 'Enter your password again to continue.',
      // After an emailed link (005 right) and the pages around it.
      accountUpdatedMessage: 'Your account is up to date.',
      expiredActionMessage: 'This link has expired or was already used.',
      expiredActionTokenNoSessionMessage: 'This link has expired.',
      expiredActionTokenSessionExistsMessage: 'This link has expired. Start again.',
      invalidCodeMessage: 'Something went wrong. Go back to the portal and sign in again.',
      staleCodeMessage: 'This page is out of date. Go back to the portal and sign in again.',
    },
  })
  .build()

type I18n = typeof ofTypeI18n

export { useI18n, type I18n }
