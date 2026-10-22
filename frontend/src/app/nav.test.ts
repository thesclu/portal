import { describe, expect, it } from 'vitest'

import { destinationsFor, drawnCollapsed, inNewRequestFlow, landing } from './nav'

const staff = { records_role: 'staff', is_staff: false }
const admin = { records_role: 'admin', is_staff: true }
const requester = { records_role: 'requester', is_staff: false }

describe('destinationsFor', () => {
  it('gives a requester only their own screens, and Settings for their profile', () => {
    expect(destinationsFor(requester).map((d) => d.key)).toEqual([
      'my-requests',
      'suggest',
      'settings',
    ])
  })
  it('keeps the staff screens from a requester and the requester screens from staff', () => {
    const keys = destinationsFor(staff).map((d) => d.key)
    expect(keys).toContain('docket')
    expect(keys).toContain('reports')
    expect(keys).not.toContain('my-requests')
    expect(keys).not.toContain('users')
  })
  it('shows Users to an admin', () => {
    expect(destinationsFor(admin).map((d) => d.key)).toContain('users')
  })
  it('gives an intern and a limited-scope attorney the team screens without Users (239–241)', () => {
    for (const role of ['intern', 'limited_attorney', 'staff_attorney']) {
      const keys = destinationsFor({ records_role: role, is_staff: false }).map((d) => d.key)
      expect(keys).toContain('docket')
      expect(keys).toContain('tasks')
      expect(keys).not.toContain('users')
      expect(keys).not.toContain('my-requests')
    }
  })
})

describe('landing', () => {
  it('sends a requester to their requests and everyone else home', () => {
    expect(landing(requester)).toBe('/my-requests')
    expect(landing(staff)).toBe('/')
    expect(landing()).toBe('/')
  })
})

describe('inNewRequestFlow', () => {
  it('holds stage 1 and every draft page, and nothing else', () => {
    expect(inNewRequestFlow('/new')).toBe(true)
    expect(inNewRequestFlow('/drafts/abc')).toBe(true)
    expect(inNewRequestFlow('/drafts')).toBe(true)
    expect(inNewRequestFlow('/newsletter')).toBe(false)
    expect(inNewRequestFlow('/requests/abc')).toBe(false)
    expect(inNewRequestFlow('/')).toBe(false)
  })
})

describe('drawnCollapsed', () => {
  it('is the flow and the review of a letter', () => {
    expect(drawnCollapsed('/drafts/abc')).toBe(true)
    expect(drawnCollapsed('/approvals/abc')).toBe(true)
    expect(drawnCollapsed('/tasks')).toBe(false)
  })
})
