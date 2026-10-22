import { Fragment, useState, type FormEvent, type ReactNode } from 'react'

import { Dialog } from './Dialog'
import {
  CheckRow,
  DialogField,
  RadioCard,
  RaisedInput,
  RaisedSelect,
  RaisedTextarea,
  ReadonlyBox,
  Segmented,
} from './Fields'
import { Button, cx } from './ui'

/**
 * One dialog for every "do something to this request" action.
 *
 * The request page has some forty of them, and each is the same thing: a few fields, a note
 * saying why, one call, and what to do when the call is refused. An action is described as data
 * (`ActionSpec`) and this renders it, so every dialog validates, reports a refusal in the API's
 * own words, and offers "Refresh" on a conflict in exactly the same way.
 *
 * Drawn as the v4 dialogs draw it (070–104): raised fields, the read-only value on grey, choice
 * cards, segmented choices, two fields to a row; a conflict is the red callout at the top and the
 * primary button becomes "Refresh" (094).
 */
export type Values = Record<string, string | boolean | string[] | File[]>

export interface FieldOption {
  value: string
  label: string
  /** A card's second line (073 "Gov. Code …", 075 "Stays on … and appears on …"). */
  hint?: string
}

export interface FieldSpec {
  name: string
  label: string
  type?:
    | 'text'
    | 'password'
    | 'textarea'
    | 'date'
    | 'datetime'
    | 'time'
    | 'number'
    | 'select'
    | 'checkbox'
    | 'checks'
    | 'files'
    /** One of a few, side by side (072, 085, 089). */
    | 'segmented'
    /** One of a few, each a card with its own words (073, 075, 087, 099). */
    | 'cards'
    /** The value as it stands, on grey (070 "Now"). */
    | 'readonly'
    /** Drawn by the caller (`render`): a picker list, a search. */
    | 'custom'
  options?: FieldOption[]
  required?: boolean
  /** Draw "required" beside the label. The screens draw it on the written fields (a reason, a
   *  summary) and not on a date or a choice that always has a value; that is the default. */
  marked?: boolean
  hint?: ReactNode
  initial?: string | boolean | string[]
  placeholder?: string
  /** The label is read out but not drawn (102's drop zone). */
  hideLabel?: boolean
  /** Has the focus when the dialog opens (071 "New", 083 "Their reference number"). */
  autoFocus?: boolean
  /** A textarea's least height in px (the screens vary it: 46–80). */
  minHeight?: number
  /** Fields with the same row key sit side by side (072 Date · Time, 086 Amount · Pay by). */
  row?: string
  /** `cardsField:value`: shown under that card, and only while it is the one chosen (087). */
  under?: string
  /** Cards drawn with 11 px of padding, not 12 (087). */
  tight?: boolean
  /** Shown only when this returns true for the current values. */
  when?: (values: Values) => boolean
  render?: (
    value: Values[string],
    onChange: (value: Values[string]) => void,
    values: Values,
  ) => ReactNode
}

export interface ActionSpec {
  key: string
  title: string
  /** The 12.5 px muted line under the title: what the action is on. */
  subtitle?: string
  intro?: ReactNode | ((values: Values) => ReactNode)
  fields: FieldSpec[]
  submit: string | ((values: Values) => string)
  danger?: boolean
  /** Shown instead of the form: the action is not open to this person, and why. */
  refused?: string
  /** The dialog's width and distance from the top, as each screen draws it. */
  width?: number
  top?: number
  /** 38 px fields (079–104) rather than 40 (070–072). */
  compact?: boolean
  /** A quiet button left of Cancel (084 "Print letter", 100 "Open in editor"). */
  secondary?: { label: string; run: (values: Values) => void }
  /** Words at the footer's left (079 "Status becomes …"). */
  footnote?: (values: Values) => ReactNode
  /** The Inbox's dialog look (159): a 17 px title, the footer on a grey band. */
  look?: 'default' | 'inbox'
  run: (values: Values) => Promise<void>
}

/** Thrown by `run` so the dialog can tell "it changed underneath you" from any other refusal. */
export class ActionError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/** Unwraps an openapi-fetch result, or throws what the API said. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || !result.response.ok) {
    const body = result.error as { detail?: unknown } | undefined
    const detail =
      typeof body?.detail === 'string'
        ? body.detail
        : Array.isArray(body?.detail)
          ? 'Some of what was entered is not valid.'
          : 'The server refused this.'
    throw new ActionError(result.response.status, detail)
  }
  return result.data as T
}

/** Consecutive fields with the same row key, side by side. */
function inRows(fields: FieldSpec[]): FieldSpec[][] {
  const groups: FieldSpec[][] = []
  for (const field of fields) {
    const last = groups.at(-1)
    if (field.row && last && last[0].row === field.row) last.push(field)
    else groups.push([field])
  }
  return groups
}

function initialValues(spec: ActionSpec): Values {
  const values: Values = {}
  for (const field of spec.fields) {
    values[field.name] =
      field.initial ??
      (field.type === 'checkbox'
        ? false
        : field.type === 'checks' || field.type === 'files'
          ? []
          : '')
  }
  return values
}

export function ActionDialog({
  spec,
  onClose,
  onDone,
}: {
  spec: ActionSpec | null
  onClose: () => void
  onDone: () => void
}) {
  return spec ? <ActionForm key={spec.key} spec={spec} onClose={onClose} onDone={onDone} /> : null
}

function ActionForm({
  spec,
  onClose,
  onDone,
}: {
  spec: ActionSpec
  onClose: () => void
  onDone: () => void
}) {
  const [values, setValues] = useState<Values>(() => initialValues(spec))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ActionError | Error | null>(null)
  const set = (name: string, value: Values[string]) =>
    setValues((current) => ({ ...current, [name]: value }))
  const visible = (field: FieldSpec) => !field.when || field.when(values)
  const formId = `action-${spec.key}`

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await spec.run(values)
      onDone()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('Something went wrong.'))
    } finally {
      setBusy(false)
    }
  }

  const conflict = error instanceof ActionError && error.status === 409
  const intro = typeof spec.intro === 'function' ? spec.intro(values) : spec.intro
  const submitLabel = typeof spec.submit === 'function' ? spec.submit(values) : spec.submit
  const footnote = spec.footnote?.(values)

  // Fields drawn under a card are taken out of the flow; the rest are grouped into rows.
  const groups = inRows(spec.fields.filter((field) => !field.under && visible(field)))
  const control = (field: FieldSpec) => (
    <FieldControl
      key={field.name}
      field={field}
      compact={spec.compact}
      value={values[field.name]}
      values={values}
      onChange={(v) => set(field.name, v)}
      under={(option) =>
        spec.fields.filter((child) => child.under === `${field.name}:${option}` && visible(child))
      }
      renderChild={(child) => (
        <FieldControl
          key={child.name}
          field={child}
          compact={spec.compact}
          value={values[child.name]}
          values={values}
          onChange={(v) => set(child.name, v)}
          under={() => []}
          renderChild={() => null}
        />
      )}
    />
  )

  return (
    <Dialog
      title={spec.title}
      subtitle={spec.subtitle}
      open
      onClose={onClose}
      width={spec.width}
      top={spec.top}
      look={spec.look}
      footer={
        spec.refused ? (
          <Button kind="text" pill onClick={onClose}>
            Close
          </Button>
        ) : spec.look === 'inbox' ? (
          <>
            {footnote ? (
              <span className="mr-auto min-w-0 text-12 text-muted">{footnote}</span>
            ) : null}
            <button
              type="button"
              onClick={onClose}
              className="box-border inline-flex h-8 items-center rounded-9 px-[15px] text-125 font-medium whitespace-nowrap text-ink-2 hover:bg-hover"
            >
              Cancel
            </button>
            {conflict ? (
              <button
                type="button"
                onClick={onDone}
                className="box-border inline-flex h-8 items-center rounded-pill border-[1.5px] border-brand bg-brand px-[15px] text-125 font-medium whitespace-nowrap text-white"
              >
                Refresh
              </button>
            ) : (
              <button
                type="submit"
                form={formId}
                disabled={busy}
                aria-busy={busy || undefined}
                className={cx(
                  'box-border inline-flex h-8 items-center border-[1.5px] px-[15px] text-125 font-medium whitespace-nowrap text-white disabled:opacity-55',
                  spec.danger
                    ? 'rounded-9 border-danger bg-danger hover:bg-danger-2'
                    : 'rounded-pill border-brand bg-brand hover:bg-brand-dark',
                )}
              >
                {submitLabel}
              </button>
            )}
          </>
        ) : (
          <>
            {footnote ? (
              <span className="mr-auto min-w-0 text-125 text-ink-2">{footnote}</span>
            ) : null}
            {spec.secondary ? (
              <button
                type="button"
                onClick={() => spec.secondary?.run(values)}
                className="inline-flex h-8 items-center gap-1.5 rounded-9 px-3.5 text-125 font-medium whitespace-nowrap text-ink-2 hover:bg-hover"
              >
                {spec.secondary.label}
              </button>
            ) : null}
            <Button kind="text" pill onClick={onClose}>
              Cancel
            </Button>
            {conflict ? (
              <Button kind="primary" pill onClick={onDone}>
                Refresh
              </Button>
            ) : (
              <Button
                kind={spec.danger ? 'danger' : 'primary'}
                pill
                type="submit"
                form={formId}
                busy={busy}
              >
                {submitLabel}
              </Button>
            )}
          </>
        )
      }
    >
      {spec.refused ? (
        <p className="text-13 text-ink-2">{spec.refused}</p>
      ) : (
        <form
          id={formId}
          className="flex flex-col gap-3.5"
          onSubmit={(event) => void submit(event)}
        >
          {conflict ? (
            <div
              role="alert"
              className="flex gap-2.5 rounded-10 bg-danger-tint px-3.5 py-[11px] text-125 leading-[1.5] text-danger-2"
            >
              <span className="font-semibold whitespace-nowrap">Couldn’t save.</span>
              <span>{error.message}</span>
            </div>
          ) : null}
          {intro ? <div className="text-13 leading-body text-ink-2">{intro}</div> : null}
          {groups.map((group) =>
            group.length === 1 ? (
              control(group[0])
            ) : (
              <div key={group.map((f) => f.name).join('+')} className="flex gap-3">
                {group.map((field) => (
                  <div key={field.name} className="min-w-0 flex-1">
                    {control(field)}
                  </div>
                ))}
              </div>
            ),
          )}
          {error && !conflict ? (
            <div
              role="alert"
              className="rounded-10 bg-danger-tint px-3.5 py-[11px] text-125 leading-[1.5] text-danger-2"
            >
              {error.message}
            </div>
          ) : null}
        </form>
      )}
    </Dialog>
  )
}

function FieldControl({
  field,
  compact,
  value,
  values,
  onChange,
  under,
  renderChild,
}: {
  field: FieldSpec
  compact?: boolean
  value: Values[string]
  values: Values
  onChange: (value: Values[string]) => void
  under: (option: string) => FieldSpec[]
  renderChild: (field: FieldSpec) => ReactNode
}) {
  const text = typeof value === 'string' ? value : ''
  if (field.type === 'custom') {
    return <Fragment>{field.render?.(value, onChange, values)}</Fragment>
  }
  if (field.type === 'checkbox') {
    return (
      <CheckRow checked={value === true} onChange={(checked) => onChange(checked)}>
        {field.label}
        {field.hint ? <span className="block text-12 text-muted">{field.hint}</span> : null}
      </CheckRow>
    )
  }
  if (field.type === 'checks') {
    const chosen = Array.isArray(value) ? (value as string[]) : []
    return (
      <fieldset className="flex flex-col gap-1.5">
        <legend className="mb-1.5 text-125 text-ink-2">{field.label}</legend>
        {(field.options ?? []).length === 0 ? (
          <p className="text-12 text-muted">There is nothing to choose from.</p>
        ) : null}
        {(field.options ?? []).map((option) => (
          <CheckRow
            key={option.value}
            checked={chosen.includes(option.value)}
            onChange={(checked) =>
              onChange(
                checked ? [...chosen, option.value] : chosen.filter((v) => v !== option.value),
              )
            }
          >
            {option.label}
          </CheckRow>
        ))}
        {field.hint ? <p className="text-12 text-muted">{field.hint}</p> : null}
      </fieldset>
    )
  }
  if (field.type === 'cards') {
    return (
      <div role="radiogroup" aria-label={field.label} className="flex flex-col gap-3.5">
        {(field.options ?? []).map((option) => (
          <Fragment key={option.value}>
            <RadioCard
              name={field.name}
              checked={text === option.value}
              onChange={() => onChange(option.value)}
              title={option.label}
              hint={option.hint}
              tight={field.tight}
            />
            {text === option.value
              ? inRows(under(option.value)).map((group) =>
                  group.length === 1 ? (
                    renderChild(group[0])
                  ) : (
                    <div key={group.map((f) => f.name).join('+')} className="flex gap-3">
                      {group.map((child) => (
                        <div key={child.name} className="min-w-0 flex-1">
                          {renderChild(child)}
                        </div>
                      ))}
                    </div>
                  ),
                )
              : null}
          </Fragment>
        ))}
      </div>
    )
  }
  if (field.type === 'segmented') {
    return (
      <DialogField label={field.label} hint={field.hint}>
        <Segmented
          label={field.label}
          options={field.options ?? []}
          value={text}
          onChange={onChange}
          size={compact ? 'sm' : 'md'}
        />
      </DialogField>
    )
  }
  if (field.type === 'files') {
    return (
      <DialogField
        label={field.label}
        hint={field.hint}
        required={field.required}
        hideLabel={field.hideLabel}
      >
        <span
          className={cx(
            'relative block rounded-10 border-[1.5px] border-dashed border-line-control bg-surface-row p-3.5',
            'text-center text-125 text-ink-2',
          )}
        >
          {Array.isArray(value) && value.length > 0 ? (
            (value as File[]).map((file) => file.name).join(', ')
          ) : (
            <>
              Drop {field.label.toLowerCase()} or{' '}
              <span className="font-medium text-brand">browse</span>
            </>
          )}
          <input
            type="file"
            multiple
            required={field.required}
            aria-label={field.label}
            className="absolute inset-0 cursor-pointer opacity-0"
            onChange={(e) => onChange([...(e.target.files ?? [])])}
          />
        </span>
      </DialogField>
    )
  }
  return (
    <DialogField
      label={field.label}
      hint={field.hint}
      required={field.marked ?? (field.required && field.type === 'textarea')}
    >
      {field.type === 'readonly' ? (
        <ReadonlyBox compact={compact}>{text}</ReadonlyBox>
      ) : field.type === 'select' ? (
        <RaisedSelect
          compact={compact}
          required={field.required}
          value={text}
          onChange={(e) => onChange(e.target.value)}
        >
          {field.required && !field.initial ? <option value="">Choose…</option> : null}
          {(field.options ?? []).map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </RaisedSelect>
      ) : field.type === 'textarea' ? (
        <RaisedTextarea
          required={field.required}
          minHeight={field.minHeight}
          autoFocus={field.autoFocus}
          data-autofocus={field.autoFocus || undefined}
          placeholder={field.placeholder}
          rows={1}
          value={text}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : (
        <RaisedInput
          compact={compact}
          type={
            field.type === 'datetime'
              ? 'datetime-local'
              : field.type === 'date' ||
                  field.type === 'time' ||
                  field.type === 'number' ||
                  field.type === 'password'
                ? field.type
                : 'text'
          }
          // A stored secret is never offered back by the browser's own autofill.
          autoComplete={field.type === 'password' ? 'new-password' : undefined}
          step={field.type === 'number' ? '0.01' : undefined}
          min={field.type === 'number' ? 0 : undefined}
          placeholder={field.placeholder}
          autoFocus={field.autoFocus}
          data-autofocus={field.autoFocus || undefined}
          required={field.required}
          value={text}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </DialogField>
  )
}

/** A `datetime-local` value, read in the browser's own zone, as the instant it names. */
export function instantOf(local: string): string {
  return new Date(local).toISOString()
}

/** A date and a time (`YYYY-MM-DD`, `HH:MM`), read in the browser's own zone, as an instant. */
export function instantOfParts(date: string, time: string): string {
  return new Date(`${date}T${time || '00:00'}`).toISOString()
}

/** Now, in the shape a `datetime-local` input wants. */
export function nowLocal(): string {
  const now = new Date()
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000)
  return local.toISOString().slice(0, 16)
}
