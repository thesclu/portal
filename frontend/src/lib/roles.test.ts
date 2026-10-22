import { describe, expect, it } from 'vitest'

import {
  ROLES,
  ROLE_DESCRIPTION,
  ROLE_LABEL,
  isFullStaff,
  isGrantScoped,
  isTeam,
  needsApproval,
  roleLabel,
  roleShort,
} from './roles'
import { isPreFiling, statusGlyph, statusText } from './status'

describe('the six roles', () => {
  it('lists them in the order 206 and 207 draw, each with its words', () => {
    expect(ROLES).toEqual([
      'admin',
      'staff',
      'staff_attorney',
      'limited_attorney',
      'intern',
      'requester',
    ])
    for (const role of ROLES) {
      expect(ROLE_LABEL[role]).toBeTruthy()
      expect(ROLE_DESCRIPTION[role].endsWith('.')).toBe(true)
    }
    expect(roleLabel('limited_attorney')).toBe('Attorney, limited scope')
    expect(roleShort('limited_attorney')).toBe('Attorney')
    expect(roleLabel('director')).toBe('No role')
  })

  it('groups them as the server does, failing closed for a role it does not know', () => {
    expect(ROLES.filter(isFullStaff)).toEqual(['admin', 'staff', 'staff_attorney'])
    expect(ROLES.filter(isTeam)).toEqual([
      'admin',
      'staff',
      'staff_attorney',
      'limited_attorney',
      'intern',
    ])
    expect(ROLES.filter(isGrantScoped)).toEqual(['limited_attorney', 'intern'])
    expect(ROLES.filter(needsApproval)).toEqual(['intern'])
    for (const unknown of ['director', '', null, undefined]) {
      expect(isTeam(unknown)).toBe(false)
      expect(isFullStaff(unknown)).toBe(false)
      expect(needsApproval(unknown)).toBe(false)
    }
  })
})

describe('waiting for approval', () => {
  it('reads as Filing · waiting for approval, with the Filing glyph, and is not yet filed', () => {
    expect(statusText('waiting_for_approval')).toBe('Filing · waiting for approval')
    expect(statusGlyph('waiting_for_approval')).toEqual(statusGlyph('pending_filing'))
    expect(isPreFiling('waiting_for_approval')).toBe(true)
    expect(isPreFiling('pending_filing')).toBe(true)
    expect(isPreFiling('filed')).toBe(false)
  })
})
