/**
 * The design's icons (v3 screens): every path is copied from the inline SVGs the screens draw,
 * on a 24 × 24 box, stroked and never filled. Each takes its size and stroke from the caller, as
 * the screens vary them (16/1.8 in the sidebar and top bar, 14/1.9 in toolbars, 12/2.2 chevrons).
 */
import type { IconName } from '../app/nav'

type Props = {
  size?: number
  stroke?: string
  width?: number
  className?: string
}

const NAV_PATHS: Record<IconName, string[]> = {
  home: ['M4 11l8-7 8 7v8a1 1 0 0 1-1 1h-4v-6h-6v6H5a1 1 0 0 1-1-1z'],
  inbox: ['M4 13l2.5-7h11L20 13v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z', 'M4 13h4.5l1.5 2h4l1.5-2H20'],
  tasks: ['M8.5 12l2.5 2.5 4.5-5'],
  calendar: ['M4 10h16M9 3v4M15 3v4'],
  docket: ['M8 8h8M8 12h8M8 16h5'],
  agencies: ['M3 9l9-5 9 5M5 9v9M12 9v9M19 9v9M3 20h18'],
  records: ['M4 8h16v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z', 'M4 8l2-4h12l2 4M9.5 12.5h5'],
  templates: [
    'M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z',
    'M14 3v6h6M8 13h8M8 17h5',
  ],
  reports: ['M5 20V11M12 20V5M19 20v-6'],
  users: ['M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6', 'M15.5 14.4c2.8.3 5 2.7 5 5.6'],
  settings: [
    'M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8',
  ],
}

function Svg({
  size = 16,
  stroke = 'currentColor',
  width = 1.8,
  className,
  children,
}: Props & { children: React.ReactNode }) {
  return (
    <svg
      aria-hidden
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke={stroke}
      strokeWidth={width}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={{ flexShrink: 0 }}
    >
      {children}
    </svg>
  )
}

/** A sidebar item's icon, as drawn in 006. */
export function NavIcon({ name, ...rest }: Props & { name: IconName }) {
  return (
    <Svg {...rest}>
      {name === 'tasks' ? <rect x="4" y="4" width="16" height="16" rx="3" /> : null}
      {name === 'calendar' ? <rect x="4" y="5" width="16" height="15" rx="2" /> : null}
      {name === 'docket' ? <rect x="4" y="3" width="16" height="18" rx="2" /> : null}
      {name === 'users' ? (
        <>
          <circle cx="9" cy="8" r="3.2" />
          <circle cx="17" cy="9" r="2.4" />
        </>
      ) : null}
      {name === 'settings' ? <circle cx="12" cy="12" r="3" /> : null}
      {NAV_PATHS[name].map((d) => (
        <path key={d} d={d} />
      ))}
    </Svg>
  )
}

export function SearchIcon(props: Props) {
  return (
    <Svg {...props}>
      <circle cx="11" cy="11" r="7" />
      <path d="M20 20l-4-4" />
    </Svg>
  )
}

export function ChevronDown(props: Props) {
  return (
    <Svg {...props}>
      <path d="M6 9l6 6 6-6" />
    </Svg>
  )
}

/** The top bar's four: chat, check-square, sparkles, bell (006). */
export function ChatIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z" />
    </Svg>
  )
}

export function CheckSquareIcon(props: Props) {
  return (
    <Svg {...props}>
      <rect x="4" y="4" width="16" height="16" rx="3.5" />
      <path d="M8.5 12.2l2.4 2.4 4.6-5" />
    </Svg>
  )
}

export function SparklesIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M12 3.5l1.7 4.6 4.6 1.7-4.6 1.7L12 16.1l-1.7-4.6-4.6-1.7 4.6-1.7z" />
      <path d="M18.5 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z" />
    </Svg>
  )
}

export function BellIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M6 8a6 6 0 0 1 12 0c0 7 3 8 3 8H3s3-1 3-8" />
      <path d="M10 20a2 2 0 0 0 4 0" />
    </Svg>
  )
}

/** The sidebar's collapse toggle («); flipped for expand. */
export function CollapseIcon({ flipped, ...rest }: Props & { flipped?: boolean }) {
  return (
    <Svg {...rest} className={flipped ? 'rotate-180' : undefined}>
      <path d="M11 17l-5-5 5-5" />
      <path d="M18 17l-5-5 5-5" />
    </Svg>
  )
}

export function CloseIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M6 6l12 12M18 6L6 18" />
    </Svg>
  )
}

export function CheckIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M5 12l5 5 9-10" />
    </Svg>
  )
}

/** The toolbar's: filter, sort, columns, export (018). */
export function FilterIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M4 6h16M7 12h10M10 18h4" />
    </Svg>
  )
}

export function SortIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M7 4v16M4 17l3 3 3-3M17 20V4M14 7l3-3 3 3" />
    </Svg>
  )
}

export function ColumnsIcon(props: Props) {
  return (
    <Svg {...props}>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9 4v16M15 4v16" />
    </Svg>
  )
}

export function ExportIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M12 4v11M7 10l5 5 5-5M5 20h14" />
    </Svg>
  )
}

/* ── P20b (v4 screens 013–059) ─────────────────────────────────────────────── */

/** "New task", "New request" (016). */
export function PlusIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M12 5v14M5 12h14" />
    </Svg>
  )
}

/** The Docket toolbar's Select (025): an empty rounded square. */
export function SelectIcon(props: Props) {
  return (
    <Svg {...props}>
      <rect x="4.5" y="4.5" width="15" height="15" rx="3.5" />
    </Svg>
  )
}

/** The bulk bar's "Assign to…" (028). */
export function AssignIcon(props: Props) {
  return (
    <Svg {...props}>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6" />
      <path d="M19 8v6M16 11h6" />
    </Svg>
  )
}

/** The bulk bar's "Add note" (028). */
export function NoteIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </Svg>
  )
}

/** "More" (032): three filled dots. */
export function MoreIcon({ stroke = 'currentColor', ...props }: Props) {
  return (
    <Svg {...props} stroke={stroke}>
      <circle cx="5" cy="12" r="1.6" fill={stroke} stroke="none" />
      <circle cx="12" cy="12" r="1.6" fill={stroke} stroke="none" />
      <circle cx="19" cy="12" r="1.6" fill={stroke} stroke="none" />
    </Svg>
  )
}

/** "Open full" (032). */
export function ExpandIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M15 3h6v6M9 21H3v-6M21 3l-8 8M3 21l8-8" />
    </Svg>
  )
}

/** "Back to split" (033). */
export function ShrinkIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7" />
    </Svg>
  )
}

/** An overdue status in the request summary (032). */
export function ClockIcon(props: Props) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </Svg>
  )
}

export function ChevronLeft(props: Props) {
  return (
    <Svg {...props}>
      <path d="M15 6l-6 6 6 6" />
    </Svg>
  )
}

export function ChevronRight(props: Props) {
  return (
    <Svg {...props}>
      <path d="M9 6l6 6-6 6" />
    </Svg>
  )
}

/* ── The request page (062–104), P20c ───────────────────────────────────── */

/** A message's toolbar (062): attach to a request. */
export function FilePlusIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
      <path d="M14 3v6h6" />
      <path d="M12 12v6M9 15h6" />
    </Svg>
  )
}

/** A message's toolbar (062): create a task from it. */
export function TaskBoxIcon(props: Props) {
  return (
    <Svg {...props}>
      <rect x="4" y="4" width="16" height="16" rx="3" />
      <path d="M8.5 12l2.5 2.5 4.5-5" />
    </Svg>
  )
}

/** "Forward" (062). */
export function ForwardIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M15 7l5 5-5 5" />
      <path d="M20 12H10a6 6 0 0 0-6 6" />
    </Svg>
  )
}

/** "Sent by your team" on a collapsed message (062). */
export function SentIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M9 7L4 12l5 5" />
      <path d="M4 12h10a6 6 0 0 1 6 6" />
    </Svg>
  )
}

/** "Has attachments" on a collapsed message (062). */
export function PaperclipIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M21 11.5l-8.6 8.6a5 5 0 0 1-7.1-7.1l8.6-8.6a3.3 3.3 0 0 1 4.7 4.7l-8.6 8.6a1.7 1.7 0 0 1-2.4-2.4l7.9-7.9" />
    </Svg>
  )
}

/** Two dates on file (062). */
export function WarningIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M12 9v4M12 17h.01" />
      <path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
    </Svg>
  )
}

/** A withheld item (062). */
export function MinusCircleIcon(props: Props) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M8 12h8" />
    </Svg>
  )
}

/** A fee (062). */
export function FeeIcon(props: Props) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M14.5 9.5c-.4-.9-1.4-1.5-2.5-1.5-1.5 0-2.5.8-2.5 2s1 1.7 2.5 2 2.5.8 2.5 2-1 2-2.5 2c-1.1 0-2.1-.6-2.5-1.5M12 6.5v1.5M12 16v1.5" />
    </Svg>
  )
}

/** A file's download button (067). */
export function DownloadIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M12 4v11M7 10l5 5 5-5M5 20h14" />
    </Svg>
  )
}

/** A privileged request (093). */
export function LockIcon(props: Props) {
  return (
    <Svg {...props}>
      <rect x="5" y="11" width="14" height="10" rx="2" />
      <path d="M8 11V7a4 4 0 0 1 8 0v4" />
    </Svg>
  )
}

/** The send key of the reply and note composers (062, 066). */
export function ReturnIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M20 5v6a3 3 0 0 1-3 3H5" />
      <path d="M9 10l-4 4 4 4" />
    </Svg>
  )
}

/* ── P20d (v5 screens 106–162, the Inbox) ─────────────────────────────────────── */

/** The Inbox's sub-items in the sidebar (106): New, Flagged, All. */
export function InboxNewIcon(props: Props) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="3.5" />
    </Svg>
  )
}

export function FlagIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M5 21V4h12l-2 4 2 4H5" />
    </Svg>
  )
}

export function AllMailIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M4 7h16M4 12h16M4 17h10" />
    </Svg>
  )
}

/** The flag beside a reason in a message's header (106). */
export function FlagReasonIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M5 21V4" />
      <path d="M5 4h11l-2 4 2 4H5" />
    </Svg>
  )
}

/** "Filters" at the end of the Inbox's search line (106). */
export function SlidersIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M4 7h10M18 7h2M4 17h4M12 17h8" />
      <circle cx="16" cy="7" r="2" />
      <circle cx="10" cy="17" r="2" />
    </Svg>
  )
}

/** "Refresh" beside the sort (106). */
export function RefreshIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M20 11a8 8 0 1 0-2.3 5.7" />
      <path d="M20 4v7h-7" />
    </Svg>
  )
}

/** "Discard draft" (118). */
export function TrashIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />
    </Svg>
  )
}

/** "Saved replies" in the composer's toolbar (118). */
export function SavedRepliesIcon(props: Props) {
  return (
    <Svg {...props}>
      <rect x="4" y="3" width="16" height="18" rx="2" />
      <path d="M8 8h8M8 12h8M8 16h5" />
    </Svg>
  )
}

/** "From request" in the composer's toolbar (118). */
export function FileIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
      <path d="M14 3v6h6" />
    </Svg>
  )
}

/** The attachment viewer's download and full-screen buttons (111). */
export function ViewerDownloadIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <path d="M7 10l5 5 5-5" />
      <path d="M12 15V3" />
    </Svg>
  )
}

export function FullScreenIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M15 3h6v6" />
      <path d="M9 21H3v-6" />
      <path d="M21 3l-8 8" />
      <path d="M3 21l8-8" />
    </Svg>
  )
}

/** "Sending…" in a message's header (127): a three-quarter ring. */
export function SendingIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M12 3a9 9 0 1 0 9 9" />
    </Svg>
  )
}

/** A message's toolbar "More" (106): three dots, filled, with a hairline stroke. */
export function DotsIcon({
  size = 13,
  fill = 'currentColor',
  stroke = 'currentColor',
}: {
  size?: number
  fill?: string
  stroke?: string
}) {
  return (
    <svg aria-hidden width={size} height={size} viewBox="0 0 24 24" fill={fill} stroke={stroke}>
      <circle cx="5" cy="12" r="1.7" />
      <circle cx="12" cy="12" r="1.7" />
      <circle cx="19" cy="12" r="1.7" />
    </svg>
  )
}

/* ── P20g (v5 screens 044–061, the new-request flow) ─────────────────────────── */

/** "Write from scratch" (044) and a cover message's "Edit this one" (049). */
export function PencilIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
    </Svg>
  )
}

/** "Copy message", "Copy as text", the alias's Copy (049–051, v6: icon-only). */
export function CopyIcon(props: Props) {
  return (
    <Svg {...props}>
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M5 15V6a2 2 0 0 1 2-2h9" />
    </Svg>
  )
}

/** A letter's Preview in stage 1 (044). */
export function EyeIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z" />
      <circle cx="12" cy="12" r="3" />
    </Svg>
  )
}

/** The letter's "Full screen" in stage 4 (049). */
export function LetterFullScreenIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M15 3h6v6" />
      <path d="M9 21H3v-6" />
      <path d="M21 3l-7 7" />
      <path d="M3 21l7-7" />
    </Svg>
  )
}

/* ── P22 (v6 screens 018–022, team chat) ───────────────────────────────────── */

/** The composer's send arrow (019). */
export function SendArrowIcon(props: Props) {
  return (
    <Svg {...props}>
      <path d="M5 12h14M13 6l6 6-6 6" />
    </Svg>
  )
}
