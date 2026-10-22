import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router'

import { PageHeader } from '../app/layout'
import { isAdmin, isRequester, useMe } from '../app/session'
import { Loading } from '../components/States'
import { cx } from '../components/ui'
import { useFork } from './newrequest/ForkLetter'
import { Addresses } from './settings/Addresses'
import { Filing } from './settings/Filing'
import { Notifications } from './settings/Notifications'
import { Organisation } from './settings/Organisation'
import { Profile } from './settings/Profile'
import { Rules } from './settings/Rules'
import { Signature } from './settings/Signature'
import { System } from './settings/System'
import { TemplatesFrame } from './templates/ForkTemplates'

interface Tab {
  path: string
  label: string
  /** Somewhere else in the portal (Letter templates live under /templates since design v3). */
  to?: string
  adminOnly?: boolean
  /** A requester's Settings is their profile alone. */
  staffOnly?: boolean
}

/**
 * The tabs in the order 197–204 draw them. Activity moved to its own page (190, from Users), and
 * Saved replies to a tab of Templates (P20h, 42h); their old addresses still arrive.
 */
const TABS: Tab[] = [
  { path: 'profile', label: 'Profile' },
  { path: 'notifications', label: 'Notifications', staffOnly: true },
  { path: 'organisation', label: 'Organisation', staffOnly: true },
  { path: 'filing', label: 'Filing & deadlines', staffOnly: true },
  { path: 'rules', label: 'Rules & calendars', staffOnly: true },
  { path: 'templates', label: 'Letter templates', to: '/templates', staffOnly: true },
  { path: 'addresses', label: 'Request addresses', adminOnly: true },
  { path: 'system', label: 'System', adminOnly: true },
]

/**
 * Settings (197–204): the 200 px list of tabs at the left, the tab's cards at the right (at most
 * 760 px), 22 px apart. Your signature (217) is a page of its own under Profile, drawn without
 * the list.
 */
export function Settings() {
  const me = useMe()
  const { pathname } = useLocation()
  const staff = !isRequester(me)
  const admin = isAdmin(me)
  // P21c: while the drafting tool is in use, Letter templates is its list framed here (203).
  const fork = useFork()
  const framed = Boolean(staff && fork.data?.in_use)
  const tabs = TABS.filter((tab) => (!tab.adminOnly || admin) && (!tab.staffOnly || staff)).map(
    // (Its own address: `/settings/templates` is the old address of Templates and still goes there.)
    (tab) =>
      tab.path === 'templates' && framed
        ? { ...tab, path: 'letter-templates', to: undefined }
        : tab,
  )
  const signature = pathname.startsWith('/settings/signature')
  return (
    <div className="flex flex-col leading-[normal]">
      <PageHeader title="Settings" />
      <div className="flex min-w-0 flex-col gap-3.5 px-2 pb-2">
        {signature ? (
          <Signature />
        ) : (
          <div className="flex items-start gap-[22px]">
            <nav aria-label="Settings" className="flex w-[200px] shrink-0 flex-col gap-px">
              {tabs.map((tab) => (
                <NavLink
                  key={tab.path}
                  to={tab.to ?? `/settings/${tab.path}`}
                  className={({ isActive }) =>
                    cx(
                      'rounded-8 px-3 py-2 text-13',
                      isActive
                        ? 'bg-fill-subtle-2 font-medium text-ink hover:text-ink'
                        : 'text-ink-2 hover:bg-hover hover:text-ink',
                    )
                  }
                >
                  {tab.label}
                </NavLink>
              ))}
            </nav>
            <div className="flex max-w-[760px] min-w-0 flex-1 flex-col gap-3.5">
              <Routes>
                <Route index element={<Navigate to="/settings/profile" replace />} />
                <Route path="profile" element={<Profile />} />
                {staff ? (
                  <>
                    <Route path="notifications" element={<Notifications />} />
                    <Route path="organisation" element={<Organisation />} />
                    <Route path="filing" element={<Filing />} />
                    <Route path="rules" element={<Rules />} />
                    {/* P21c: the drafting tool's list, framed (until the answer about it has
                        come, a wait rather than the catch-all's Profile). */}
                    <Route
                      path="letter-templates"
                      element={
                        fork.isPending ? (
                          <Loading what="Opening the letter templates" />
                        ) : framed && fork.data ? (
                          <TemplatesFrame
                            web={fork.data.web_url ?? ''}
                            origin={fork.data.web_origin ?? ''}
                            path="/embed/templates"
                            navParent
                            height="calc(100vh - 140px)"
                          />
                        ) : (
                          <Navigate to="/templates" replace />
                        )
                      }
                    />
                    <Route path="replies" element={<Navigate to="/templates/replies" replace />} />
                  </>
                ) : null}
                {admin ? (
                  <>
                    <Route path="addresses" element={<Addresses />} />
                    <Route path="system" element={<System />} />
                    {/* Activity moved to its own page (190); old links still arrive. */}
                    <Route path="activity" element={<Navigate to="/activity" replace />} />
                  </>
                ) : null}
                <Route path="*" element={<Navigate to="/settings/profile" replace />} />
              </Routes>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
