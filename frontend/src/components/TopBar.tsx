import { Bell } from './Bell'
import { IconButton } from './IconButton'
import { ChatPanel } from '../screens/chat/ChatPanel'
import { CheckSquareIcon, ChevronDown, SearchIcon, SparklesIcon } from './icons'
import { Menu } from './Menu'
import { TasksPanel } from './TasksPanel'

/**
 * The 56 px bar across the top of the window (design 013): on the page grey, no border, three
 * columns. Left, the brand row: the SCLU wordmark and the "Public Records ▾" app switcher.
 * Centre, the glass search (at most 460 px) that opens the quick-jump palette, with its ⌘K hint.
 * Right, four 38 px icon buttons (DECISIONS 32): chat (team chat, P22, with who's online inside
 * it: 018–022), check-square (the Tasks panel, 016), sparkles (the assistant anywhere, P21g:
 * inert until then) and the bell (notifications). A requester has no tasks
 * panel: the check-square is drawn inert for them.
 */
export function TopBar({ onSearch, staff }: { onSearch: () => void; staff: boolean }) {
  return (
    <div className="grid h-topbar shrink-0 grid-cols-[1fr_minmax(0,460px)_1fr] items-center gap-3 bg-page pr-4 leading-[normal]">
      <span className="flex h-16 items-center gap-2.5 pr-3.5 pl-[25px]">
        <img src="/brand/sclu-letters-blue.png" alt="SCLU" className="h-[21px] w-auto shrink-0" />
        <Menu
          label="Switch app"
          align="left"
          width={200}
          items={[
            { label: 'Public Records', onPick: () => {} },
            // Documents is a later part; listed, not yet openable.
            { label: 'Documents', onPick: () => {}, disabled: true },
          ]}
        >
          <span className="inline-flex items-center gap-[7px] text-14 font-semibold whitespace-nowrap text-ink">
            Public Records
            <ChevronDown size={12} width={2.2} stroke="var(--color-ink-3)" />
          </span>
        </Menu>
      </span>
      <button
        type="button"
        onClick={onSearch}
        className="glass flex h-[38px] min-w-0 items-center gap-2.5 justify-self-stretch rounded-pill px-3.5 text-left text-13 text-ink-3 outline-none focus-visible:border-brand"
      >
        <SearchIcon size={15} width={2} stroke="var(--color-muted)" />
        <span className="flex-1 truncate">Search requests, agencies, records</span>
        <kbd
          aria-hidden
          className="rounded-6 border border-[rgba(11,31,58,0.08)] bg-[rgba(255,255,255,0.75)] px-1.5 py-px font-ui text-105 text-muted"
        >
          ⌘K
        </kbd>
      </button>
      <div className="flex items-center gap-2 justify-self-end">
        <ChatPanel />
        {staff ? (
          <TasksPanel />
        ) : (
          <IconButton label="Tasks" inert>
            <CheckSquareIcon size={16} stroke="var(--color-ink-2)" />
          </IconButton>
        )}
        <IconButton label="Assistant" inert>
          <SparklesIcon size={16} stroke="var(--color-ink-2)" />
        </IconButton>
        <Bell />
      </div>
    </div>
  )
}
