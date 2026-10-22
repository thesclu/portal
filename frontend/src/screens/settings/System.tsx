import type { ReactNode } from 'react'

import type { Schemas } from '../../api/client'
import { $api } from '../../api/query'
import { useMe } from '../../app/session'
import { LoadFailed, Loading } from '../../components/States'
import { cx } from '../../components/ui'
import { formatAgo, formatListTime } from '../../lib/dates'
import { activityWhen } from '../../lib/notifications'
import { civilToday } from '../../lib/rules'
import { isLate, jobLabel, jobOrder, runsOut } from '../../lib/system'
import { BoolRow, NumberRow, Row, SettingsCard, TextRow, type Setting } from './rows'

type Job = Schemas['JobOut']
type SignIn = Schemas['SignInOut']
type DraftingTool = Schemas['DraftingToolOut']

/** One line of System (199): a dot (green when all is well, orange when something wants a
 *  look), the name and a muted line under it, and the time at the right. */
function HealthRow({
  ok,
  label,
  hint,
  children,
  warn = false,
}: {
  ok: boolean
  label: string
  hint?: ReactNode
  children?: ReactNode
  /** The right-hand words in the warning colour (199 "1 in the last 24 hours"). */
  warn?: boolean
}) {
  return (
    <div className="flex items-center gap-3.5 border-t border-line-row px-[22px] py-[13px]">
      <span
        aria-label={ok ? 'Working' : 'Needs a look'}
        className={cx('size-2 shrink-0 rounded-pill', ok ? 'bg-avatar-green' : 'bg-attention')}
      />
      <div className="min-w-0 flex-1">
        <div className="text-13 text-ink">{label}</div>
        {hint ? <div className="mt-0.5 text-12 text-muted">{hint}</div> : null}
      </div>
      {children ? (
        <span className={cx('text-125 whitespace-nowrap', warn ? 'text-warn' : 'text-ink-2')}>
          {children}
        </span>
      ) : null}
    </div>
  )
}

/**
 * Sign-in (P25a; no screen draws it, so it comes after 199's cards): the rules an administrator
 * sets, each written to Keycloak (the session's idle limit and longest life, how often the
 * portal and the other applications ask for the second step again, whether authenticator apps
 * are a second step beside passkeys: on by default since P25c); where Keycloak is and whether the
 * server holds its two client secrets (never
 * a value); changes still owed to Keycloak; and where Keycloak's own console is, for everything
 * else about signing in (the password rules, lockouts, the emails).
 */
function SignInCard({ signIn, settings }: { signIn: SignIn; settings: Setting[] }) {
  const byKey = new Map(settings.map((setting) => [setting.key, setting]))
  const idle = byKey.get('auth.session_hours')
  const longest = byKey.get('auth.session_max_hours')
  const reverify = byKey.get('auth.reverify_hours')
  const tools = byKey.get('auth.reverify_tool_minutes')
  const app = byKey.get('auth.allow_authenticator_app')
  const secret = (set: boolean) => (set ? 'Set' : <span className="text-warn">Not set</span>)
  const owed =
    signIn.failed_changes > 0
      ? `${signIn.failed_changes} failed, retried every hour (see Users)`
      : signIn.pending_changes > 0
        ? `${signIn.pending_changes} on the way`
        : 'None'
  return (
    <SettingsCard title="Sign-in">
      {idle ? (
        <NumberRow
          setting={idle}
          label="Sign out after"
          hint="Without use. Keycloak's idle limit too."
          unit="hours"
          min={1}
          max={168}
          editable
        />
      ) : null}
      {longest ? (
        <NumberRow
          setting={longest}
          label="Longest sign-in"
          hint="Used or not; then the password is asked for again."
          unit="hours"
          min={1}
          max={720}
          editable
        />
      ) : null}
      {reverify ? (
        <NumberRow
          setting={reverify}
          label="Ask for the second step again after"
          hint="In the portal, once the last passkey or app check is this old."
          unit="hours"
          min={1}
          max={168}
          editable
        />
      ) : null}
      {tools ? (
        <NumberRow
          setting={tools}
          label="Second step again in another tool after"
          hint="When opening the drafting tool or another app, if the last check is older."
          unit="minutes"
          min={1}
          max={1440}
          editable
        />
      ) : null}
      {app ? (
        <BoolRow
          setting={app}
          label="Authenticator apps"
          on="On (the default): each person chooses a passkey or an authenticator app when they first set up two-step sign-in. Recovery codes are the backup."
          off="Off: passkeys only. Anyone with only an app uses a recovery code at their next sign-in and adds a passkey."
          editable
        />
      ) : null}
      <Row label="Keycloak" hint={signIn.issuer || 'Not configured'} value={null} action={null} />
      <Row
        label="Client secrets"
        hint="KEYCLOAK_CLIENT_SECRET and KEYCLOAK_ADMIN_CLIENT_SECRET, on the server"
        value={
          <>
            {secret(signIn.client_secret_set)} · {secret(signIn.admin_secret_set)}
          </>
        }
        action={null}
      />
      <Row label="Changes owed to Keycloak" value={owed} action={null} />
      <Row
        label="Everything else"
        hint="The password rules, lockouts, emails and passkey settings are Keycloak's own, in its admin console (not on the public address)."
        value={
          signIn.admin_console_url ? (
            <span className="select-all">{signIn.admin_console_url}</span>
          ) : (
            <span className="text-muted">Through the server (see the handbook)</span>
          )
        }
        action={null}
      />
    </SettingsCard>
  )
}

/**
 * The drafting tool (P21c; no screen draws it, so it comes after 199's cards): the switch, its two
 * addresses (empty: the server's), the organisation's usual letterhead there, whether each of the
 * two shared secrets is set on the server (never a value: they live in its environment only), how
 * the tool signs people in (Keycloak, P25b), and the address to give the drafting tool for its
 * webhooks. Off, or with a secret missing, the portal drafts in its own editor.
 */
function DraftingToolCard({
  tool,
  settings,
  timezone,
}: {
  tool: DraftingTool
  settings: Setting[]
  timezone: string
}) {
  const byKey = new Map(settings.map((setting) => [setting.key, setting]))
  const enabled = byKey.get('drafting.fork_enabled')
  const api = byKey.get('drafting.fork_api_url')
  const web = byKey.get('drafting.fork_web_url')
  const letterhead = byKey.get('drafting.fork_letterhead_id')
  const missing = [
    !tool.service_secret_set && 'DRAFTING_SERVICE_SECRET',
    !tool.webhook_secret_set && 'DRAFTING_WEBHOOK_SECRET',
    !tool.api_url && 'the API address',
    !tool.web_url && 'the pages’ address',
  ].filter(Boolean)
  const fromServer = (value: string, fallback: string) =>
    value ||
    (fallback ? (
      <span className="text-muted">The server’s: {fallback}</span>
    ) : (
      <span className="text-muted">Not set</span>
    ))
  const secret = (set: boolean) => (set ? 'Set' : <span className="text-warn">Not set</span>)
  return (
    <SettingsCard title="Drafting tool">
      {enabled ? (
        <BoolRow
          setting={enabled}
          label="Write letters in the drafting tool"
          on={
            missing.length === 0
              ? 'On: stage 2 and Templates are the drafting tool’s pages.'
              : `On, but not in use until the server has ${missing.join(', ')}.`
          }
          off="Off: letters are written in the portal’s own editor."
          editable
        />
      ) : null}
      {api ? (
        <TextRow
          setting={api}
          label="API address"
          hint="As this server reaches the drafting tool."
          editable
          shown={(value) => fromServer(value, tool.api_url_default)}
        />
      ) : null}
      {web ? (
        <TextRow
          setting={web}
          label="Pages’ address"
          hint="As a browser reaches it; the only other site the portal’s pages may frame."
          editable
          shown={(value) => fromServer(value, tool.web_url_default)}
        />
      ) : null}
      {letterhead ? (
        <TextRow
          setting={letterhead}
          label="Usual letterhead"
          hint="The drafting tool’s id for it, given to a letter written from scratch."
          editable
          shown={(value) => value || <span className="text-muted">None</span>}
        />
      ) : null}
      <Row
        label="Service secret"
        hint="DRAFTING_SERVICE_SECRET, on the server"
        value={secret(tool.service_secret_set)}
        action={null}
      />
      <Row
        label="Webhook secret"
        hint="DRAFTING_WEBHOOK_SECRET, on the server"
        value={secret(tool.webhook_secret_set)}
        action={null}
      />
      <Row
        label="Sign-in"
        hint="Through Keycloak’s drafting client. Signing out of the portal, deactivating someone or signing them out everywhere ends their drafting-tool sessions there too."
        value="Keycloak"
        action={null}
      />
      <Row
        label="Webhook address"
        hint={
          tool.last_webhook_at
            ? `Give this to the drafting tool. Last one received ${formatListTime(tool.last_webhook_at, timezone)}.`
            : 'Give this to the drafting tool (SCLU_WEBHOOK_URL). None received yet.'
        }
        value={<span className="select-all">{tool.webhook_url}</span>}
        action={null}
      />
    </SettingsCard>
  )
}

/**
 * Settings → System (199), administrators: is mail arriving and leaving, did anything bounce,
 * are the scheduled checks running, and how far the holiday calendars reach. A read over what
 * the portal already keeps (the inbound sources, the outbound messages and their delivery
 * events, procrastinate's own job rows); refreshed every minute.
 */
export function System() {
  const me = useMe()
  const system = $api.useQuery('get', '/api/core/system', {}, { refetchInterval: 60_000 })
  const settings = $api.useQuery('get', '/api/core/settings')
  if (system.isPending) return <Loading what="Checking the system" />
  if (system.isError)
    return (
      <LoadFailed
        what="the system status"
        error={system.error}
        onRetry={() => void system.refetch()}
      />
    )
  const { mail, jobs, calendars, outgoing, bounces, sign_in, last_export } = system.data
  const followupsSending =
    settings.data?.find((s) => s.key === 'records.followups_send_enabled')?.value === true
  const when = (iso: string | null | undefined) => formatListTime(iso, me.timezone)
  const today = civilToday(me.timezone)
  const now = new Date(system.data.now)
  const failedLine = (job: Job) =>
    job.failed_recent > 0
      ? `${job.failed_recent} failed ${job.failed_recent === 1 ? 'run' : 'runs'} in the last ${mail.window_days} days`
      : undefined

  return (
    <>
      <SettingsCard title="Mail">
        <HealthRow
          ok={mail.last_received_at !== null}
          label="Incoming mail"
          hint={`${mail.received} received in the last ${mail.window_days} days`}
        >
          {mail.last_received_at ? `Last message ${formatAgo(mail.last_received_at)}` : 'None yet'}
        </HealthRow>
        {mail.awaiting_extraction > 0 ? (
          <HealthRow
            ok={false}
            label="Waiting on their files"
            hint="On their requests, attachments not stored yet. If this stays up, the background worker is not running."
          >
            {mail.awaiting_extraction}
          </HealthRow>
        ) : null}
        <HealthRow
          ok={outgoing.sending_enabled && outgoing.failed === 0}
          label="Outgoing mail"
          hint={
            !outgoing.sending_enabled
              ? 'Test mode: the provider accepts each message and delivers nothing (MAIL_SEND_ENABLED on the server).'
              : outgoing.failed > 0
                ? `${outgoing.failed} failed to send in the last ${mail.window_days} days`
                : outgoing.queued > 0
                  ? `${outgoing.queued} waiting to go`
                  : undefined
          }
        >
          {outgoing.last_sent_at ? `Last sent ${when(outgoing.last_sent_at)}` : 'Nothing sent yet'}
        </HealthRow>
        <HealthRow
          ok={bounces.count === 0}
          label="Bounces"
          hint={bounces.count > 0 ? 'Each one waits in Flagged to be fixed and resent.' : undefined}
          warn={bounces.count > 0}
        >
          {bounces.count} in the last {bounces.window_hours} hours
        </HealthRow>
      </SettingsCard>
      <SettingsCard title="Scheduled checks">
        {jobOrder(jobs).map((job) => {
          const late = isLate(job.last_run_at, job.schedule, now)
          const notes = [
            job.name === 'records.send_auto_followups'
              ? followupsSending
                ? 'Sending is on'
                : 'Sending is off'
              : undefined,
            late ? 'Late: the worker may not be running' : undefined,
            failedLine(job),
          ].filter(Boolean)
          return (
            <HealthRow
              key={job.name}
              ok={
                Boolean(job.last_run_at) &&
                !late &&
                job.failed_recent === 0 &&
                job.last_status !== 'failed'
              }
              label={jobLabel(job.name)}
              hint={notes.length > 0 ? notes.join(' · ') : undefined}
            >
              {job.last_run_at ? `Ran ${when(job.last_run_at)}` : 'Has not run yet'}
              {job.last_status && !['succeeded', 'doing', 'todo'].includes(job.last_status)
                ? ` · ${job.last_status}`
                : ''}
              {job.name === 'records.sweep_overdue_deadlines'
                ? ` · next ${when(job.next_run_at)}`
                : ''}
            </HealthRow>
          )
        })}
      </SettingsCard>
      <SettingsCard title="Data">
        {calendars.map((calendar) => {
          const words = runsOut(calendar.last_holiday, today)
          return (
            <HealthRow
              key={calendar.key}
              ok={words.startsWith('Runs out in') && !/ (day|month)s?$/.test(words)}
              label={
                calendars.length > 1 ? `Holiday calendar · ${calendar.key}` : 'Holiday calendar'
              }
            >
              {words}
            </HealthRow>
          )
        })}
        {/* P23b (199): the newest registry export, from its activity line. */}
        <HealthRow
          ok
          label="Last registry export"
          hint={
            last_export
              ? `${last_export.who} · ${last_export.format === 'pdf' ? 'PDF summary' : last_export.format.toUpperCase()}`
              : undefined
          }
        >
          {last_export ? activityWhen(last_export.at, me.timezone) : 'None yet'}
        </HealthRow>
      </SettingsCard>
      {settings.data ? <SignInCard signIn={sign_in} settings={settings.data} /> : null}
      {system.data.drafting && settings.data ? (
        <DraftingToolCard
          tool={system.data.drafting}
          settings={settings.data}
          timezone={me.timezone}
        />
      ) : null}
    </>
  )
}
