import { createGetKcContextMock } from 'keycloakify/login/KcContext'

import { kcEnvDefaults, themeNames } from '../kc.gen'
import type { KcContextExtension, KcContextExtensionPerPage } from './KcContext'

export const { getKcContextMock } = createGetKcContextMock({
  kcContextExtension: {
    themeName: themeNames[0],
    properties: { ...kcEnvDefaults },
  } satisfies KcContextExtension,
  kcContextExtensionPerPage: {} satisfies KcContextExtensionPerPage,
  overrides: { realm: { displayName: 'SCLU Portal' } },
  overridesPerPage: {},
})
