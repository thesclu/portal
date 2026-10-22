/**
 * The Agencies dialogs (165, 166, 169–172), each an `ActionSpec` drawn by `ActionDialog` at the
 * width and distance from the top its screen draws. Fields the design draws and the API cannot
 * take are left out ("Contact email on the form", "Test it before the next filing", 166); fields
 * the API needs and the design does not draw are kept (an agency's jurisdiction and records law
 * and notes on Edit agency, the main contact, a note on every edit).
 */
import { api, type Schemas } from '../../api/client'
import { $api } from '../../api/query'
import { unwrap, type ActionSpec, type FieldSpec, type Values } from '../../components/ActionDialog'
import { DialogField, PillChoice, RadioCard, RaisedSelect } from '../../components/Fields'
import {
  FILING_CHOICES,
  filingChoice,
  fromFilingChoice,
  jurisdictionLabel,
  knownJurisdictions,
  letterLines,
  parseLetter,
} from '../../lib/agencies'

type Agency = Schemas['AgencyOut']
type AgencyRow = Schemas['AgencyRowOut']
type Contact = Schemas['ContactOut']

const text = (values: Values, name: string) => String(values[name] ?? '').trim()
const OTHER = '__other'

/** "Files by" on the grey pill track, every kind of portal the directory knows among them. */
function filingField(initial: string): FieldSpec {
  return {
    name: 'filing',
    label: 'Files by',
    type: 'custom',
    initial,
    render: (value, onChange) => (
      <DialogField label="Files by">
        <PillChoice
          label="Files by"
          options={FILING_CHOICES}
          value={String(value ?? '')}
          onChange={onChange}
        />
      </DialogField>
    ),
  }
}

/** What the chosen way of filing needs: an address for email, a form for a portal. */
function channelFields(agency?: Agency): FieldSpec[] {
  return [
    {
      name: 'default_request_email',
      label: 'Records email',
      initial: agency?.default_request_email ?? '',
      when: (values) => text(values, 'filing') === 'email',
    },
    {
      name: 'portal_url',
      label: 'Form address',
      initial: agency?.portal_url ?? '',
      when: (values) => text(values, 'filing').startsWith('portal'),
    },
  ]
}

/** Jurisdiction and records law as one choice (165 "California · CPRA") among those already in
 *  the directory, or two written fields for a new one. */
function jurisdictionFields(agencies: Agency[], agency?: Agency): FieldSpec[] {
  const known = knownJurisdictions(agencies)
  const own = agency ? jurisdictionLabel(agency.jurisdiction, agency.records_law) : ''
  const initial = agency ? own : (known[0]?.label ?? OTHER)
  return [
    {
      name: 'jurisdiction_pick',
      label: 'Jurisdiction',
      type: 'select',
      row: 'place',
      initial,
      options: [
        ...known.map((k) => ({ value: k.label, label: k.label })),
        { value: OTHER, label: 'Another jurisdiction…' },
      ],
    },
    {
      name: 'jurisdiction',
      label: 'Jurisdiction',
      required: true,
      row: 'new-place',
      initial: agency?.jurisdiction ?? '',
      when: (values) => values.jurisdiction_pick === OTHER,
    },
    {
      name: 'records_law',
      label: 'Records law',
      row: 'new-place',
      initial: agency?.records_law ?? '',
      when: (values) => values.jurisdiction_pick === OTHER,
    },
  ]
}

function placeOf(values: Values, agencies: Agency[]) {
  if (values.jurisdiction_pick === OTHER) {
    return { jurisdiction: text(values, 'jurisdiction'), records_law: text(values, 'records_law') }
  }
  const known = knownJurisdictions(agencies).find((k) => k.label === values.jurisdiction_pick)
  return { jurisdiction: known?.jurisdiction ?? '', records_law: known?.law ?? '' }
}

/** 165: Add an agency. */
export function addAgency(agencies: Agency[], onMade: (agency: Agency) => void): ActionSpec {
  const place = jurisdictionFields(agencies)
  return {
    key: 'add-agency',
    title: 'Add an agency',
    width: 480,
    top: 60,
    submit: 'Add agency',
    fields: [
      { name: 'name', label: 'Name', required: true, autoFocus: true },
      place[0],
      { name: 'short_name', label: 'Short name', row: 'place' },
      place[1],
      place[2],
      filingField('email'),
      ...channelFields(),
      { name: 'letter', label: 'Letter goes to', type: 'textarea', minHeight: 70 },
    ],
    run: async (values) => {
      const { jurisdiction, records_law } = placeOf(values, agencies)
      if (!jurisdiction) throw new Error('Choose or write the jurisdiction.')
      const filing = fromFilingChoice(text(values, 'filing'))
      const made = unwrap(
        await api.POST('/api/records/agencies', {
          body: {
            name: text(values, 'name'),
            jurisdiction,
            records_law: records_law || null,
            short_name: text(values, 'short_name') || null,
            ...filing,
            default_request_email:
              filing.filing_method === 'email'
                ? text(values, 'default_request_email') || null
                : null,
            portal_url:
              filing.filing_method === 'portal' ? text(values, 'portal_url') || null : null,
            ...parseLetter(text(values, 'letter')),
          },
        }),
      )
      onMade(made)
    },
  }
}

/** Only what changed is sent: an unchanged field is not an edit and leaves no history. */
function changes(agency: Agency, body: Record<string, string | null>) {
  return Object.fromEntries(
    Object.entries(body).filter(([key, value]) => (agency[key as keyof Agency] ?? null) !== value),
  )
}

async function patchAgency(agency: Agency, body: Record<string, string | null>, note: string) {
  const changed = changes(agency, body)
  if (Object.keys(changed).length === 0) throw new Error('Nothing was changed.')
  unwrap(
    await api.PATCH('/api/records/agencies/{agency_id}', {
      params: { path: { agency_id: agency.id } },
      body: { ...changed, note },
    }),
  )
}

/** 166: how the agency is filed with, from the Filing card's Edit. */
export function editFiling(agency: Agency): ActionSpec {
  return {
    key: `filing-${agency.id}`,
    title: 'Filing',
    subtitle: agency.name,
    width: 480,
    top: 80,
    submit: 'Save',
    fields: [
      filingField(filingChoice(agency)),
      ...channelFields(agency),
      {
        name: 'letter',
        label: 'Letter goes to',
        type: 'textarea',
        minHeight: 70,
        initial: letterLines(agency).join('\n'),
        when: (values) => text(values, 'filing') === 'mail',
      },
      { name: 'note', label: 'Note', type: 'textarea', required: true, minHeight: 48 },
    ],
    run: async (values) => {
      const filing = fromFilingChoice(text(values, 'filing'))
      const body: Record<string, string | null> = { ...filing }
      if (filing.filing_method === 'email') {
        body.default_request_email = text(values, 'default_request_email') || null
      }
      if (filing.filing_method === 'portal') body.portal_url = text(values, 'portal_url') || null
      if (filing.filing_method === 'mail') Object.assign(body, parseLetter(text(values, 'letter')))
      await patchAgency(agency, body, text(values, 'note'))
    },
  }
}

/** 170: the agency's names and where its letter goes (and, kept from before, its jurisdiction,
 *  records law and notes, which move its clocks and its page). */
export function editAgency(agency: Agency, agencies: Agency[]): ActionSpec {
  const place = jurisdictionFields(agencies, agency)
  return {
    key: `edit-${agency.id}`,
    title: 'Edit agency',
    subtitle: agency.name,
    width: 500,
    top: 70,
    compact: true,
    submit: 'Save',
    fields: [
      { name: 'name', label: 'Name', required: true, row: 'names', initial: agency.name },
      {
        name: 'short_name',
        label: 'Short name',
        row: 'names',
        initial: agency.short_name ?? '',
      },
      { ...place[0], row: undefined },
      place[1],
      place[2],
      {
        name: 'letter',
        label: 'Letter goes to',
        type: 'textarea',
        minHeight: 70,
        initial: letterLines(agency).join('\n'),
      },
      {
        name: 'notes',
        label: 'Notes',
        type: 'textarea',
        minHeight: 46,
        initial: agency.notes ?? '',
      },
      { name: 'note', label: 'Note', type: 'textarea', required: true, minHeight: 46 },
    ],
    run: async (values) => {
      const { jurisdiction, records_law } = placeOf(values, agencies)
      if (!jurisdiction) throw new Error('Choose or write the jurisdiction.')
      await patchAgency(
        agency,
        {
          name: text(values, 'name'),
          short_name: text(values, 'short_name') || null,
          jurisdiction,
          records_law: records_law || null,
          notes: text(values, 'notes') || null,
          ...parseLetter(text(values, 'letter')),
        },
        text(values, 'note'),
      )
    },
  }
}

function contactFields(contact?: Contact): FieldSpec[] {
  return [
    {
      name: 'name',
      label: 'Name',
      required: true,
      autoFocus: true,
      row: 'who',
      initial: contact?.name ?? '',
    },
    { name: 'title', label: 'Role', row: 'who', initial: contact?.title ?? '' },
    { name: 'email', label: 'Email', initial: contact?.email ?? '' },
    { name: 'phone', label: 'Phone', initial: contact?.phone ?? '' },
    {
      name: 'copy',
      label: 'Copy on every request',
      type: 'checkbox',
      initial: contact?.copy_on_every_request ?? false,
    },
    {
      name: 'primary',
      label: 'The main contact',
      type: 'checkbox',
      initial: contact?.is_primary ?? false,
    },
  ]
}

/** 169: Add a contact. */
export function addContact(agency: Agency): ActionSpec {
  return {
    key: `contact-${agency.id}`,
    title: 'Add a contact',
    subtitle: agency.name,
    width: 460,
    top: 90,
    compact: true,
    submit: 'Add contact',
    fields: contactFields(),
    run: async (values) => {
      unwrap(
        await api.POST('/api/records/agencies/{agency_id}/contacts', {
          params: { path: { agency_id: agency.id } },
          body: {
            name: text(values, 'name'),
            title: text(values, 'title') || null,
            email: text(values, 'email') || null,
            phone: text(values, 'phone') || null,
            is_primary: values.primary === true,
            copy_on_every_request: values.copy === true,
          },
        }),
      )
    },
  }
}

/** 171: Edit contact, with the note every edit carries. */
export function editContact(contact: Contact, agency: Agency): ActionSpec {
  return {
    key: `edit-contact-${contact.id}`,
    title: 'Edit contact',
    subtitle: `${contact.name} · ${agency.short_name || agency.name}`,
    width: 460,
    top: 80,
    compact: true,
    submit: 'Save',
    fields: [
      ...contactFields(contact),
      { name: 'note', label: 'Note', type: 'textarea', required: true, minHeight: 46 },
    ],
    run: async (values) => {
      const wanted = {
        name: text(values, 'name'),
        title: text(values, 'title') || null,
        email: text(values, 'email') || null,
        phone: text(values, 'phone') || null,
        is_primary: values.primary === true,
        copy_on_every_request: values.copy === true,
      }
      const changed = Object.fromEntries(
        Object.entries(wanted).filter(
          ([key, value]) => (contact[key as keyof Contact] ?? null) !== value,
        ),
      )
      if (Object.keys(changed).length === 0) throw new Error('Nothing was changed.')
      unwrap(
        await api.PATCH('/api/records/agencies/contacts/{contact_id}', {
          params: { path: { contact_id: contact.id } },
          body: { ...changed, note: text(values, 'note') },
        }),
      )
    },
  }
}

/** "7 requests · 3 contacts" under an agency in the merge dialog (172). */
function AgencyFacts({ agency }: { agency: AgencyRow }) {
  const contacts = $api.useQuery('get', '/api/records/agencies/{agency_id}/contacts', {
    params: { path: { agency_id: agency.id } },
  })
  const requests = agency.requests_total
  const count = contacts.data?.length
  return (
    <>
      {requests} {requests === 1 ? 'request' : 'requests'}
      {count === undefined ? null : ` · ${count} ${count === 1 ? 'contact' : 'contacts'}`}
      {/* P23b (172): an agency the importer made says so. */}
      {agency.imported_from ? ' · added by import' : null}
    </>
  )
}

/**
 * 172: Merge two agencies. The two are chosen first (nothing on 163 picks them), then the cards
 * say which is kept: the chosen card reads "Keep …", the other "Merge in …". Nothing moves: the
 * merged agency's requests and contacts stay its own and its page says where it went (P16).
 */
export function mergeAgencies(agencies: AgencyRow[]): ActionSpec {
  const live = agencies
    .filter((a) => !a.merged_into_agency_id)
    .toSorted((a, b) => a.name.localeCompare(b.name))
  const byId = (id: unknown) => live.find((a) => a.id === id)
  const options = [
    { value: '', label: 'Choose…' },
    ...live.map((a) => ({ value: a.id, label: a.name })),
  ]
  return {
    key: 'merge',
    title: 'Merge two agencies',
    width: 500,
    top: 90,
    submit: 'Merge',
    fields: [
      {
        name: 'first',
        label: 'Agency',
        type: 'custom',
        row: 'pair',
        render: (value, onChange) => (
          <DialogField label="Agency">
            <RaisedSelect value={String(value ?? '')} onChange={(e) => onChange(e.target.value)}>
              {options.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </RaisedSelect>
          </DialogField>
        ),
      },
      {
        name: 'second',
        label: 'The same agency as',
        type: 'custom',
        row: 'pair',
        render: (value, onChange) => (
          <DialogField label="The same agency as">
            <RaisedSelect value={String(value ?? '')} onChange={(e) => onChange(e.target.value)}>
              {options.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </RaisedSelect>
          </DialogField>
        ),
      },
      {
        name: 'keep',
        label: 'Which one is kept',
        type: 'custom',
        initial: 'first',
        when: (values) => Boolean(byId(values.first) && byId(values.second)),
        render: (value, onChange, values) => {
          const first = byId(values.first)
          const second = byId(values.second)
          if (!first || !second) return null
          return (
            <div role="radiogroup" aria-label="Which one is kept" className="flex flex-col gap-3.5">
              {(
                [
                  ['first', first],
                  ['second', second],
                ] as const
              ).map(([key, agency]) => (
                <RadioCard
                  key={key}
                  name="keep"
                  checked={value === key}
                  onChange={() => onChange(key)}
                  title={`${value === key ? 'Keep' : 'Merge in'} ${agency.name}`}
                  hint={<AgencyFacts agency={agency} />}
                  tight
                />
              ))}
            </div>
          )
        },
      },
      { name: 'note', label: 'Reason', type: 'textarea', required: true, minHeight: 46 },
    ],
    run: async (values) => {
      const first = text(values, 'first')
      const second = text(values, 'second')
      if (!first || !second) throw new Error('Choose the two agencies.')
      if (first === second) throw new Error('Choose two different agencies.')
      const keepFirst = values.keep !== 'second'
      unwrap(
        await api.POST('/api/records/agencies/merge', {
          body: {
            keep_id: keepFirst ? first : second,
            merge_id: keepFirst ? second : first,
            note: text(values, 'note'),
          },
        }),
      )
    },
  }
}

/** Undo a merge (P16): the agency is its own again. */
export function unmergeAgency(agency: Agency): ActionSpec {
  return {
    key: `unmerge-${agency.id}`,
    title: 'Unmerge agency',
    subtitle: agency.name,
    width: 460,
    submit: 'Unmerge',
    fields: [{ name: 'note', label: 'Reason', type: 'textarea', required: true, minHeight: 46 }],
    run: async (values) => {
      unwrap(
        await api.POST('/api/records/agencies/{agency_id}/unmerge', {
          params: { path: { agency_id: agency.id } },
          body: { note: text(values, 'note') },
        }),
      )
    },
  }
}
