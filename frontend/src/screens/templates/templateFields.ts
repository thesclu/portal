import type { Schemas } from '../../api/client'
import type { FieldSpec, Values } from '../../components/ActionDialog'
import { text } from './shared'

type Template = Schemas['TemplateOut']
type Letterhead = Schemas['ShellOut']

/** The laws the rules file has rules for, as it spells them. */
export function lawsOf(rules: Schemas['RulesOut'] | undefined): string[] {
  return [...new Set((rules?.rules ?? []).map((rule) => rule.law))].sort()
}

/** Law, jurisdiction, letterhead and notes: the same four fields when adding and when editing. */
export function detailFields(
  letterheads: Letterhead[],
  laws: string[],
  template?: Template,
  when?: (values: Values) => boolean,
): FieldSpec[] {
  const preset = template
    ? (template.shell_id ?? '')
    : (letterheads.find((letterhead) => letterhead.is_default)?.id ?? '')
  return [
    {
      name: 'legal_basis',
      label: 'Law',
      initial: template?.legal_basis ?? '',
      hint:
        laws.length > 0
          ? `An item can have its own deadline only under a law the rules file has, spelled the same way: ${laws.join(', ')}.`
          : 'The rules file has no rules yet, so no item can have its own deadline.',
      when,
    },
    { name: 'jurisdiction', label: 'Jurisdiction', initial: template?.jurisdiction ?? '', when },
    {
      name: 'shell_id',
      label: 'Letterhead',
      type: 'select',
      initial: preset,
      options: [
        { value: '', label: 'None' },
        ...letterheads.map((letterhead) => ({ value: letterhead.id, label: letterhead.name })),
      ],
      when,
    },
    { name: 'notes', label: 'Notes', type: 'textarea', initial: template?.notes ?? '', when },
  ]
}

export function detailBody(values: Values) {
  return {
    legal_basis: text(values, 'legal_basis') || null,
    jurisdiction: text(values, 'jurisdiction') || null,
    shell_id: text(values, 'shell_id') || null,
    notes: text(values, 'notes') || null,
  }
}
