import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'

import { api, type Schemas } from '../../api/client'
import { $api, messageOf } from '../../api/query'
import { isAdmin, useMe } from '../../app/session'
import { Dialog } from '../../components/Dialog'
import { Button, Callout } from '../../components/ui'
import { MEBIBYTE, unplaced } from '../../lib/settings'
import { AgreementsCard } from './Agreements'
import { AssistantRuns } from './AssistantRuns'
import {
  BoolRow,
  ChoiceRow,
  GenericRow,
  NumberRow,
  Row,
  RowAction,
  SettingsCard,
  SettingsTab,
  TextAreaRow,
  TextRow,
  TimeZoneRow,
  type Setting,
  type Unit,
} from './rows'

type Shell = Schemas['ShellOut']

/** The assistant's two seats (P19d): who runs the constant checks, and who drafts. Each key is
 *  read only from the server's .env; here only the choice is made. */
const GENERAL_PROVIDERS = [
  { value: 'gemini', label: 'Gemini' },
  { value: 'off', label: 'Off' },
]
const DRAFTING_PROVIDERS = [
  { value: 'claude_code', label: 'Claude Code, this machine' },
  { value: 'anthropic', label: 'Claude API' },
  { value: 'gemini', label: 'Gemini' },
  { value: 'off', label: 'Off' },
]

/** The default letterhead: where the seal and the signer live (P11, P19b). */
function useDefaultLetterhead() {
  return $api.useQuery(
    'get',
    '/api/drafting/shells',
    {},
    { select: (shells: Shell[]) => shells.find((s) => s.is_default && !s.archived_at) ?? null },
  )
}

/**
 * Settings → Organisation (202): the organisation's name, short name, the default letterhead's
 * seal (224 replaces it) and zone; who letters are signed by and the lines under every message;
 * for an admin, the agreements the new-user setup asks people to sign (P20i); then the mail, notification, upload and assistant windows, and last whatever setting no tab
 * names, so nothing the backend adds later is out of reach (DECISIONS 23). Staff see it; an
 * admin edits it. Each muted line is the backend's own description of the setting.
 */
export function Organisation() {
  const me = useMe()
  const navigate = useNavigate()
  const editable = isAdmin(me)
  const letterhead = useDefaultLetterhead()
  const people = $api.useQuery('get', '/api/core/users')
  const [replacing, setReplacing] = useState(false)
  return (
    <SettingsTab>
      {(byKey, all) => {
        const number = (
          key: string,
          label: string,
          unit: Unit,
          extra: { min?: number; max?: number; scale?: number } = {},
        ) => {
          const setting = byKey.get(key)
          return setting ? (
            <NumberRow
              key={key}
              setting={setting}
              label={label}
              hint={setting.description}
              unit={unit}
              editable={editable}
              {...extra}
            />
          ) : null
        }
        const text = (key: string, label: string) => {
          const setting = byKey.get(key)
          return setting ? (
            <TextRow
              key={key}
              setting={setting}
              label={label}
              hint={setting.description}
              editable={editable}
            />
          ) : null
        }
        const choice = (
          key: string,
          label: string,
          options: ReadonlyArray<{ value: string; label: string }>,
        ) => {
          const setting = byKey.get(key)
          return setting ? (
            <ChoiceRow
              key={key}
              setting={setting}
              label={label}
              hint={setting.description}
              options={options}
              editable={editable}
            />
          ) : null
        }
        const bool = (key: string, label: string) => {
          const setting = byKey.get(key)
          return setting ? (
            <BoolRow
              key={key}
              setting={setting}
              label={label}
              on={setting.description}
              off={setting.description}
              editable={editable}
            />
          ) : null
        }
        const houseStyle = byKey.get('ai.house_style')
        const name = byKey.get('org.name')
        const shortName = byKey.get('org.short_name')
        const zone = byKey.get('org.default_display_timezone')
        const signature = byKey.get('org.signature')
        const director = byKey.get('org.legal_director')
        // P20h (42d): the legal director, one person named here; nothing reads it until team chat.
        const directorOptions = [
          { value: '', label: 'Nobody named' },
          ...(people.data ?? [])
            .filter((p) => p.is_active && p.records_role && p.records_role !== 'requester')
            .map((p) => ({ value: p.id, label: p.name })),
        ]
        const other: Setting[] = unplaced(all)
        const shell = letterhead.data
        return (
          <>
            {editable ? null : <Callout>Only an admin can change these.</Callout>}
            <SettingsCard title="Organisation">
              {name ? <TextRow setting={name} label="Name" editable={editable} /> : null}
              {shortName ? (
                <TextRow
                  setting={shortName}
                  label="Short name"
                  editable={editable}
                  shown={(value) => value || <span className="text-muted">The initials</span>}
                />
              ) : null}
              <Row
                label="Letterhead seal"
                hint={
                  shell ? undefined : letterhead.isPending ? undefined : 'No default letterhead'
                }
                action={
                  editable && shell ? (
                    <RowAction onClick={() => setReplacing(true)}>Replace</RowAction>
                  ) : null
                }
              >
                {shell?.has_seal ? <SealImage shellId={shell.id} className="h-5 w-auto" /> : null}
                {shell && !shell.has_seal ? <span className="text-13 text-muted">None</span> : null}
              </Row>
              {zone ? (
                <TimeZoneRow
                  setting={zone}
                  label="Time zone"
                  hint={zone.description}
                  editable={editable}
                />
              ) : null}
              {director && people.data ? (
                <ChoiceRow
                  setting={director}
                  label="Legal director"
                  hint="Holds the team chat's compliance keys once team chat is built."
                  options={directorOptions}
                  editable={editable}
                />
              ) : null}
            </SettingsCard>
            <SettingsCard title="Signature">
              <Row
                label="Signed by"
                hint={shell?.signer_title || undefined}
                value={shell?.signer_name || <span className="text-muted">Nobody named</span>}
                action={
                  <RowAction
                    onClick={() =>
                      void navigate(
                        shell ? `/templates/letterheads/${shell.id}` : '/templates/letterheads',
                      )
                    }
                  >
                    Edit
                  </RowAction>
                }
              />
              {signature ? (
                <TextAreaRow
                  setting={signature}
                  label="Under every message"
                  hint={signature.description}
                  editable={editable}
                />
              ) : null}
            </SettingsCard>
            {/* P20i: the agreements the new-user setup asks people to sign (no screen draws it). */}
            {editable ? <AgreementsCard /> : null}
            <SettingsCard title="Mail">
              {number('mail.undo_send_seconds', 'Undo window after Send', 'seconds')}
              {number('core.system_mail_window_days', 'Mail counted on System', 'days')}
            </SettingsCard>
            <SettingsCard title="Notifications">
              {number('notifications.deadline_warning_days', 'Warn before a deadline', 'days')}
              {number('notifications.daily_summary_hour', 'Daily summary hour', 'h', { max: 23 })}
            </SettingsCard>
            <SettingsCard title="Who’s online">
              {number('core.presence_away_minutes', 'Shown as away after', 'minutes')}
              {number('core.presence_gone_minutes', 'Dropped from the list after', 'minutes')}
            </SettingsCard>
            {/* P22: team chat's one setting (no screen draws it). */}
            <SettingsCard title="Team chat">
              {number('chat.attachment_max_bytes', 'Largest file attached in chat', 'MB', {
                scale: MEBIBYTE,
              })}
            </SettingsCard>
            <SettingsCard title="Inbox and uploads">
              {number('records.upload_max_file_bytes', 'Largest file in Add a document', 'MB', {
                scale: MEBIBYTE,
              })}
              {number('records.upload_max_files', 'Most files in one Add a document', 'files')}
              {number('records.inbound_max_payload_bytes', 'Largest inbound mail delivery', 'MB', {
                scale: MEBIBYTE,
              })}
              {number(
                'records.inbound_date_window_hours',
                'Inbound mail may be dated up to',
                'hours',
              )}
            </SettingsCard>
            <SettingsCard title="Assistant">
              {choice('ai.general_provider', 'Constant checks run by', GENERAL_PROVIDERS)}
              {text('ai.general_model', 'Model for the constant checks')}
              {choice('ai.drafting_provider', 'Drafting and research run by', DRAFTING_PROVIDERS)}
              {text('ai.drafting_model', 'Model for drafting and research')}
              {text('ai.claude_code_command', 'Claude Code command')}
              {text('ai.claude_code_allowed_tools', 'Tools Claude Code may use')}
              {bool('ai.exclude_privileged', 'Keep privileged work out')}
              {bool('ai.keep_prompts', 'Keep the full prompts')}
              {number('ai.daily_run_limit', 'Runs allowed per day', 'runs')}
              {houseStyle ? (
                <TextAreaRow
                  setting={houseStyle}
                  label="House style"
                  hint={houseStyle.description}
                  editable={editable}
                />
              ) : null}
            </SettingsCard>
            {editable ? (
              <SettingsCard title="Assistant runs">
                <div className="border-t border-line-row px-[22px] py-3.5">
                  <p className="mb-2 text-12 text-muted">
                    Everything sent to the assistant, newest first: what was sent is kept as the
                    size of each part, never the text itself unless “Keep the full prompts” is on.
                  </p>
                  <AssistantRuns />
                </div>
              </SettingsCard>
            ) : null}
            {other.length > 0 ? (
              <SettingsCard title="Other">
                {other.map((setting) => (
                  <GenericRow key={setting.key} setting={setting} editable={editable} />
                ))}
              </SettingsCard>
            ) : null}
            {replacing && shell ? (
              <ReplaceSeal shell={shell} onClose={() => setReplacing(false)} />
            ) : null}
          </>
        )
      }}
    </SettingsTab>
  )
}

/** The default letterhead's seal, as stored (the route caches it for five minutes; `v` asks for
 *  a replaced one anew). */
function SealImage({
  shellId,
  version,
  className,
}: {
  shellId: string
  version?: number
  className?: string
}) {
  return (
    <img
      src={`/api/drafting/shells/${shellId}/seal${version ? `?v=${version}` : ''}`}
      alt="The letterhead's seal"
      className={className}
    />
  )
}

/**
 * Replace the seal (224): drop or browse an image; it is shown with its name and size before it
 * is used. The seal is the default letterhead's (letterheads each have their own, under
 * Templates); the server takes a PNG, JPEG or SVG up to 4 MB.
 */
function ReplaceSeal({ shell, onClose }: { shell: Shell; onClose: () => void }) {
  const client = useQueryClient()
  const [file, setFile] = useState<{ file: File; url: string; size: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => () => (file ? URL.revokeObjectURL(file.url) : undefined), [file])

  function choose(picked: File | undefined) {
    setError(null)
    if (!picked) return
    if (!picked.type.startsWith('image/')) return setError('The seal is an image.')
    if (picked.size > 4 * MEBIBYTE) return setError('The image is larger than 4 MB.')
    const url = URL.createObjectURL(picked)
    const probe = new Image()
    probe.onload = () =>
      setFile({ file: picked, url, size: `${probe.naturalWidth} × ${probe.naturalHeight}` })
    probe.onerror = () => setFile({ file: picked, url, size: '' })
    probe.src = url
  }

  async function use() {
    if (!file) return
    setBusy(true)
    setError(null)
    const payload = new FormData()
    payload.set('file', file.file)
    const result = await api.POST('/api/drafting/shells/{shell_id}/seal', {
      params: { path: { shell_id: shell.id } },
      body: payload as never,
      bodySerializer: (body) => body as unknown as FormData,
    })
    setBusy(false)
    if (result.error) return setError(messageOf(result.error))
    await client.invalidateQueries({ queryKey: ['get', '/api/drafting/shells'] })
    onClose()
  }

  return (
    <Dialog
      title="Replace the seal"
      open
      onClose={onClose}
      width={460}
      top={80}
      footer={
        <>
          <Button kind="text" pill onClick={onClose}>
            Cancel
          </Button>
          <Button kind="primary" pill busy={busy} disabled={!file} onClick={() => void use()}>
            Use this seal
          </Button>
        </>
      }
    >
      <label
        className="relative block cursor-pointer rounded-10 border-[1.5px] border-dashed border-line-control bg-surface-row p-[18px] text-center text-125 text-ink-2"
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault()
          choose(event.dataTransfer.files[0])
        }}
      >
        Drop an image or <span className="font-medium text-brand">browse</span>
        <br />
        <span className="text-12 text-muted">PNG, JPEG or SVG, up to 4 MB</span>
        <input
          type="file"
          accept="image/png,image/jpeg,image/svg+xml"
          aria-label="Seal image"
          className="absolute inset-0 cursor-pointer opacity-0"
          onChange={(event) => choose(event.target.files?.[0])}
        />
      </label>
      {file || shell.has_seal ? (
        <div className="flex items-center gap-3.5">
          {file ? (
            <img src={file.url} alt="" className="h-[35px] w-auto shrink-0" />
          ) : (
            <SealImage shellId={shell.id} className="h-[35px] w-auto shrink-0" />
          )}
          <div className="min-w-0 truncate text-125 text-ink-2">
            {file
              ? `${file.file.name}${file.size ? ` · ${file.size}` : ''}`
              : `The seal on ${shell.name} now`}
          </div>
        </div>
      ) : null}
      {error ? (
        <p
          role="alert"
          className="rounded-10 bg-danger-tint px-3.5 py-[11px] text-125 text-danger-2"
        >
          {error}
        </p>
      ) : null}
    </Dialog>
  )
}
