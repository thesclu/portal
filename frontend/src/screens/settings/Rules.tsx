import { useQueryClient } from '@tanstack/react-query'
import { useMemo, useState, type FormEvent } from 'react'

import { api, type Schemas } from '../../api/client'
import { $api } from '../../api/query'
import { isAdmin, useMe } from '../../app/session'
import { ActionDialog, unwrap, type ActionSpec, type Values } from '../../components/ActionDialog'
import { Dialog } from '../../components/Dialog'
import {
  CheckRow,
  DialogField,
  PillChoice,
  RaisedInput,
  RaisedSelect,
} from '../../components/Fields'
import { CardAction, CardHead, ListCard, ListHead, ListRow } from '../../components/ListTable'
import { Menu } from '../../components/Menu'
import { LoadFailed, Loading } from '../../components/States'
import { Button, Callout, cx } from '../../components/ui'
import { formatDate, formatShortDate } from '../../lib/dates'
import {
  DEFAULT_WEEKEND,
  WEEKDAYS,
  civilToday,
  isRetired,
  parseHolidayLines,
  weekendNames,
} from '../../lib/rules'
import { timeZones } from '../../lib/settings'
import { Closures } from './Closures'
import { Row, SettingsCard } from './rows'

/**
 * Settings → Rules & calendars (198), editable by an administrator (P18b): the rules in force
 * and the requests still waiting on one (213 adds a rule), each holiday calendar, the law each
 * counts on, and the closure days (214). Every count, date and citation here is typed by an
 * administrator; nothing in this file suggests one. Staff see the same cards without the doors.
 */

type Rule = Schemas['RuleOut']
type Calendar = Schemas['RuleCalendarOut']
type RulePatch = Schemas['RulePatchIn']
type Missing = Schemas['RulesOut']['missing'][number]

const RULES_QUERY = ['get', '/api/records/rules'] as const
const COLUMNS = '1.3fr 110px 1.4fr 110px 70px'

const KIND: Record<string, string> = {
  statutory_determination: 'Determination',
  production: 'Production',
  fee_response: 'Fee response',
  other: 'Other',
}
const DEADLINE_TYPES = [
  { value: 'statutory_determination', label: 'Determination' },
  { value: 'production', label: 'Production' },
]
const UNITS = [
  { value: 'calendar_days', label: 'Calendar days' },
  { value: 'business_days', label: 'Business days' },
]
const UNIT_WORD: Record<string, string> = {
  calendar_days: 'calendar',
  business_days: 'business',
}
const WEEKDAY_OPTIONS = WEEKDAYS.map((name, day) => ({ value: String(day), label: name }))
const OTHER = '__other__'

const text = (value: Values[string] | undefined) => String(value ?? '').trim()
const dayList = (value: Values[string] | undefined) =>
  (Array.isArray(value) ? (value as string[]) : []).map(Number).sort((a, b) => a - b)
const zoneOptions = (zones: string[]) =>
  zones.map((zone) => ({ value: zone, label: zone.replaceAll('_', ' ') }))
const lawsOf = (calendars: Calendar[]) =>
  [...new Set(calendars.flatMap((calendar) => calendar.laws))].sort()

function useRefreshRules() {
  const client = useQueryClient()
  return () => client.invalidateQueries({ queryKey: RULES_QUERY })
}

export function Rules() {
  const me = useMe()
  const admin = isAdmin(me)
  const rules = $api.useQuery('get', '/api/records/rules')
  const refresh = useRefreshRules()
  const [spec, setSpec] = useState<ActionSpec | null>(null)
  const [editing, setEditing] = useState<Rule | 'new' | null>(null)
  const zones = useMemo(() => timeZones(me.timezone), [me.timezone])
  if (rules.isPending) return <Loading what="Loading the rules" />
  if (rules.isError)
    return <LoadFailed what="the rules" error={rules.error} onRetry={() => void rules.refetch()} />
  const { rules: inForce, calendars, missing } = rules.data
  const done = () => {
    setSpec(null)
    setEditing(null)
    void refresh()
  }
  const provisions = [...new Set(inForce.map((rule) => rule.provision).filter(Boolean))].sort()
  return (
    <>
      {admin ? null : <Callout>Only an admin can change these.</Callout>}
      <RulesCard
        rules={inForce}
        missing={missing}
        admin={admin}
        onAdd={() => setEditing('new')}
        onEdit={(rule) => setEditing(rule)}
        onRetire={(rule) => setSpec(retireRule(rule))}
      />
      {calendars.map((calendar) => (
        <CalendarCard
          key={calendar.key}
          calendar={calendar}
          admin={admin}
          onEdit={() => setSpec(editCalendar(calendar, zones))}
          onAddDates={() => setSpec(addDates(calendar))}
          onName={(day) => setSpec(nameHoliday(calendar, day))}
          onChanged={refresh}
        />
      ))}
      <LawsCard
        calendars={calendars}
        admin={admin}
        onMap={() => setSpec(mapLaw(calendars.map((calendar) => calendar.key)))}
        onAddCalendar={() => setSpec(addCalendar(zones, me.timezone))}
        onChanged={refresh}
      />
      <Closures />
      <ActionDialog spec={spec} onClose={() => setSpec(null)} onDone={done} />
      {editing ? (
        <RuleDialog
          rule={editing === 'new' ? null : editing}
          laws={lawsOf(calendars)}
          provisions={provisions}
          onClose={() => setEditing(null)}
          onDone={done}
        />
      ) : null}
    </>
  )
}

// ---- Rules in force ---------------------------------------------------------------------------

function RulesCard({
  rules,
  missing,
  admin,
  onAdd,
  onEdit,
  onRetire,
}: {
  rules: Rule[]
  missing: Missing[]
  admin: boolean
  onAdd: () => void
  onEdit: (rule: Rule) => void
  onRetire: (rule: Rule) => void
}) {
  const me = useMe()
  const today = civilToday(me.timezone)
  // The law leads a rule's name only when more than one law has rules (198 has one).
  const several = new Set(rules.map((rule) => rule.law)).size > 1
  return (
    <ListCard aria-label="Rules in force">
      <CardHead title="Rules in force" />
      <ListHead columns={COLUMNS} ruled="both">
        <span>Rule</span>
        <span>Days</span>
        <span>Citation</span>
        <span>In force from</span>
        <span />
      </ListHead>
      {rules.length === 0 && missing.length === 0 ? (
        <p className="px-9 py-[11px] text-125 text-muted">No rules entered yet.</p>
      ) : null}
      {rules.map((rule, index) => {
        const retired = isRetired(rule, today)
        return (
          <ListRow
            key={rule.id}
            columns={COLUMNS}
            index={index}
            zebra={false}
            className={retired ? 'text-muted' : undefined}
          >
            <span className="min-w-0 truncate" title={rule.id}>
              {several ? `${rule.law} · ` : ''}
              {KIND[rule.deadline_type] ?? rule.deadline_type} · {rule.provision || 'general'}
            </span>
            <span className="min-w-0 truncate">
              {rule.count} {UNIT_WORD[rule.unit] ?? rule.unit.replaceAll('_', ' ')}
            </span>
            <span className="min-w-0 truncate" title={rule.citation}>
              {rule.citation}
            </span>
            <span
              className="min-w-0 truncate"
              title={rule.effective_to ? `Until ${formatDate(rule.effective_to)}` : undefined}
            >
              {retired
                ? `Ended ${formatShortDate(rule.effective_to)}`
                : formatDate(rule.effective_from)}
            </span>
            <span className="min-w-0 justify-self-end">
              {admin ? (
                <Menu
                  label={`More on ${KIND[rule.deadline_type] ?? rule.deadline_type} · ${rule.provision || 'general'}`}
                  width={160}
                  items={[
                    { label: 'Edit…', onPick: () => onEdit(rule) },
                    ...(retired
                      ? []
                      : [{ label: 'Retire…', danger: true, onPick: () => onRetire(rule) }]),
                  ]}
                  buttonClassName="px-1 text-125 text-ink hover:text-brand"
                >
                  ⋯
                </Menu>
              ) : null}
            </span>
          </ListRow>
        )
      })}
      {missing.map((entry, index) => (
        <ListRow
          key={entry.task.id}
          columns={COLUMNS}
          index={rules.length + index}
          zebra={false}
          aria-label={`${entry.tracking_number} · ${entry.agency_name}`}
        >
          <span className="min-w-0 truncate" title={entry.task.description}>
            {entry.task.description}
          </span>
          <span className="text-muted">—</span>
          <span className="min-w-0 truncate text-ink-2">
            {entry.tracking_number} · {entry.agency_name}
          </span>
          <span className="text-warn">No rule entered</span>
          <span className="justify-self-end">
            {admin ? (
              <button
                type="button"
                onClick={onAdd}
                className="font-medium text-brand hover:text-brand-dark"
              >
                Enter
              </button>
            ) : null}
          </span>
        </ListRow>
      ))}
      {admin ? (
        <div className="border-t border-line-row px-5 py-3 leading-[normal]">
          <CardAction onClick={onAdd}>＋ Add a rule</CardAction>
        </div>
      ) : null}
    </ListCard>
  )
}

function retireRule(rule: Rule): ActionSpec {
  return {
    key: `retire-${rule.id}`,
    title: 'Retire rule',
    subtitle: `${KIND[rule.deadline_type] ?? rule.deadline_type} · ${rule.law} · ${rule.provision || 'general'}`,
    compact: true,
    submit: 'Retire',
    danger: true,
    fields: [
      {
        name: 'effective_to',
        label: 'Ends on',
        type: 'date',
        required: true,
        initial: rule.effective_to ?? '',
      },
    ],
    run: async (values) => {
      unwrap(
        await api.POST('/api/core/rules/{rule_id}/retire', {
          params: { path: { rule_id: rule.id } },
          body: { effective_to: text(values.effective_to) },
        }),
      )
    },
  }
}

/** A rule's id as an administrator would write it: law.type.provision.date. Offered, editable. */
function suggestedId(law: string, type: string, provision: string, from: string): string {
  const part = (value: string) =>
    value
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '')
  const kind = type === 'statutory_determination' ? 'determination' : type
  return [part(law), part(kind), part(provision) || 'general', from].filter(Boolean).join('.')
}

/**
 * Add a rule (213), or edit one. Law · Applies to, Deadline · Days, Count, Citation, In force
 * from, as drawn; then what the API also takes and 213 does not draw: an end date, whether a
 * date that falls on a holiday moves, and the rule's id (offered from the rest). An edit sends
 * only what changed.
 */
function RuleDialog({
  rule,
  laws,
  provisions,
  onClose,
  onDone,
}: {
  rule: Rule | null
  laws: string[]
  provisions: string[]
  onClose: () => void
  onDone: () => void
}) {
  const knownLaw = rule ? laws.includes(rule.law) : true
  const knownProvision = rule ? !rule.provision || provisions.includes(rule.provision) : true
  const [lawChoice, setLawChoice] = useState(rule ? (knownLaw ? rule.law : OTHER) : (laws[0] ?? ''))
  const [lawOther, setLawOther] = useState(rule && !knownLaw ? rule.law : '')
  const [provisionChoice, setProvisionChoice] = useState(
    rule ? (knownProvision ? rule.provision : OTHER) : '',
  )
  const [provisionOther, setProvisionOther] = useState(
    rule && !knownProvision ? rule.provision : '',
  )
  const [deadlineType, setDeadlineType] = useState(rule?.deadline_type ?? 'statutory_determination')
  const [count, setCount] = useState(rule ? String(rule.count) : '')
  const [unit, setUnit] = useState(rule?.unit ?? 'calendar_days')
  const [rolls, setRolls] = useState(rule?.roll_forward_on_non_business_day ?? true)
  const [from, setFrom] = useState(rule?.effective_from ?? '')
  const [to, setTo] = useState(rule?.effective_to ?? '')
  const [citation, setCitation] = useState(rule?.citation ?? '')
  const [ruleId, setRuleId] = useState('')
  // 213 draws the first fields only; the end date, the holiday rule and the id open on asking.
  const [more, setMore] = useState(
    rule ? Boolean(rule.effective_to) || !rule.roll_forward_on_non_business_day : false,
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const formId = rule ? `edit-rule-${rule.id}` : 'add-rule'
  const law = (lawChoice === OTHER ? lawOther : lawChoice).trim()
  const provision = (provisionChoice === OTHER ? provisionOther : provisionChoice).trim()
  const offeredId = suggestedId(law, deadlineType, provision, from)

  async function submit(event: FormEvent) {
    event.preventDefault()
    const number = Number(count)
    if (!law) return setError('Choose the law.')
    if (!Number.isInteger(number) || number <= 0)
      return setError('Days is a whole number above zero.')
    if (!citation.trim()) return setError('The citation is required.')
    if (to && to <= from) return setError('The end must be after In force from.')
    const fields = {
      law,
      provision,
      deadline_type: deadlineType,
      count: number,
      unit,
      roll_forward_on_non_business_day: rolls,
      effective_from: from,
      effective_to: to || null,
      citation: citation.trim(),
    }
    setBusy(true)
    setError(null)
    try {
      if (rule) {
        const patch: RulePatch = {}
        for (const key of Object.keys(fields) as Array<keyof typeof fields>) {
          if (rule[key] !== fields[key]) (patch as Record<string, unknown>)[key] = fields[key]
        }
        if (Object.keys(patch).length === 0) throw new Error('Nothing was changed.')
        unwrap(
          await api.PATCH('/api/core/rules/{rule_id}', {
            params: { path: { rule_id: rule.id } },
            body: patch,
          }),
        )
      } else {
        unwrap(
          await api.POST('/api/core/rules', {
            body: { rule_id: (ruleId.trim() || offeredId).trim(), ...fields },
          }),
        )
      }
      onDone()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      title={rule ? 'Edit rule' : 'Add a rule'}
      subtitle={rule ? rule.id : undefined}
      open
      onClose={onClose}
      width={520}
      top={60}
      footer={
        <>
          <Button kind="text" pill onClick={onClose}>
            Cancel
          </Button>
          <Button kind="primary" pill type="submit" form={formId} busy={busy}>
            {rule ? 'Save' : 'Add rule'}
          </Button>
        </>
      }
    >
      <form id={formId} className="flex flex-col gap-3.5" onSubmit={(event) => void submit(event)}>
        <div className="flex gap-3">
          <div className="min-w-0 flex-1">
            <DialogField label="Law">
              <RaisedSelect
                compact
                required
                value={lawChoice}
                onChange={(event) => setLawChoice(event.target.value)}
              >
                {laws.length === 0 ? <option value="">Choose…</option> : null}
                {laws.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
                <option value={OTHER}>Another law…</option>
              </RaisedSelect>
            </DialogField>
          </div>
          <div className="min-w-0 flex-1">
            <DialogField label="Applies to">
              <RaisedSelect
                compact
                value={provisionChoice}
                onChange={(event) => setProvisionChoice(event.target.value)}
              >
                <option value="">Every request (general)</option>
                {provisions.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
                <option value={OTHER}>Another provision…</option>
              </RaisedSelect>
            </DialogField>
          </div>
        </div>
        {lawChoice === OTHER || provisionChoice === OTHER ? (
          <div className="flex gap-3">
            <div className="min-w-0 flex-1">
              {lawChoice === OTHER ? (
                <DialogField label="Which law" hint="It must already be mapped to a calendar.">
                  <RaisedInput
                    compact
                    required
                    value={lawOther}
                    onChange={(event) => setLawOther(event.target.value)}
                  />
                </DialogField>
              ) : null}
            </div>
            <div className="min-w-0 flex-1">
              {provisionChoice === OTHER ? (
                <DialogField label="Which provision">
                  <RaisedInput
                    compact
                    required
                    value={provisionOther}
                    onChange={(event) => setProvisionOther(event.target.value)}
                  />
                </DialogField>
              ) : null}
            </div>
          </div>
        ) : null}
        <div className="flex gap-3">
          <div className="min-w-0 flex-1">
            <DialogField label="Deadline">
              <RaisedSelect
                compact
                value={deadlineType}
                onChange={(event) => setDeadlineType(event.target.value)}
              >
                {DEADLINE_TYPES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </RaisedSelect>
            </DialogField>
          </div>
          <div className="min-w-0 flex-1">
            <DialogField label="Days">
              <RaisedInput
                compact
                type="number"
                inputMode="numeric"
                step={1}
                min={1}
                required
                autoFocus
                data-autofocus
                className="[appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
                value={count}
                onChange={(event) => setCount(event.target.value)}
              />
            </DialogField>
          </div>
        </div>
        <DialogField label="Count">
          <PillChoice label="Count" options={UNITS} value={unit} onChange={setUnit} />
        </DialogField>
        <DialogField label="Citation" required>
          <RaisedInput
            compact
            required
            value={citation}
            onChange={(event) => setCitation(event.target.value)}
          />
        </DialogField>
        <DialogField label="In force from">
          <RaisedInput
            compact
            type="date"
            required
            value={from}
            onChange={(event) => setFrom(event.target.value)}
          />
        </DialogField>
        {more ? (
          <>
            <DialogField label="Ends" hint="Leave empty while it stands.">
              <RaisedInput
                compact
                type="date"
                min={from || undefined}
                value={to}
                onChange={(event) => setTo(event.target.value)}
              />
            </DialogField>
            <CheckRow checked={rolls} onChange={setRolls}>
              A date that falls on a weekend or holiday moves to the next business day
            </CheckRow>
            {rule ? null : (
              <DialogField
                label="Rule id"
                hint="A short stable name; offered from the fields above."
              >
                <RaisedInput
                  compact
                  placeholder={offeredId}
                  value={ruleId}
                  onChange={(event) => setRuleId(event.target.value)}
                />
              </DialogField>
            )}
          </>
        ) : (
          <button
            type="button"
            onClick={() => setMore(true)}
            className="self-start text-125 font-medium text-brand hover:text-brand-dark"
          >
            {rule ? 'An end date, holidays…' : 'An end date, holidays, the id…'}
          </button>
        )}
        {error ? (
          <p
            role="alert"
            className="rounded-10 bg-danger-tint px-3.5 py-[11px] text-125 text-danger-2"
          >
            {error}
          </p>
        ) : null}
      </form>
    </Dialog>
  )
}

// ---- Holiday calendars ------------------------------------------------------------------------

function CalendarCard({
  calendar,
  admin,
  onEdit,
  onAddDates,
  onName,
  onChanged,
}: {
  calendar: Calendar
  admin: boolean
  onEdit: () => void
  onAddDates: () => void
  onName: (day: Calendar['holidays'][number]) => void
  onChanged: () => Promise<void>
}) {
  const [all, setAll] = useState(false)
  const [removing, setRemoving] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const dates = [...calendar.holidays].sort((a, b) => a.date.localeCompare(b.date))
  const upcoming = calendar.upcoming_holidays.slice(0, 5)

  async function remove(on: string) {
    setRemoving(on)
    setError(null)
    try {
      unwrap(
        await api.DELETE('/api/core/calendars/{key}/holidays/{on}', {
          params: { path: { key: calendar.key, on } },
        }),
      )
      await onChanged()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Something went wrong.')
    } finally {
      setRemoving(null)
    }
  }

  return (
    <ListCard aria-label={`Holiday calendar · ${calendar.key}`}>
      <CardHead
        title={`Holiday calendar · ${calendar.key}`}
        action={
          <span className="text-12 text-warn">
            {calendar.last_holiday
              ? `Runs out ${formatDate(calendar.last_holiday)}`
              : 'No dates entered'}
          </span>
        }
      />
      <div className="px-5 pb-4 text-125 leading-[1.7] text-ink-2">
        Weekend {weekendNames(calendar.weekend)}, in {calendar.timezone.replaceAll('_', ' ')}.{' '}
        {calendar.laws.length > 0
          ? `Counts the clocks of ${calendar.laws.join(', ')}.`
          : 'No law counts on it yet.'}
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          {upcoming.map((day) => (
            <span
              key={day.date}
              className="rounded-6 border border-line bg-page px-[9px] py-[3px] text-12 leading-[normal] text-ink-2"
            >
              {formatShortDate(day.date)}
              {day.name ? ` · ${day.name}` : ''}
            </span>
          ))}
          <button
            type="button"
            onClick={() => setAll((was) => !was)}
            className="ml-1 self-center text-12 leading-[normal] text-brand hover:text-brand-dark"
          >
            {all ? 'Fewer dates' : `All ${calendar.holiday_count} dates`}
          </button>
          {admin ? (
            <>
              <button
                type="button"
                onClick={onAddDates}
                className="ml-2 self-center text-12 leading-[normal] text-brand hover:text-brand-dark"
              >
                Add dates
              </button>
              <button
                type="button"
                onClick={onEdit}
                className="ml-2 self-center text-12 leading-[normal] text-brand hover:text-brand-dark"
              >
                Edit calendar
              </button>
            </>
          ) : null}
        </div>
        {all ? (
          <ul className="mt-3 max-h-72 overflow-y-auto">
            {dates.length === 0 ? <li className="text-12 text-muted">None entered.</li> : null}
            {dates.map((day) => (
              <li
                key={day.date}
                className="flex items-center gap-3 border-t border-line-row py-1.5 text-125 leading-[normal]"
              >
                <span className="w-28 whitespace-nowrap text-ink">{formatDate(day.date)}</span>
                <span className="min-w-0 flex-1 truncate text-muted">{day.name ?? ''}</span>
                {admin ? (
                  <>
                    <button
                      type="button"
                      onClick={() => onName(day)}
                      className="text-12 font-medium text-brand hover:text-brand-dark"
                    >
                      {day.name ? 'Rename' : 'Name'}
                    </button>
                    <button
                      type="button"
                      disabled={removing === day.date}
                      onClick={() => void remove(day.date)}
                      className="text-12 font-medium text-ink-2 hover:text-danger disabled:opacity-55"
                    >
                      Remove
                    </button>
                  </>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
        {error ? (
          <p role="alert" className="mt-2 text-12 text-danger">
            {error}
          </p>
        ) : null}
      </div>
    </ListCard>
  )
}

function addCalendar(zones: string[], current: string): ActionSpec {
  return {
    key: 'add-calendar',
    title: 'Add a calendar',
    compact: true,
    submit: 'Add',
    fields: [
      { name: 'key', label: 'Key', required: true, hint: 'A short stable name.' },
      {
        name: 'timezone',
        label: 'Time zone',
        type: 'select',
        required: true,
        initial: current,
        options: zoneOptions(zones),
      },
      {
        name: 'weekend',
        label: 'Weekend',
        type: 'checks',
        initial: DEFAULT_WEEKEND.map(String),
        options: WEEKDAY_OPTIONS,
      },
    ],
    run: async (values) => {
      unwrap(
        await api.POST('/api/core/calendars', {
          body: {
            key: text(values.key),
            timezone: text(values.timezone),
            weekend: dayList(values.weekend),
          },
        }),
      )
    },
  }
}

function editCalendar(calendar: Calendar, zones: string[]): ActionSpec {
  return {
    key: `edit-calendar-${calendar.key}`,
    title: 'Edit calendar',
    subtitle: calendar.key,
    compact: true,
    submit: 'Save',
    fields: [
      {
        name: 'timezone',
        label: 'Time zone',
        type: 'select',
        required: true,
        initial: calendar.timezone,
        options: zoneOptions(zones),
      },
      {
        name: 'weekend',
        label: 'Weekend',
        type: 'checks',
        initial: calendar.weekend.map(String),
        options: WEEKDAY_OPTIONS,
      },
    ],
    run: async (values) => {
      unwrap(
        await api.PATCH('/api/core/calendars/{key}', {
          params: { path: { key: calendar.key } },
          body: { timezone: text(values.timezone), weekend: dayList(values.weekend) },
        }),
      )
    },
  }
}

function addDates(calendar: Calendar): ActionSpec {
  return {
    key: `add-dates-${calendar.key}`,
    title: 'Add dates',
    subtitle: calendar.key,
    submit: 'Add',
    fields: [
      {
        name: 'lines',
        label: 'Dates',
        type: 'textarea',
        required: true,
        hint: 'One per line: the date as YYYY-MM-DD, then a space and a name if you like.',
      },
    ],
    run: async (values) => {
      const { holidays, errors } = parseHolidayLines(text(values.lines))
      if (errors.length > 0) throw new Error(errors.join(' '))
      if (holidays.length === 0) throw new Error('Enter at least one date.')
      unwrap(
        await api.POST('/api/core/calendars/{key}/holidays', {
          params: { path: { key: calendar.key } },
          body: { holidays },
        }),
      )
    },
  }
}

/** A holiday's name, typed by an administrator (the portal never fills one in); empty clears it. */
function nameHoliday(calendar: Calendar, day: Calendar['holidays'][number]): ActionSpec {
  return {
    key: `name-${calendar.key}-${day.date}`,
    title: `Name ${formatDate(day.date)}`,
    subtitle: calendar.key,
    compact: true,
    submit: 'Save',
    fields: [
      {
        name: 'name',
        label: 'Name',
        initial: day.name ?? '',
        hint: 'Shown on the Calendar. Leave empty for none.',
      },
    ],
    run: async (values) => {
      unwrap(
        await api.PATCH('/api/core/calendars/{key}/holidays/{on}', {
          params: { path: { key: calendar.key, on: day.date } },
          body: { name: text(values.name) || null },
        }),
      )
    },
  }
}

// ---- Laws -------------------------------------------------------------------------------------

function mapLaw(keys: string[]): ActionSpec {
  return {
    key: 'map-law',
    title: 'Map a law',
    compact: true,
    submit: 'Map',
    fields: [
      { name: 'law', label: 'Law', required: true, hint: 'As agencies name it under Records law.' },
      {
        name: 'calendar',
        label: 'Calendar',
        type: 'select',
        required: true,
        options: keys.map((key) => ({ value: key, label: key })),
      },
    ],
    run: async (values) => {
      unwrap(
        await api.PUT('/api/core/laws/{law}', {
          params: { path: { law: text(values.law) } },
          body: { calendar: text(values.calendar) },
        }),
      )
    },
  }
}

/** Every law and the calendar it counts on; an admin moves one, maps a new one, or adds a
 *  calendar. A rule's law must be mapped first. */
function LawsCard({
  calendars,
  admin,
  onMap,
  onAddCalendar,
  onChanged,
}: {
  calendars: Calendar[]
  admin: boolean
  onMap: () => void
  onAddCalendar: () => void
  onChanged: () => Promise<void>
}) {
  const keys = calendars.map((calendar) => calendar.key)
  const mapped = calendars
    .flatMap((calendar) => calendar.laws.map((law) => ({ law, calendar: calendar.key })))
    .sort((a, b) => a.law.localeCompare(b.law))
  return (
    <SettingsCard
      title="Laws"
      aside={
        admin ? (
          <>
            <CardAction onClick={onAddCalendar}>＋ Add a calendar</CardAction>
            {keys.length > 0 ? <CardAction onClick={onMap}>＋ Map a law</CardAction> : null}
          </>
        ) : null
      }
    >
      {mapped.length === 0 ? (
        <Row label="No law is mapped yet." hint="Each law counts its clocks on one calendar." />
      ) : (
        mapped.map((entry) => (
          <LawRow
            key={entry.law}
            law={entry.law}
            calendar={entry.calendar}
            keys={keys}
            admin={admin}
            onChanged={onChanged}
          />
        ))
      )}
    </SettingsCard>
  )
}

function LawRow({
  law,
  calendar,
  keys,
  admin,
  onChanged,
}: {
  law: string
  calendar: string
  keys: string[]
  admin: boolean
  onChanged: () => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function move(to: string) {
    if (to === calendar) return
    setBusy(true)
    setError(null)
    try {
      unwrap(
        await api.PUT('/api/core/laws/{law}', {
          params: { path: { law } },
          body: { calendar: to },
        }),
      )
      await onChanged()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Row label={law} hint="Counts on" error={error} value={admin ? undefined : calendar}>
      {admin ? (
        <span className={cx('w-56', busy && 'opacity-55')}>
          <RaisedSelect
            compact
            aria-label={`Calendar for ${law}`}
            className="h-8!"
            value={calendar}
            disabled={busy}
            onChange={(event) => void move(event.target.value)}
          >
            {keys.map((key) => (
              <option key={key} value={key}>
                {key}
              </option>
            ))}
          </RaisedSelect>
        </span>
      ) : null}
    </Row>
  )
}
