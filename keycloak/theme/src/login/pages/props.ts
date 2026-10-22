import type { I18n } from '../i18n'
import type { KcContext } from '../KcContext'

/** What KcPage hands each page: Keycloak's context for that page, and the words. */
export type PageProps<PageId extends KcContext['pageId']> = {
  kcContext: Extract<KcContext, { pageId: PageId }>
  i18n: I18n
}
