import { ChevronRight } from '../icons'
import { Card, Text } from '../ui'
import { wayOf, type Way } from './OtherWays'
import type { PageProps } from './props'

const NAME: Record<Way, string> = {
  passkey: 'Passkey',
  recovery: 'Recovery code',
  app: 'Authenticator app',
  other: 'Another way',
}

const HELP: Record<Way, string> = {
  passkey: 'Your fingerprint, face or phone',
  recovery: 'One of the codes you saved',
  app: 'A code from the app',
  other: '',
}

/**
 * Try another way (select-authenticator.ftl): the ways this person can confirm it is them, as a
 * short list of raised rows in the card's style. Each row posts Keycloak's choice.
 */
export default function SelectAuthenticator({ kcContext }: PageProps<'select-authenticator.ftl'>) {
  const { url, auth } = kcContext
  return (
    <Card kcContext={kcContext} title="Choose a way to sign in">
      <Text>Choose how to confirm it’s you.</Text>
      <ul className="flex flex-col gap-2">
        {auth.authenticationSelections.map((selection) => {
          const way = wayOf(selection)
          return (
            <li key={selection.authExecId}>
              <form action={url.loginAction} method="post">
                <input type="hidden" name="authenticationExecution" value={selection.authExecId} />
                <button
                  type="submit"
                  className="flex w-full items-center gap-3 rounded-12 bg-card-white px-3.5 py-2.5 text-left shadow-field transition-shadow duration-[120ms] hover:shadow-primary"
                >
                  <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <span className="text-13 font-medium text-ink">{NAME[way]}</span>
                    {HELP[way] ? <span className="text-12 text-muted">{HELP[way]}</span> : null}
                  </span>
                  <ChevronRight />
                </button>
              </form>
            </li>
          )
        })}
      </ul>
    </Card>
  )
}
