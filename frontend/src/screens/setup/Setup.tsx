import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Navigate, useNavigate, useParams } from 'react-router'

import { api, type Schemas } from '../../api/client'
import { $api } from '../../api/query'
import { useMe, useSignOut, type Me } from '../../app/session'
import { unwrap } from '../../components/ActionDialog'
import { Empty, Loading } from '../../components/States'
import { initialsOf } from '../../lib/presence'
import {
  articleFor,
  firstName,
  isSetupStep,
  nextStep,
  previousStep,
  runSteps,
  type SetupStep,
} from '../../lib/setup'
import { signatureSrc, useSignature } from '../settings/Signature'
import { AgreementStep } from './AgreementStep'
import { Done } from './Done'
import { SETUP_FIELD, SETUP_LABEL, SetupActions, SetupFrame, SetupTitle } from './parts'
import { SignatureStep } from './SignatureStep'

type SetupData = Schemas['NewUserSetupOut']

const ME_KEY = ['get', '/api/core/auth/me', {}] as const

/**
 * The new-user setup (P20i; v6 006–012, ARCHITECTURE "New user setup (first sign-in)"): shown
 * once, at a person's first sign-in, full screen with no sidebar, one decision per screen, Back
 * and Next. Every page sends a person here until they have finished (`me.setup_required`, the
 * session's gate in app/router.tsx), and again for an agreement whose new version they have not
 * agreed to.
 *
 * The steps are the server's for this run (`/core/setup`): welcome (who the account says they
 * are), name and title, signature, the confidentiality agreement, working as an intern (the
 * non-attorney roles), then Done, which opens Home. They are fixed when the setup opens, so the
 * count never changes under the person while they are in it. Each step is its own address
 * (`/setup/<step>`), so Back is the browser's too.
 */
export function Setup() {
  const me = useMe()
  const { step } = useParams()
  const setup = $api.useQuery('get', '/api/core/setup', {}, { staleTime: 0 })
  const signature = useSignature(me.id)
  const [run, setRun] = useState<SetupStep[] | null>(null)
  if (run === null && setup.data) setRun(runSteps(setup.data.steps))

  const logoAlt = setup.data?.org_short_name || 'Home'
  if (step === 'done') return <Done logoAlt={logoAlt} />
  if (setup.isError || signature.isError) {
    return (
      <div role="alert" className="flex h-full items-center justify-center bg-page">
        <Empty full title="The setup could not be opened">
          Reload the page to try again.
        </Empty>
      </div>
    )
  }
  if (!setup.data || !signature.data || run === null) {
    return (
      <div className="flex h-full items-center justify-center bg-page">
        <Loading what="Opening the portal" />
      </div>
    )
  }
  if (run.length === 0) return <NothingToDo />
  if (!isSetupStep(step) || !run.includes(step)) {
    return <Navigate to={`/setup/${run[0]}`} replace />
  }
  // An agreement is signed with the signature: without one stored, its step comes first.
  const agreement = setup.data.agreements.find((a) => a.kind === step)
  if (agreement && !signature.data.has_image && run.includes('signature')) {
    return <Navigate to="/setup/signature" replace />
  }
  return (
    <SetupFrame
      label={`Step ${run.indexOf(step) + 1} of ${run.length}`}
      progress={{ index: run.indexOf(step), total: run.length }}
      logoAlt={logoAlt}
    >
      <StepBody
        key={step}
        me={me}
        step={step}
        run={run}
        data={setup.data}
        stored={signature.data}
      />
    </SetupFrame>
  )
}

/** No step left (a finished person on /setup): Home. */
function NothingToDo() {
  const navigate = useNavigate()
  useEffect(() => {
    void navigate('/', { replace: true })
  }, [navigate])
  return (
    <div className="flex h-full items-center justify-center bg-page">
      <Loading what="Opening the portal" />
    </div>
  )
}

function StepBody({
  me,
  step,
  run,
  data,
  stored,
}: {
  me: Me
  step: SetupStep
  run: SetupStep[]
  data: SetupData
  stored: Schemas['SignatureOut']
}) {
  const navigate = useNavigate()
  const client = useQueryClient()
  const back = previousStep(run, step)
  const after = nextStep(run, step)
  const last = after === 'done'
  const onBack = back ? () => void navigate(`/setup/${back}`) : undefined

  /** Move on; after the last step, mark the setup finished and play Done. */
  async function advance() {
    if (!last) {
      await navigate(`/setup/${after}`)
      return
    }
    const updated = unwrap(await api.POST('/api/core/setup/finish'))
    client.setQueryData(ME_KEY, updated)
    await navigate('/setup/done', { replace: true })
    void client.invalidateQueries({ queryKey: ['get', '/api/core/setup'] })
  }

  if (step === 'welcome') return <Welcome me={me} data={data} onNext={() => void advance()} />
  if (step === 'name') {
    return <NameAndTitle me={me} onBack={onBack} onNext={advance} last={last} />
  }
  if (step === 'signature') {
    return (
      <SignatureStep
        me={me}
        stored={stored}
        orgName={data.org_name}
        onBack={onBack}
        onNext={advance}
        nextLabel={last ? 'Finish' : 'Next'}
      />
    )
  }
  const agreement = data.agreements.find((a) => a.kind === step)
  if (!agreement) return <Navigate to={`/setup/${after}`} replace />
  const intern = step === 'intern'
  return (
    <AgreementStep
      me={me}
      agreement={agreement}
      signatureUrl={stored.has_image ? signatureSrc(me.id, stored.updated_at) : null}
      scrolls={!intern}
      signed={!intern}
      checkLabel={intern ? 'I understand' : 'I’ve read and agree to this agreement'}
      agreeLabel={last && intern ? 'Finish' : 'Agree'}
      moveLabel={last ? 'Finish' : 'Next'}
      onBack={onBack}
      onAgree={async (alreadyAgreed) => {
        if (!alreadyAgreed) {
          unwrap(
            await api.POST('/api/core/agreements/{agreement_id}/accept', {
              params: { path: { agreement_id: agreement.id } },
            }),
          )
          // The run keeps its steps; the agreement is now shown as agreed if they come back.
          client.setQueryData(
            ['get', '/api/core/setup', {}],
            (old: SetupData | undefined) =>
              old && {
                ...old,
                agreements: old.agreements.map((a) =>
                  a.id === agreement.id ? { ...a, accepted_at: new Date().toISOString() } : a,
                ),
              },
          )
        }
        await advance()
      }}
    />
  )
}

/** Step 1 (006): who the account says they are, to confirm. Not you? signs out. */
function Welcome({ me, data, onNext }: { me: Me; data: SetupData; onNext: () => void }) {
  const signOut = useSignOut()
  const short = data.org_short_name
  const account = short ? `${articleFor(short)} ${short} account` : 'an account'
  return (
    <>
      <SetupTitle
        title={`Welcome, ${firstName(me.name)}`}
        sub={`You already have ${account}. Your details carry over.`}
      />
      <div className="flex items-center gap-3.5 rounded-14 bg-surface-soft p-4">
        <span
          aria-hidden
          className="inline-flex size-12 shrink-0 items-center justify-center rounded-pill bg-avatar-green text-md-lg font-semibold text-white"
        >
          {initialsOf(me.name)}
        </span>
        <div className="min-w-0">
          <div className="truncate text-14 font-semibold text-ink">{me.name}</div>
          <div className="mt-0.5 truncate text-125 text-muted">
            {[me.email, me.title].filter(Boolean).join(' · ')}
          </div>
        </div>
      </div>
      <SetupActions
        backLabel="Not you?"
        onBack={() => void signOut()}
        nextLabel="Continue"
        onNext={onNext}
      />
    </>
  )
}

/** Step 2 (007): the name and title, as the portal holds them, to confirm or correct. Saved
 *  through the route Settings → Profile uses. */
function NameAndTitle({
  me,
  onBack,
  onNext,
  last,
}: {
  me: Me
  onBack?: () => void
  onNext: () => Promise<void>
  last: boolean
}) {
  const client = useQueryClient()
  const [name, setName] = useState(me.name)
  const [title, setTitle] = useState(me.title)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function next() {
    setBusy(true)
    setError(null)
    try {
      if (name.trim() !== me.name || title.trim() !== me.title) {
        const updated = unwrap(
          await api.PATCH('/api/core/auth/me', { body: { name: name.trim(), title } }),
        )
        client.setQueryData(ME_KEY, updated)
      }
      await onNext()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'It was not saved. Try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <SetupTitle title="Your name and title" />
      <label className="flex flex-col gap-1.5">
        <span className={SETUP_LABEL}>Name</span>
        <input
          className={SETUP_FIELD}
          value={name}
          autoComplete="name"
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label className="flex flex-col gap-1.5">
        <span className={SETUP_LABEL}>Title</span>
        <input
          className={SETUP_FIELD}
          value={title}
          autoComplete="organization-title"
          onChange={(event) => setTitle(event.target.value)}
        />
      </label>
      <SetupActions
        onBack={onBack}
        nextLabel={last ? 'Finish' : 'Next'}
        onNext={() => void next()}
        disabled={!name.trim()}
        busy={busy}
        error={error}
        hint="Give your name first"
      />
    </>
  )
}
