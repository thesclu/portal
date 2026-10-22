import { Navigate, Outlet, createBrowserRouter, useLocation, useParams } from 'react-router'

import { Loading } from '../components/States'
import { inSetup } from '../lib/setup'
import { rememberEmail } from '../lib/signIn'
import { Activity } from '../screens/Activity'
import { Agencies, AgencyPage } from '../screens/Agencies'
import { ImportAgencies } from '../screens/agencies/Import'
import { Approval } from '../screens/Approval'
import { Calendar } from '../screens/Calendar'
import { Docket } from '../screens/Docket'
import { NotFound, SomethingWentWrong } from '../screens/ErrorPages'
import { Home } from '../screens/Home'
import { Inbox } from '../screens/Inbox'
import { InboxMessagePage } from '../screens/inbox/FullPage'
import { DraftPage } from '../screens/newrequest/DraftPage'
import { Records } from '../screens/Records'
import { Reports } from '../screens/Reports'
import { NewRequest } from '../screens/newrequest/NewRequest'
import { RequestPage } from '../screens/request/RequestPage'
import { MyRequests } from '../screens/requester/MyRequests'
import { Search } from '../screens/Search'
import { Suggest } from '../screens/requester/Suggest'
import { Settings } from '../screens/Settings'
import { Setup } from '../screens/setup/Setup'
import { Templates } from '../screens/templates/Templates'
import { SignIn } from '../screens/SignIn'
import { SignedOut } from '../screens/auth/SignedOut'
import { Tasks } from '../screens/Tasks'
import { Users } from '../screens/Users'
import { Shell } from './layout'
import { SessionProvider, isAdmin, isRequester, useMe, useSessionQuery } from './session'

/** Everything inside needs a signed-in person. Nobody signed in goes to the sign-in screen and
 *  comes back to where they were headed. A person who has not finished the new-user setup (or
 *  has an agreement's new version to agree to) is sent to it from every page (P20i). */
function RequireSession() {
  const session = useSessionQuery()
  const location = useLocation()
  if (session.isPending) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loading what="Opening the portal" />
      </div>
    )
  }
  if (!session.data) {
    const next = location.pathname + location.search
    return <Navigate to={`/sign-in?next=${encodeURIComponent(next)}`} replace />
  }
  rememberEmail(session.data.email)
  if (session.data.setup_required && !inSetup(location.pathname)) {
    return <Navigate to="/setup" replace />
  }
  return (
    <SessionProvider me={session.data}>
      <Outlet />
    </SessionProvider>
  )
}

function AdminOnly() {
  return isAdmin(useMe()) ? <Outlet /> : <NotFound />
}

/** The staff screens. A requester has only their own requests and the suggestion form (P13). */
function StaffOnly() {
  return isRequester(useMe()) ? <NotFound /> : <Outlet />
}

/** Home for staff; a requester lands on their own requests. (The organisation first-run screen
 *  was removed with the v4 export: the administrator configures the portal in Settings.) */
function Landing() {
  if (isRequester(useMe())) return <Navigate to="/my-requests" replace />
  return <Home />
}

/** The templates' old address under Settings, kept as a redirect to /templates. */
function TemplatesMoved() {
  const rest = useParams()['*'] ?? ''
  return <Navigate to={`/templates${rest ? `/${rest}` : ''}`} replace />
}

export const router = createBrowserRouter([
  // The signed-out screens. P25a: the sign-in itself, forgot password, the invitation and the
  // two-step pages are Keycloak's (themed to the design); these two only hand over to it.
  { path: '/sign-in', element: <SignIn />, errorElement: <SomethingWentWrong /> },
  { path: '/signed-out', element: <SignedOut />, errorElement: <SomethingWentWrong /> },
  {
    element: <RequireSession />,
    errorElement: <SomethingWentWrong />,
    children: [
      // The new-user setup (P20i, 006–012): full screen, outside the shell.
      { path: 'setup/:step?', element: <Setup />, errorElement: <SomethingWentWrong /> },
      {
        element: <Shell />,
        errorElement: <SomethingWentWrong />,
        children: [
          {
            // A screen that throws is caught here, inside the shell, so the page keeps its
            // sidebar and top bar as 227 draws it; the shell's own boundary is the last resort.
            errorElement: <SomethingWentWrong />,
            children: [
              { index: true, element: <Landing /> },
              { path: 'requests/:requestId', element: <RequestPage /> },
              { path: 'my-requests', element: <MyRequests /> },
              { path: 'suggest', element: <Suggest /> },
              {
                element: <StaffOnly />,
                children: [
                  { path: 'docket', element: <Docket /> },
                  { path: 'inbox', element: <Inbox /> },
                  // P23b: "Open full page" (107).
                  { path: 'inbox/message/:sourceId', element: <InboxMessagePage /> },
                  { path: 'agencies', element: <Agencies /> },
                  // Before the agency's own page, so "import" is never read as an id.
                  { path: 'agencies/import', element: <ImportAgencies /> },
                  { path: 'agencies/:agencyId', element: <AgencyPage /> },
                  { path: 'tasks', element: <Tasks /> },
                  { path: 'approvals/:reviewId', element: <Approval /> },
                  { path: 'records', element: <Records /> },
                  { path: 'calendar', element: <Calendar /> },
                  { path: 'reports', element: <Reports /> },
                  { path: 'search', element: <Search /> },
                  { path: 'new', element: <NewRequest /> },
                  { path: 'drafts/:draftId', element: <DraftPage /> },
                  // Templates is a top-level destination since design v3; its old address under
                  // Settings still works.
                  { path: 'templates/*', element: <Templates /> },
                  { path: 'settings/templates/*', element: <TemplatesMoved /> },
                  {
                    element: <AdminOnly />,
                    children: [
                      { path: 'users', element: <Users /> },
                      { path: 'activity', element: <Activity /> },
                    ],
                  },
                ],
              },
              { path: 'settings/*', element: <Settings /> },
              { path: '*', element: <NotFound /> },
            ],
          },
        ],
      },
    ],
  },
])
