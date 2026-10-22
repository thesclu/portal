/**
 * The cards and rows every Settings tab is made of, drawn from the v5 screens (197–204): a
 * #fdfdfd card, 16 px round, the float shadow, a 14 px 600 title (16px 22px 12px), and rows of
 * 13 px under a hairline (14px 22px): the label (a 12 px muted line under it when there is one),
 * the value at the right, and a 44 px brand action ("Edit", "Change", "Manage", "Replace").
 *
 * The organisation settings are read from GET /api/core/settings and saved one at a time through
 * PUT /api/core/settings/{key}. The backend keeps each value's type (a number stays a whole
 * non-negative number, a boolean a boolean, text text) and refuses a wrong one with a message,
 * which the row shows under its label. Only an admin can change anything; everyone else sees the
 * values with no Edit (the 44 px stays, so the values line up).
 */
import { useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent, type ReactNode } from 'react'

import type { Schemas } from '../../api/client'
import { $api, messageOf } from '../../api/query'
import { RaisedInput, RaisedSelect, RaisedTextarea, Switch } from '../../components/Fields'
import { LoadFailed, Loading } from '../../components/States'
import { cx } from '../../components/ui'
import { displayValue, storedValue, timeZones, zoneLabel } from '../../lib/settings'

export type Setting = Schemas['SettingOut']

/** A card of rows with its title (197 "Follow-ups"). `aside` sits at the title's right (198
 *  "Runs out …"). */
export function SettingsCard({
  title,
  aside,
  className,
  children,
}: {
  title: ReactNode
  aside?: ReactNode
  className?: string
  children: ReactNode
}) {
  return (
    <section className={cx('overflow-hidden rounded-16 bg-card shadow-float', className)}>
      <div className="flex items-center gap-2.5 px-[22px] pt-4 pb-3">
        <h2 className="text-14 font-semibold text-ink">{title}</h2>
        {aside ? <span className="ml-auto flex items-center gap-2">{aside}</span> : null}
      </div>
      {children}
    </section>
  )
}

const ACTION =
  'w-11 shrink-0 text-right text-125 font-medium whitespace-nowrap text-brand hover:text-brand-dark disabled:opacity-55'

/** The 44 px action at a row's right; with no `onClick` it only keeps the place. */
export function RowAction({
  onClick,
  children,
  disabled,
  type = 'button',
}: {
  onClick?: () => void
  children?: ReactNode
  disabled?: boolean
  type?: 'button' | 'submit'
}) {
  if (!onClick && type === 'button') return <span aria-hidden className="w-11 shrink-0" />
  return (
    <button type={type} onClick={onClick} disabled={disabled} className={ACTION}>
      {children}
    </button>
  )
}

/** "Cancel" beside a row's Save. */
function CancelButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="text-125 font-medium whitespace-nowrap text-ink-2 hover:text-ink"
    >
      Cancel
    </button>
  )
}

/**
 * One row: the label (and its muted line), then whatever the caller puts at the right (the
 * value, a switch, an editor), then the action. `error` replaces the muted line in the danger
 * colour.
 */
export function Row({
  label,
  hint,
  error,
  value,
  action,
  children,
  className,
}: {
  label: ReactNode
  hint?: ReactNode
  error?: string | null
  /** The value as it reads (13 px ink, one line). */
  value?: ReactNode
  /** The 44 px action; `null` keeps its place empty, `undefined` leaves no place at all. */
  action?: ReactNode | null
  /** Anything else at the right (an editor, a switch), before the action. */
  children?: ReactNode
  className?: string
}) {
  return (
    <div
      className={cx('flex items-center gap-4 border-t border-line-row px-[22px] py-3.5', className)}
    >
      <div className="min-w-0 flex-1">
        <div className="text-13 text-ink">{label}</div>
        {error ? (
          <div role="alert" className="mt-[3px] text-12 text-danger">
            {error}
          </div>
        ) : hint ? (
          <div className="mt-[3px] text-12 text-muted">{hint}</div>
        ) : null}
      </div>
      {value !== undefined ? (
        <span className="max-w-[60%] truncate text-13 whitespace-nowrap text-ink">{value}</span>
      ) : null}
      {children}
      {action === undefined ? null : action === null ? <RowAction /> : action}
    </div>
  )
}

export function useSaveSetting() {
  const client = useQueryClient()
  return $api.useMutation('put', '/api/core/settings/{key}', {
    onSuccess: () => client.invalidateQueries({ queryKey: ['get', '/api/core/settings'] }),
  })
}

/** Loads every setting once for a tab and hands the tab a lookup by key plus the whole list. */
export function SettingsTab({
  children,
}: {
  children: (byKey: Map<string, Setting>, all: Setting[]) => ReactNode
}) {
  const settings = $api.useQuery('get', '/api/core/settings')
  if (settings.isPending) return <Loading what="Loading settings" />
  if (settings.isError)
    return (
      <LoadFailed
        what="the settings"
        error={settings.error}
        onRetry={() => void settings.refetch()}
      />
    )
  const byKey = new Map(settings.data.map((setting) => [setting.key, setting]))
  return <>{children(byKey, settings.data)}</>
}

// ---- Numbers ---------------------------------------------------------------------------------

export type Unit = 'days' | 'hours' | 'minutes' | 'seconds' | 'files' | 'MB' | 'h' | 'runs'

function formatShown(shown: number): string {
  return Number.isInteger(shown) ? String(shown) : shown.toFixed(1)
}

/**
 * A whole number with a unit ("5 days", 197). `scale` is how many stored units make one shown
 * unit (MEBIBYTE for a byte count shown in MB): the row shows stored / scale and stores
 * shown * scale.
 */
export function NumberRow({
  setting,
  label,
  hint,
  editable,
  unit,
  min = 0,
  max,
  scale = 1,
}: {
  setting: Setting
  label: string
  hint?: string
  editable: boolean
  unit?: Unit
  min?: number
  max?: number
  scale?: number
}) {
  const save = useSaveSetting()
  const stored = typeof setting.value === 'number' ? setting.value : Number(setting.value)
  const shown = displayValue(stored, scale)
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(formatShown(shown))
  const number = Number(value)
  const valid =
    value.trim() !== '' &&
    Number.isInteger(number) &&
    number >= min &&
    (max === undefined || number <= max)
  const unitWord = unit && unit !== 'h' ? ` of ${unit}` : ''
  const range = max === undefined ? `at least ${min}` : `from ${min} to ${max}`
  const suffix = unit ? ` ${unit}` : ''

  async function commit(event: FormEvent) {
    event.preventDefault()
    if (!valid) return
    try {
      await save.mutateAsync({
        params: { path: { key: setting.key } },
        body: { value: storedValue(number, scale) },
      })
      setEditing(false)
    } catch {
      /* shown under the label */
    }
  }

  const error = save.isError ? messageOf(save.error) : null
  if (!editing) {
    return (
      <Row
        label={label}
        hint={hint}
        error={error}
        value={`${Number.isNaN(shown) ? String(setting.value) : formatShown(shown)}${suffix}`}
        action={
          editable ? (
            <RowAction
              onClick={() => {
                save.reset()
                setValue(formatShown(shown))
                setEditing(true)
              }}
            >
              Edit
            </RowAction>
          ) : null
        }
      />
    )
  }
  return (
    <form onSubmit={(event) => void commit(event)}>
      <Row
        label={label}
        hint={!valid ? `A whole number${unitWord}, ${range}.` : hint}
        error={error}
        action={
          <RowAction type="submit" disabled={!valid || save.isPending}>
            Save
          </RowAction>
        }
      >
        <span className="flex items-center gap-2 text-13 text-ink">
          <RaisedInput
            type="number"
            min={min}
            max={max}
            step={1}
            autoFocus
            aria-label={unit ? `${label}, in ${unit}` : label}
            className="h-8! w-20!"
            value={value}
            onChange={(event) => setValue(event.target.value)}
          />
          {unit ? <span>{unit}</span> : null}
        </span>
        <CancelButton onClick={() => setEditing(false)} />
      </Row>
    </form>
  )
}

// ---- On or off --------------------------------------------------------------------------------

/** A switch, saved as it is flipped (201). `on` and `off` say what each state means. */
export function BoolRow({
  setting,
  label,
  on,
  off,
  editable,
}: {
  setting: Setting
  label: string
  on?: string
  off?: string
  editable: boolean
}) {
  const save = useSaveSetting()
  const checked = setting.value === true
  return (
    <Row
      label={label}
      hint={checked ? on : off}
      error={save.isError ? messageOf(save.error) : null}
    >
      <Switch
        label={label}
        checked={checked}
        disabled={!editable || save.isPending}
        onChange={(next) =>
          save.mutate({ params: { path: { key: setting.key } }, body: { value: next } })
        }
      />
    </Row>
  )
}

// ---- Text -------------------------------------------------------------------------------------

/** One line of text with Edit, then Save and Cancel. */
export function TextRow({
  setting,
  label,
  hint,
  editable,
  shown,
}: {
  setting: Setting
  label: string
  hint?: string
  editable: boolean
  /** What the row shows for the stored text, when that is not the text itself. */
  shown?: (value: string) => ReactNode
}) {
  const save = useSaveSetting()
  const current = typeof setting.value === 'string' ? setting.value : String(setting.value ?? '')
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(current)

  async function commit(event: FormEvent) {
    event.preventDefault()
    try {
      await save.mutateAsync({ params: { path: { key: setting.key } }, body: { value } })
      setEditing(false)
    } catch {
      /* shown under the label */
    }
  }

  const error = save.isError ? messageOf(save.error) : null
  if (!editing) {
    return (
      <Row
        label={label}
        hint={hint}
        error={error}
        value={shown ? shown(current) : current || <span className="text-muted">Not set</span>}
        action={
          editable ? (
            <RowAction
              onClick={() => {
                save.reset()
                setValue(current)
                setEditing(true)
              }}
            >
              Edit
            </RowAction>
          ) : null
        }
      />
    )
  }
  return (
    <form onSubmit={(event) => void commit(event)}>
      <Row
        label={label}
        hint={hint}
        error={error}
        action={
          <RowAction type="submit" disabled={save.isPending}>
            Save
          </RowAction>
        }
      >
        <RaisedInput
          type="text"
          autoFocus
          aria-label={label}
          className="h-8! w-72!"
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
        <CancelButton onClick={() => setEditing(false)} />
      </Row>
    </form>
  )
}

/** Several lines of text: the first two lines under the label, the whole text in a box to edit,
 *  with the setting's own description (its placeholders) under the box. */
export function TextAreaRow({
  setting,
  label,
  hint,
  editable,
}: {
  setting: Setting
  label: string
  hint?: string
  editable: boolean
}) {
  const save = useSaveSetting()
  const current = typeof setting.value === 'string' ? setting.value : String(setting.value ?? '')
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(current)

  async function commit(event: FormEvent) {
    event.preventDefault()
    try {
      await save.mutateAsync({ params: { path: { key: setting.key } }, body: { value } })
      setEditing(false)
    } catch {
      /* shown under the box */
    }
  }

  const error = save.isError ? messageOf(save.error) : null
  if (!editing) {
    return (
      <Row
        label={label}
        error={error}
        hint={
          current ? <span className="line-clamp-2 whitespace-pre-wrap">{current}</span> : 'Not set'
        }
        action={
          editable ? (
            <RowAction
              onClick={() => {
                save.reset()
                setValue(current)
                setEditing(true)
              }}
            >
              Edit
            </RowAction>
          ) : null
        }
      />
    )
  }
  return (
    <form
      onSubmit={(event) => void commit(event)}
      className="flex flex-col gap-2.5 border-t border-line-row px-[22px] py-3.5"
    >
      <div className="text-13 text-ink">{label}</div>
      <RaisedTextarea
        autoFocus
        aria-label={label}
        minHeight={110}
        value={value}
        onChange={(event) => setValue(event.target.value)}
      />
      {error ? (
        <p role="alert" className="text-12 text-danger">
          {error}
        </p>
      ) : hint ? (
        <p className="text-12 leading-body text-muted">{hint}</p>
      ) : null}
      <div className="flex items-center justify-end gap-4">
        <CancelButton onClick={() => setEditing(false)} />
        <RowAction type="submit" disabled={save.isPending}>
          Save
        </RowAction>
      </div>
    </form>
  )
}

// ---- Choices ----------------------------------------------------------------------------------

/**
 * One of a few named choices ("Internal", 197): the chosen one's name with Edit, which turns it
 * into a menu that saves as it is chosen. A stored value the list does not name stays
 * choosable, so nothing an administrator set another way is hidden.
 */
export function ChoiceRow({
  setting,
  label,
  hint,
  options,
  editable,
  toStored = (value) => value,
}: {
  setting: Setting
  label: string
  hint?: string
  options: ReadonlyArray<{ value: string; label: string }>
  editable: boolean
  /** The value to store for a chosen option, when it is not the option's string (a switch). */
  toStored?: (value: string) => string | boolean
}) {
  const save = useSaveSetting()
  const [editing, setEditing] = useState(false)
  const current = String(setting.value ?? '')
  const shown = options.some((o) => o.value === current)
    ? options
    : [{ value: current, label: current || 'Not set' }, ...options]
  const error = save.isError ? messageOf(save.error) : null
  const chosen = shown.find((o) => o.value === current)?.label ?? current
  if (!editing) {
    return (
      <Row
        label={label}
        hint={hint}
        error={error}
        value={chosen}
        action={
          editable ? (
            <RowAction
              onClick={() => {
                save.reset()
                setEditing(true)
              }}
            >
              Edit
            </RowAction>
          ) : null
        }
      />
    )
  }
  return (
    <Row label={label} hint={hint} error={error} action={null}>
      <span className="w-64">
        <RaisedSelect
          compact
          autoFocus
          aria-label={label}
          className="h-8!"
          disabled={save.isPending}
          value={current}
          onChange={(event) =>
            save.mutate(
              {
                params: { path: { key: setting.key } },
                body: { value: toStored(event.target.value) },
              },
              { onSuccess: () => setEditing(false) },
            )
          }
        >
          {shown.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </RaisedSelect>
      </span>
      <CancelButton onClick={() => setEditing(false)} />
    </Row>
  )
}

export const SENSITIVITIES = [
  { value: 'public', label: 'Public' },
  { value: 'internal', label: 'Internal' },
  { value: 'privileged', label: 'Privileged' },
] as const

export function SensitivityRow({ setting, editable }: { setting: Setting; editable: boolean }) {
  return (
    <ChoiceRow
      setting={setting}
      label="Default sensitivity"
      options={SENSITIVITIES}
      editable={editable}
    />
  )
}

/** An IANA time zone, read as "Pacific · PDT" (200), chosen from the browser's list. */
export function TimeZoneRow({
  setting,
  label,
  hint,
  editable,
}: {
  setting: Setting
  label: string
  hint?: string
  editable: boolean
}) {
  const current = String(setting.value ?? '')
  return (
    <ChoiceRow
      setting={setting}
      label={label}
      hint={hint}
      editable={editable}
      options={timeZones(current).map((zone) => ({
        value: zone,
        label: zone === current ? zoneLabel(zone) : zone.replaceAll('_', ' '),
      }))}
    />
  )
}

// ---- Anything else ----------------------------------------------------------------------------

/** A setting no section names: shown by the type of its value, under its key. */
export function GenericRow({ setting, editable }: { setting: Setting; editable: boolean }) {
  const hint = setting.description || undefined
  if (typeof setting.value === 'boolean')
    return (
      <BoolRow setting={setting} label={setting.key} on={hint} off={hint} editable={editable} />
    )
  if (typeof setting.value === 'number')
    return <NumberRow setting={setting} label={setting.key} hint={hint} editable={editable} />
  return <TextRow setting={setting} label={setting.key} hint={hint} editable={editable} />
}
