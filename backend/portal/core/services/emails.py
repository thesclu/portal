"""The emails the portal sends in its own name, as text and as HTML (P20f, P22; design 228, 229).

The daily summary (with its mentions), the mention, and the invite. Pure functions: plain data
in, `(text, html)` out, so a test or a preview renders them without sending anything; the
callers (`notifications`, `invites`, team chat) read the ledger and the settings and hand the
data over. The plain text is always sent as well.

The HTML is built for mail clients, not browsers: one `<!doctype html>` document, laid out with
tables, every style inline (no `<style>` block, no flex or grid, no web fonts: DM Sans when the
reader has it, then Helvetica and Arial), the logo by absolute URL, and every interpolated string
escaped. The colours and sizes are the design's own inline values.

The mention email (design 228's right half) is team chat's (P22). It names who mentioned the
reader and where, never the message's words (DECISIONS 45: the same rule as the browser's
notifications), so the quote the design draws is shown only when a caller passes one, and chat
never does.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from django.utils.html import escape

FONT = "'DM Sans', Helvetica, Arial, sans-serif"
INK = "#0b1f3a"
MUTED = "#878e98"
SUBTLE = "#4f5964"
PAGE = "#e9ecf0"
HEADER_RULE = "#edf0f3"
ROW_RULE = "#f1f3f5"
ACCENT = "#0b4ea2"
SUMMARY_WIDTH = 560
# The most rows one summary section lists, as the plain text always has.
MAX_ROWS = 10
# The task's first line, cut to this many characters, as the plain text always has.
TASK_CHARS = 80
LOGO_PATH = "/brand/sclu-letters-blue.png"
FOOTER = "Change what you get in Settings → Notifications."


@dataclass(frozen=True)
class Brand:
    """What the mail calls the portal, and where its links and logo are (absolute URLs)."""

    short_name: str
    portal: str
    logo_url: str
    home_url: str


def brand() -> Brand:
    """The portal's own name is the organisation's short name (`org.short_name`, else the
    initials of `org.name`) and "Portal", so nothing about the organisation is written here."""
    from portal.core.services.settings import org_short_name
    from portal.mail.services.system_mail import site_url

    short = org_short_name()
    return Brand(
        short_name=short or "Portal",
        portal=f"{short} Portal".strip(),
        logo_url=site_url(LOGO_PATH),
        home_url=site_url("/"),
    )


@dataclass(frozen=True)
class DeadlineLine:
    agency: str
    # The deadline kind's label, as the deadline definitions name it.
    kind: str
    tracking_number: str
    due: date
    # How far past due, in days, counted to the reader's own date; overdue lines only.
    late_by: int = 0


@dataclass(frozen=True)
class TaskLine:
    # The task's first line.
    text: str
    agency: str


@dataclass(frozen=True)
class DailySummary:
    """One person's morning, from the same read Home makes (`overview.home`)."""

    first_name: str
    subject: str
    overdue: Sequence[DeadlineLine]
    # Deadlines falling due on the reader's own date, and the rest of the due-soon window.
    due_today: Sequence[DeadlineLine]
    due_soon: Sequence[DeadlineLine]
    # The reader's open tasks due on their own date, and the rest of their open tasks.
    tasks_today: Sequence[TaskLine]
    other_tasks: Sequence[TaskLine]
    # Flagged in the Inbox.
    flagged: int
    # P20h, administrators only: the open asks for access, as (who asked, which request). An ask
    # is never emailed as it happens (42e); this is where an administrator reads them by mail.
    access_asks: Sequence[tuple[str, str]] = ()
    # P22: the mentions the reader has not opened yet, newest first, as (what the bell says, when
    # on the reader's clock): "Seed Staff mentioned you in #filing", "9:20 AM".
    mentions: Sequence[tuple[str, str]] = ()

    @property
    def is_empty(self) -> bool:
        return not (
            self.overdue
            or self.due_today
            or self.due_soon
            or self.tasks_today
            or self.other_tasks
            or self.flagged
            or self.access_asks
            or self.mentions
        )


def _count(n: int, word: str) -> str:
    """The number and the word, plural unless the number is one."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _first_line(text: str) -> str:
    return (text.splitlines() or [""])[0][:TASK_CHARS]


def _short_date(when: date) -> str:
    return f"{when:%b} {when.day}"


# ---- the frame every portal email shares ----------------------------------------------------


def _page(subject: str, card: str) -> str:
    """The whole document: the grey page, the card centred on it."""
    return (
        "<!doctype html>"
        '<html lang="en"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="x-apple-disable-message-reformatting">'
        f"<title>{escape(subject)}</title>"
        "</head>"
        f'<body style="margin:0;padding:0;background:{PAGE}">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background:{PAGE};border-collapse:collapse">'
        '<tr><td align="center" style="padding:30px 20px">'
        f"{card}"
        "</td></tr></table>"
        "</body></html>"
    )


def _card(
    width: int,
    brand: Brand,
    subject: str,
    body: str,
    *,
    sender: str | None = None,
    logo: bool = True,
) -> str:
    """The white card: the header strip (the portal's name, or who it is from "in" it, and the
    subject), then the body, under the logo unless `logo` is off (the mention draws none)."""
    return (
        f'<table role="presentation" width="{width}" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:{width}px;max-width:100%;background:#ffffff;border-radius:10px;'
        "box-shadow:0 2px 12px rgba(11,31,58,.08);overflow:hidden;"
        'border-collapse:separate;border-spacing:0">'
        "<tr>"
        f'<td style="padding:14px 22px;border-bottom:1px solid {HEADER_RULE};font-family:{FONT};'
        f'font-size:12px;color:{MUTED};line-height:1.7;text-align:left">'
        f'<span style="color:{INK};font-weight:500">{escape(sender or brand.portal)}</span>'
        f"<br>{escape(subject)}"
        "</td>"
        "</tr>"
        "<tr>"
        f'<td style="padding:22px 26px 26px;font-family:{FONT};font-size:13px;line-height:1.6;'
        f'color:{INK};text-align:left">'
        f"{_logo(brand) if logo else ''}{body}"
        "</td>"
        "</tr>"
        "</table>"
    )


def _logo(brand: Brand) -> str:
    return (
        f'<img src="{escape(brand.logo_url)}" alt="{escape(brand.short_name)}" height="19" '
        'style="height:19px;width:auto;border:0">'
    )


def _button(label: str, href: str, margin_top: int) -> str:
    return (
        f'<div style="margin-top:{margin_top}px">'
        f'<a href="{escape(href)}" style="display:inline-block;height:32px;line-height:32px;'
        "padding:0 14px;border-radius:999px;font-size:12.5px;font-weight:500;"
        f"background:{ACCENT};color:#ffffff;text-decoration:none;white-space:nowrap;"
        f'font-family:{FONT}">{escape(label)}</a>'
        "</div>"
    )


def _section(title: str, rows: Iterable[tuple[str, str]]) -> str:
    """A heading and its rows: the line on the left, a short fact on the right."""
    cell = f"padding:8px 0;border-top:1px solid {ROW_RULE};font-family:{FONT};font-size:12.5px;"
    cells = "".join(
        "<tr>"
        f'<td width="100%" style="{cell}padding-right:12px;color:{INK};line-height:1.6">'
        f"{escape(left)}</td>"
        f'<td align="right" style="{cell}color:{MUTED};white-space:nowrap;text-align:right;'
        f'line-height:1.6">{escape(right)}</td>'
        "</tr>"
        for left, right in rows
    )
    return (
        '<div style="margin-top:16px">'
        f'<div style="font-size:10.5px;letter-spacing:.07em;color:{MUTED};'
        f'text-transform:uppercase;font-weight:600;margin-bottom:6px">{escape(title)}</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:100%;border-collapse:collapse">{cells}</table>'
        "</div>"
    )


# ---- the daily summary (design 228, left) ---------------------------------------------------


def summary_sections(summary: DailySummary) -> list[tuple[str, list[tuple[str, str]]]]:
    """The four lists as (heading, rows), each only when it has something, each at most
    `MAX_ROWS` rows. The same lists the plain text carries."""
    overdue = [
        (f"{d.agency} · {d.kind.lower()}", _count(d.late_by, "day")) for d in summary.overdue
    ]
    today = [(f"{d.agency} · {d.kind.lower()}", d.tracking_number) for d in summary.due_today]
    today += [(f"{_first_line(t.text)} · {t.agency}", "Task") for t in summary.tasks_today]
    soon = [(f"{d.agency} · {d.kind.lower()}", _short_date(d.due)) for d in summary.due_soon]
    assigned = [(f"{_first_line(t.text)} · {t.agency}", "Task") for t in summary.other_tasks]
    asks = [(f"{who} asked for access", tracking) for who, tracking in summary.access_asks]
    sections = [
        ("Overdue", overdue),
        ("Due today", today),
        ("Mentions", list(summary.mentions)),
        ("Due soon", soon),
        ("Assigned to you", assigned),
        ("Asks for access", asks),
    ]
    return [(title, rows[:MAX_ROWS]) for title, rows in sections if rows]


def summary_counts(summary: DailySummary) -> str:
    """Always the three, as drawn: overdue, due today (deadlines and tasks), flagged."""
    due_today = len(summary.due_today) + len(summary.tasks_today)
    return f"{len(summary.overdue)} overdue · {due_today} due today · {summary.flagged} flagged"


def daily_summary_card(summary: DailySummary, brand: Brand) -> str:
    body = (
        f'<div style="font-size:17px;font-weight:600;margin-top:12px">'
        f"Good morning, {escape(summary.first_name)}</div>"
        f'<div style="color:{SUBTLE};margin-top:4px">{escape(summary_counts(summary))}</div>'
        + "".join(_section(title, rows) for title, rows in summary_sections(summary))
        + _button(f"Open {brand.portal}", brand.home_url, 20)
        + f'<div style="margin-top:18px;font-size:11.5px;color:{MUTED}">{escape(FOOTER)}</div>'
    )
    return _card(SUMMARY_WIDTH, brand, summary.subject, body)


def daily_summary_text(summary: DailySummary, brand: Brand) -> str:
    """The plain-text part, in the words it has always used, with the same four lists."""
    soon = len(summary.due_today) + len(summary.due_soon)
    tasks = len(summary.tasks_today) + len(summary.other_tasks)
    lines = [
        f"Good morning, {summary.first_name}",
        f"{len(summary.overdue)} overdue · {soon} due soon · {tasks} assigned to you"
        + (f" · {summary.flagged} flagged in the Inbox" if summary.flagged else ""),
        "",
    ]
    if summary.overdue:
        lines.append("Overdue")
        lines += [
            f"  {d.agency} · {d.kind} · {d.tracking_number} · {_count(d.late_by, 'day')} overdue"
            for d in summary.overdue[:MAX_ROWS]
        ]
        lines.append("")
    today = [f"  {d.agency} · {d.kind} · {d.tracking_number}" for d in summary.due_today] + [
        f"  Task: {_first_line(t.text)} · {t.agency}" for t in summary.tasks_today
    ]
    if today:
        lines.append("Due today")
        lines += today[:MAX_ROWS]
        lines.append("")
    if summary.mentions:
        lines.append("Mentions")
        lines += [f"  {what} · {when}" for what, when in summary.mentions[:MAX_ROWS]]
        lines.append("")
    if summary.due_soon:
        lines.append("Due soon")
        lines += [
            f"  {_short_date(d.due)} · {d.tracking_number} · {d.agency}"
            for d in summary.due_soon[:MAX_ROWS]
        ]
        lines.append("")
    if summary.other_tasks:
        lines.append("Assigned to you")
        lines += [f"  {_first_line(t.text)}" for t in summary.other_tasks[:MAX_ROWS]]
        lines.append("")
    if summary.access_asks:
        lines.append("Asks for access")
        lines += [
            f"  {who} asked for access to {tracking}"
            for who, tracking in summary.access_asks[:MAX_ROWS]
        ]
        lines.append("")
    lines.append(f"Open {brand.portal}: {brand.home_url}")
    lines.append("")
    lines.append(FOOTER)
    return "\n".join(lines)


def daily_summary(summary: DailySummary, brand: Brand) -> tuple[str, str]:
    """`(text, html)` for one person's summary."""
    return (
        daily_summary_text(summary, brand),
        _page(summary.subject, daily_summary_card(summary, brand)),
    )


# ---- the mention (design 228, right) ---------------------------------------------------------

MENTION_WIDTH = 480


@dataclass(frozen=True)
class Mention:
    """Someone @mentioned the reader. `where` is what the subject calls the place ("#filing",
    "a direct message"); `context` is the muted line under the title ("#filing", "Direct
    message"); `quote` is the drawn quote, which team chat never passes; `link` is absolute."""

    sender: str
    where: str
    context: str
    link: str
    quote: str | None = None


def _first(name: str) -> str:
    return (name.split() or [name])[0]


def mention_subject(mention: Mention) -> str:
    return f"{_first(mention.sender)} mentioned you in {mention.where}"


def mention_card(mention: Mention, brand: Brand) -> str:
    quote = (
        f'<div style="margin-top:14px;padding:12px 14px;background:#f4f6f9;border-radius:10px">'
        f"“{escape(mention.quote)}”</div>"
        if mention.quote
        else ""
    )
    body = (
        f'<div style="font-size:15px;font-weight:600">'
        f"{escape(_first(mention.sender))} mentioned you</div>"
        f'<div style="color:{MUTED};margin-top:3px">{escape(mention.context)}</div>'
        + quote
        + _button("Reply in the portal", mention.link, 18)
    )
    return _card(
        MENTION_WIDTH,
        brand,
        mention_subject(mention),
        body,
        sender=f"{mention.sender} in {brand.portal}",
        logo=False,
    )


def mention(mention: Mention, brand: Brand) -> tuple[str, str]:
    """`(text, html)` for one mention, as it happens (when the reader's preference says so)."""
    text = (
        f"{mention.sender} mentioned you in {mention.where}.\n\n"
        + (f"“{mention.quote}”\n\n" if mention.quote else "")
        + f"Reply in the portal: {mention.link}\n\n{FOOTER}"
    )
    return text, _page(mention_subject(mention), mention_card(mention, brand))


# P25a: the invitation (design 229) is Keycloak's email now (its "execute actions" email in the
# sclu email theme, keycloak/theme/src/email), so the portal no longer renders one.
