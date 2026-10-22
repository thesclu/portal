import { describe, expect, it } from 'vitest'

import { safePath, signInAddress, signedOutReason } from './signIn'

describe('signInAddress', () => {
  it('goes to the portal server with the way back', () => {
    expect(signInAddress({ next: '/docket?x=1' })).toBe(
      '/api/core/auth/oidc/login?next=%2Fdocket%3Fx%3D1',
    )
  })

  it('never carries another site as the way back', () => {
    expect(signInAddress({ next: 'https://evil.example' })).toBe(
      '/api/core/auth/oidc/login?next=%2F',
    )
    expect(signInAddress({ next: '//evil.example' })).toBe('/api/core/auth/oidc/login?next=%2F')
    expect(safePath('/\\evil')).toBe('/')
  })

  it('carries the expired hint and an action', () => {
    const address = signInAddress({ next: '/', expired: true, loginHint: 'a@example.org' })
    expect(address).toContain('expired=1')
    expect(address).toContain('login_hint=a%40example.org')
    expect(signInAddress({ next: '/settings', action: 'UPDATE_PASSWORD' })).toContain(
      'kc_action=UPDATE_PASSWORD',
    )
  })
})

describe('signedOutReason', () => {
  it('has words for each reason and falls back for an unknown one', () => {
    expect(signedOutReason('not_invited')).toMatch(/invite/)
    expect(signedOutReason('whatever')).toBe(signedOutReason('refused'))
    expect(signedOutReason(null)).toBeNull()
  })
})
