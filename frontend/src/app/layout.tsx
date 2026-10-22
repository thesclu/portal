import { useEffect, useState, type ReactNode } from 'react'
import { Link, Outlet, useLocation } from 'react-router'

import { $api } from '../api/query'
import { CommandPalette } from '../components/CommandPalette'
import {
  AllMailIcon,
  ChevronLeft,
  CollapseIcon,
  FlagIcon,
  InboxNewIcon,
  NavIcon,
} from '../components/icons'
import { Menu } from '../components/Menu'
import { TopBar } from '../components/TopBar'
import { Avatar, cx } from '../components/ui'
import { initialsOf, pageLabel } from '../lib/presence'
import { GROUPS, destinationsFor, drawnCollapsed, isBuilt, type Destination } from './nav'
import { setPageLabel, usePresence } from './presence'
import { isRequester, useMe, useSignOut } from './session'

/**
 * A screen's title row (design 006/018): the name in 32 px Libre Franklin 800, an optional
 * 14 px DM Sans muted subtitle 12 px to its right, and the actions at the right of the same row
 * (the "＋ New request" pill). The row is `6px 8px 14px` on every page (v4). It also names the
 * page for the browser tab and the presence list.
 */
export function PageHeader({
  title,
  subtitle,
  trail,
  back,
  note,
  roomy = false,
  actions,
}: {
  title: string
  subtitle?: ReactNode
  /** What the page sits under, e.g. ["Docket"] on a request: drawn as the breadcrumb line. */
  trail?: string[]
  /** The breadcrumb as a way back, "‹ New request" (049): a 13 px chevron and the words. */
  back?: { label: string; to: string }
  /** 12.5 px muted words on the title's baseline, 12 px after it ("REQ-… reserved", 049). */
  note?: ReactNode
  /** 20 px under the title instead of 14 (049, 052). */
  roomy?: boolean
  actions?: ReactNode
}) {
  const label = pageLabel(title, trail)
  useEffect(() => {
    document.title = `${title} · SCLU Portal`
  }, [title])
  useEffect(() => {
    setPageLabel(label)
    return () => setPageLabel('')
  }, [label])
  const heading = (
    <h1 className="min-w-0 truncate font-display text-32 leading-tight font-extrabold tracking-title text-ink">
      {title}
      {subtitle ? (
        <span className="ml-3 align-baseline font-ui text-14 font-normal tracking-normal text-muted">
          {subtitle}
        </span>
      ) : null}
    </h1>
  )
  return (
    <header
      className={cx('flex shrink-0 items-start gap-5 px-2 pt-1.5', roomy ? 'pb-5' : 'pb-3.5')}
    >
      <div className="min-w-0 flex-initial">
        {back ? (
          <Link
            to={back.to}
            className="mb-1 flex items-center gap-1.5 text-125 leading-[normal] text-ink-2 hover:text-ink"
          >
            <ChevronLeft size={13} width={2} stroke="var(--color-ink-2)" />
            {back.label}
          </Link>
        ) : trail?.length ? (
          <p className="mb-1 text-125 text-muted">{trail.join(' › ')}</p>
        ) : null}
        {note ? (
          <div className="flex min-w-0 items-baseline gap-3">
            {heading}
            <span className="text-125 leading-[normal] whitespace-nowrap text-muted">{note}</span>
          </div>
        ) : (
          heading
        )}
      </div>
      {actions ? <div className="ml-auto flex shrink-0 gap-2">{actions}</div> : null}
    </header>
  )
}

/** Which destination the sidebar lights for a path: the one whose address the path is under
 *  (a request page belongs to Docket, a draft to New request's section, search results to
 *  Records), and Home when no destination claims the path, as 212 and 213 draw it. */
export function activeKey(
  pathname: string,
  destinations: readonly Destination[],
): string | undefined {
  const under = (to: string) => pathname === to || pathname.startsWith(`${to}/`)
  const direct = destinations.find((d) => d.to !== '/' && under(d.to))
  if (direct) return direct.key
  if (['/requests', '/new', '/drafts'].some(under)) {
    return destinations.find((d) => d.key === 'docket' || d.key === 'my-requests')?.key
  }
  // An approval opens from Tasks (187, 191); the activity log from Users (190).
  if (under('/approvals')) {
    const tasks = destinations.find((d) => d.key === 'tasks')
    if (tasks) return tasks.key
  }
  if (under('/activity')) {
    const users = destinations.find((d) => d.key === 'users')
    if (users) return users.key
  }
  // Search results sit under Records, as 041 draws them.
  if (under('/search')) {
    const records = destinations.find((d) => d.key === 'records')
    if (records) return records.key
  }
  return destinations.find((d) => d.to === '/')?.key ?? destinations[0]?.key
}

/** The numbers beside Inbox (flagged) and Tasks (open, assigned to me). Nothing else badges.
 *  A requester has neither screen, so nothing is asked for them. */
function useCounts(enabled: boolean): Record<string, number | undefined> {
  const inbox = $api.useQuery(
    'get',
    '/api/records/inbox',
    { params: { query: { bucket: 'flagged', limit: 0 } } },
    { enabled: enabled && isBuilt('inbox') },
  )
  const tasks = $api.useQuery(
    'get',
    '/api/records/tasks',
    { params: { query: { assignee: 'me', state: 'open' } } },
    { enabled: enabled && isBuilt('tasks') },
  )
  return { inbox: inbox.data?.counts.flagged, tasks: tasks.data?.length }
}

const COLLAPSED_KEY = 'sclu.sidebar.collapsed'

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === '1'
  } catch {
    return false
  }
}

/**
 * The shell (design 006, ARCHITECTURE §2.1): the 56 px top bar on the page grey; under it the
 * 256 px white floating sidebar (Today · Requests · Organisation, the person and the collapse
 * toggle in its footer) and the page, which stretches to the window. Collapsed, the sidebar is
 * the 64 px strip the new-request screens draw (044–061): each destination's icon in its order,
 * the badges as small brand pills, the person at the foot. The new-request flow collapses it for
 * its duration (§2.4) and offers no way to open it there; elsewhere the person chooses, and the
 * toggle sits under their avatar.
 */
export function Shell() {
  const me = useMe()
  const signOut = useSignOut()
  const requester = isRequester(me)
  const counts = useCounts(!requester)
  const [searching, setSearching] = useState(false)
  const [collapsed, setCollapsed] = useState(readCollapsed)
  usePresence()

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setSearching(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  function toggle() {
    setCollapsed((was) => {
      try {
        localStorage.setItem(COLLAPSED_KEY, was ? '0' : '1')
      } catch {
        /* a private window: the choice lasts the session */
      }
      return !was
    })
  }

  const destinations = destinationsFor(me)
  const location = useLocation()
  const active = activeKey(location.pathname, destinations)
  const flow = drawnCollapsed(location.pathname)
  const narrow = collapsed || flow
  // On the Inbox its three lists sit under it (106); Sent and Drafts are All's.
  const inboxTab = active === 'inbox' && !narrow ? inboxTabOf(location.search) : null

  return (
    <div className="flex h-full flex-col bg-page text-ink">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-40 focus:rounded-9 focus:bg-card-white focus:px-3 focus:py-2"
      >
        Skip to the page
      </a>
      <TopBar onSearch={() => setSearching(true)} staff={!requester} />
      <div className="flex min-h-0 flex-1">
        {narrow ? (
          <CollapsedRail
            destinations={destinations}
            active={active}
            counts={counts}
            name={me.name}
            onSignOut={() => void signOut()}
            onExpand={flow ? undefined : toggle}
          />
        ) : (
          <nav
            aria-label="Main"
            className="mt-0.5 mb-4 ml-4 box-border flex w-rail shrink-0 flex-col rounded-16 bg-card-white px-2.5 pt-2 pb-3.5 leading-[normal] shadow-float"
          >
            {GROUPS.map((group, groupIndex) => {
              const items = destinations.filter((d) => d.group === group.key)
              if (items.length === 0) return null
              return (
                <div key={group.key} className="flex flex-col">
                  <div
                    className={cx(
                      'mb-1 flex items-end px-[11px] text-11 leading-[normal] font-semibold tracking-group text-muted-2 uppercase',
                      // The design's boxes are content-box: 22 px of label and 2 px under it.
                      groupIndex === 0 ? 'h-6 pb-0.5' : 'h-[30px]',
                    )}
                  >
                    {group.label}
                  </div>
                  {items.map((destination, index) => (
                    <div key={destination.key} className="contents">
                      <NavItem
                        destination={destination}
                        count={counts[destination.key]}
                        active={
                          destination.key === active && !(inboxTab && destination.key === 'inbox')
                        }
                        parent={destination.key === 'inbox' && Boolean(inboxTab)}
                        // v4: 16 px between the last item and the person row, however tall the window.
                        last={groupIndex === GROUPS.length - 1 && index === items.length - 1}
                      />
                      {destination.key === 'inbox' && inboxTab ? (
                        <InboxTabs current={inboxTab} flagged={counts.inbox} />
                      ) : null}
                    </div>
                  ))}
                </div>
              )
            })}
            <div className="mt-auto flex items-center gap-2 px-1 pt-2.5">
              <Menu
                label="Account"
                align="left"
                width={180}
                className="min-w-0 flex-1"
                items={[{ label: 'Sign out', onPick: () => void signOut() }]}
              >
                {/* The design's avatar is an inline image on a line box, so the row is 35 px with the
                  avatar at its top and the name centred. */}
                <span className="ml-1 flex h-[35px] min-w-0 items-center gap-[9px]">
                  <Avatar initials={initialsOf(me.name)} size={30} className="self-start" />
                  <span className="min-w-0 truncate text-125 font-medium text-ink">{me.name}</span>
                </span>
              </Menu>
              <button
                type="button"
                aria-label="Collapse the sidebar"
                aria-pressed={false}
                onClick={toggle}
                className="inline-flex size-7 shrink-0 items-center justify-center rounded-8 hover:bg-hover"
              >
                <CollapseIcon size={18} width={1.9} stroke="var(--color-ink-3)" />
              </button>
            </div>
          </nav>
        )}
        <main
          id="main"
          tabIndex={-1}
          className="flex min-w-0 flex-1 flex-col overflow-y-auto px-4 pb-4 outline-none"
        >
          <Outlet />
        </main>
      </div>
      <CommandPalette open={searching} onClose={() => setSearching(false)} />
    </div>
  )
}

type InboxTab = 'new' | 'flagged' | 'all'

/** Which of the Inbox's lists a query string names: Sent and Drafts are filters of All. */
export function inboxTabOf(search: string): InboxTab {
  const bucket = new URLSearchParams(search).get('bucket')
  if (bucket === 'new') return 'new'
  return bucket === 'all' || bucket === 'sent' || bucket === 'drafts' ? 'all' : 'flagged'
}

const INBOX_TABS: Array<{ key: InboxTab; label: string; icon: typeof FlagIcon }> = [
  { key: 'new', label: 'New', icon: InboxNewIcon },
  { key: 'flagged', label: 'Flagged', icon: FlagIcon },
  { key: 'all', label: 'All', icon: AllMailIcon },
]

/** New · Flagged · All under Inbox (106): 30 px rows indented 27 px, the current one lit. The
 *  Flagged count is the only number (ARCHITECTURE §2.3, DECISIONS 38). */
function InboxTabs({ current, flagged }: { current: InboxTab; flagged: number | undefined }) {
  return INBOX_TABS.map(({ key, label, icon: Icon }) => {
    const on = key === current
    return (
      <Link
        key={key}
        to={`/inbox?bucket=${key}`}
        aria-current={on ? 'page' : undefined}
        className={cx(
          'ml-[27px] flex h-[30px] shrink-0 items-center gap-[9px] rounded-8 px-2.5 text-13 leading-[normal]',
          on ? 'bg-brand-tint font-semibold text-brand' : 'text-ink-2 hover:bg-page',
        )}
      >
        <Icon size={15} stroke={on ? 'var(--color-brand)' : 'var(--color-ink-3)'} />
        <span className="flex-1">{label}</span>
        {key === 'flagged' && flagged ? (
          <span className={cx('text-11', on ? 'text-brand' : 'text-muted')}>{flagged}</span>
        ) : null}
      </Link>
    )
  })
}

/**
 * The collapsed sidebar as 044–061 draw it: a 64 px white card, `12px 10px 14px` inside; each
 * destination a 44 × 38 cell with its 16 px icon (the current one on the brand tint), the badges
 * as 16 px brand pills at the cell's corner; the person's 30 px avatar at the foot. `onExpand`
 * draws the toggle under it (left out in the new-request flow, which keeps it collapsed).
 */
function CollapsedRail({
  destinations,
  active,
  counts,
  name,
  onSignOut,
  onExpand,
}: {
  destinations: Destination[]
  active: string | undefined
  counts: Record<string, number | undefined>
  name: string
  onSignOut: () => void
  onExpand?: () => void
}) {
  return (
    <nav
      aria-label="Main"
      className="mt-0.5 mb-4 ml-4 box-border flex w-rail-collapsed shrink-0 flex-col rounded-16 bg-card-white px-2.5 pt-3 pb-3.5 leading-[normal] shadow-float"
    >
      {destinations.map((destination) => {
        const on = destination.key === active
        const count = counts[destination.key]
        return (
          <Link
            key={destination.key}
            to={destination.to}
            aria-current={on ? 'page' : undefined}
            aria-label={destination.label}
            title={destination.label}
            className={cx(
              'relative mx-auto flex h-[38px] w-11 shrink-0 items-center justify-center rounded-12',
              on ? 'bg-brand-tint' : 'hover:bg-page',
            )}
          >
            <NavIcon
              name={destination.icon}
              size={16}
              stroke={on ? 'var(--color-brand)' : 'var(--color-ink-3)'}
            />
            {count ? (
              <span className="absolute top-1 right-1 box-content inline-flex h-4 min-w-4 items-center justify-center rounded-pill bg-brand px-1 text-10 font-semibold text-white">
                {count}
              </span>
            ) : null}
          </Link>
        )
      })}
      <div className="mt-auto flex flex-col items-center gap-2.5 pt-2.5">
        <Menu
          label="Account"
          align="left"
          width={180}
          items={[{ label: 'Sign out', onPick: onSignOut }]}
        >
          {/* An inline image on a line box in the design: 35 px high, the avatar at its top. */}
          <span className="ml-1 inline-flex h-[35px] items-start">
            <Avatar initials={initialsOf(name)} size={30} />
          </span>
        </Menu>
        {onExpand ? (
          <button
            type="button"
            aria-label="Expand the sidebar"
            aria-pressed
            onClick={onExpand}
            className="inline-flex size-7 shrink-0 items-center justify-center rounded-8 hover:bg-hover"
          >
            <CollapseIcon size={18} width={1.9} stroke="var(--color-ink-3)" flipped />
          </button>
        ) : null}
      </div>
    </nav>
  )
}

function NavItem({
  destination,
  count,
  active,
  parent = false,
  last,
}: {
  destination: Destination
  count: number | undefined
  active: boolean
  /** Its own lists are showing under it: drawn in ink, not lit (106). */
  parent?: boolean
  last: boolean
}) {
  return (
    <Link
      to={destination.to}
      aria-current={active ? 'page' : undefined}
      className={cx(
        'relative flex h-9 shrink-0 items-center gap-[11px] rounded-10 px-[11px] text-135 leading-[normal]',
        last && 'mb-4',
        active
          ? 'bg-brand-tint font-semibold text-brand'
          : parent
            ? 'font-semibold text-ink hover:bg-page'
            : 'text-ink-2 hover:bg-page',
      )}
    >
      <NavIcon
        name={destination.icon}
        size={16}
        stroke={active ? 'var(--color-brand)' : parent ? 'var(--color-ink)' : 'var(--color-ink-3)'}
      />
      <span className="flex-1">{destination.label}</span>
      {count ? <span className="text-12 text-muted">{count}</span> : null}
    </Link>
  )
}
