import { describe, expect, it } from 'vitest'

import {
  articleFor,
  firstName,
  inSetup,
  isSetupRefusal,
  nextStep,
  previousStep,
  readToEnd,
  runSteps,
  scrollThumb,
} from './setup'

describe('the run of steps', () => {
  const steps = runSteps(['welcome', 'name', 'signature', 'confidentiality', 'intern', 'later'])

  it('keeps the steps the screens know, in order', () => {
    expect(steps).toEqual(['welcome', 'name', 'signature', 'confidentiality', 'intern'])
  })

  it('goes to done after the last step and has no step before the first', () => {
    expect(nextStep(steps, 'welcome')).toBe('name')
    expect(nextStep(steps, 'intern')).toBe('done')
    expect(nextStep(['confidentiality'], 'confidentiality')).toBe('done')
    expect(previousStep(steps, 'welcome')).toBeNull()
    expect(previousStep(steps, 'confidentiality')).toBe('signature')
  })
})

describe('the welcome', () => {
  it('greets by the first name', () => {
    expect(firstName('  Maya   Torres ')).toBe('Maya')
    expect(firstName('')).toBe('')
  })

  it('says a or an as the short name is spoken', () => {
    expect(articleFor('SCLU')).toBe('an')
    expect(articleFor('ACLU')).toBe('an')
    expect(articleFor('UC')).toBe('a')
    expect(articleFor('Org')).toBe('an')
    expect(articleFor('Union')).toBe('a')
    expect(articleFor('Office')).toBe('an')
    expect(articleFor('Clinic')).toBe('a')
    expect(articleFor('')).toBe('a')
  })
})

describe('the agreement box', () => {
  it('is read once scrolled to its end, or when it does not scroll', () => {
    expect(readToEnd(0, 320, 800)).toBe(false)
    expect(readToEnd(479, 320, 800)).toBe(true)
    expect(readToEnd(0, 320, 300)).toBe(true)
  })

  it('draws a thumb as long as the share in view, moving down the track', () => {
    expect(scrollThumb(0, 320, 320)).toBeNull()
    const top = scrollThumb(0, 320, 846)
    expect(top).toEqual({ top: 12, height: 112 })
    const end = scrollThumb(526, 320, 846)
    expect(end).toEqual({ top: 12 + 296 - 112, height: 112 })
    expect(scrollThumb(0, 320, 100_000)?.height).toBe(24)
  })
})

describe('the server holding someone at the setup (P23a)', () => {
  it('is a 403 with the setup code, nothing else', () => {
    expect(
      isSetupRefusal(403, {
        detail: 'Finish setting up your account first.',
        code: 'setup_required',
      }),
    ).toBe(true)
    expect(isSetupRefusal(403, { detail: 'x', code: 'privileged', tracking_number: 'REQ-1' })).toBe(
      false,
    )
    expect(isSetupRefusal(403, { detail: 'Portal administrators only.' })).toBe(false)
    expect(isSetupRefusal(401, { code: 'setup_required' })).toBe(false)
    expect(isSetupRefusal(403, null)).toBe(false)
    expect(isSetupRefusal(403, 'setup_required')).toBe(false)
  })

  it('knows the setup pages, so it never sends someone there from there', () => {
    expect(inSetup('/setup')).toBe(true)
    expect(inSetup('/setup/signature')).toBe(true)
    expect(inSetup('/setupx')).toBe(false)
    expect(inSetup('/settings')).toBe(false)
  })
})
