"""One letter structure, two serialisations of it -- the letter text, and the frozen snapshot.

Pure and Django-free on purpose, the same discipline `packages/deadline_rules` keeps: this is
where a letter's content is decided, so it should be testable without a database and readable
without knowing the ORM.

**Why one structure rather than two builders.** The snapshot in `letter_template_revisions.content`
is the only clean record of what a template said, because a filed letter is the template plus
that drafter's edits minus the placeholders and you cannot subtract those back out. If the
snapshot ever stopped describing what was sent, the revision system would stop being evidence,
which is its whole purpose. So neither serialiser walks a `Letter` itself: both consume the
identical `Letter.sections()` tuple. Adding a kind of content to the letter makes it appear in
both or in neither -- they cannot drift in what they cover, only in how they present it.

**Placeholders resolve in the text and stay literal in the snapshot.** The names in
`PLACEHOLDER_NAMES` come from the agency directory, the recipient's channel and the date at render
time (`filing.placeholders_for` gathers them), and per-agency exceptions from
`letter_recipients.placeholder_overrides`; none of it is stored. A snapshot describes a template,
not any one recipient's letter, so it holds the text with its placeholders still in it, which is
what the template actually says.

**One order for prose and asks (P10).** The editor places a shared block between items or after
the last one ("Goes after item 4"), so blocks and items share ONE `sort_order` sequence: the body
of the letter is ordered by `sort_order` first and only then by kind. Until P10 every block came
ahead of every item whatever its position. Letterhead, subject and opening still come before the
body, citations and the closing after it.

**Stable ordering, and why array order is the half that matters.** `content` is `jsonb`, and
Postgres jsonb does not preserve object key order at all: it normalises keys on storage, so a
round-tripped dict compares equal whatever order it was written in. What jsonb does preserve is
array element order, and the snapshot is an array of sections -- so the requirement is a TOTAL,
run-stable order on that array, or an unchanged template serialises differently between runs and
mints a revision over nothing but noise. Sections sort by (the part of the letter their kind
belongs to, `sort_order`, their kind's rank, `label`, `body`): total even when two rows tie on
`sort_order` and on `label`, since two sections equal on all of those are interchangeable. Never
by row id -- a uuid would reorder the array whenever a row was retyped rather than edited. Keys
are emitted sorted anyway, so an exported row diffs cleanly for a human too.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

# The order the kinds appear in a finished letter. Also the first component of the sort key, so
# it decides the snapshot array's order as much as it decides the text's.
KIND_ORDER = ("letterhead", "subject", "opening", "block", "item", "citation", "closing")

# The part of the letter each kind belongs to. Within the body, blocks and items interleave.
_ZONE = {
    "letterhead": 0,
    "subject": 0,
    "opening": 0,
    "block": 1,
    "item": 1,
    "citation": 2,
    "closing": 3,
}

PLACEHOLDER_NAMES = (
    "AGENCY_FULL",
    "AGENCY_ABBR",
    "RECIPIENT_OFFICE",
    "ADDRESS",
    "AGENCY_STREETADDRESS",
    "AGENCY_CITYSTATEZIP",
    "CHANNEL_METHOD",
    "FILING_DATE",
)

# Bumped when the shape or the order of a snapshot changes, so two snapshots of different
# versions are never mistaken for an edit. 2 (P10): one order for blocks and items, and an item's
# `provision` is part of what a template says. 3 (P19b): a section carries its editor document.
SNAPSHOT_VERSION = 3

_OPEN, _CLOSE = "{{", "}}"
# Reported among the unresolved names when a letter holds a `{{` or `}}` that is not part of a
# well-formed placeholder.
UNMATCHED_BRACES = "(an unmatched {{ or }})"
_PLACEHOLDER = re.compile(r"\{\{([^{}]*)\}\}")


@dataclass(frozen=True)
class Section:
    """One addressable piece of a letter. `label` is the item number, the citation marker or the
    block name; `parent` is an item's parent item number, empty when it has none."""

    kind: str
    body: str
    label: str = ""
    parent: str = ""
    sort_order: int = 0
    # An item's own-clock provision. Never part of the letter's text; part of the snapshot,
    # because it decides which deadlines a request filed from this template gets.
    provision: str = ""
    # P19b: the editor's document for this piece, when it was written in the editor. The plain
    # `body` is derived from it and is what the text serialisation and search read.
    rich: Any = None

    def sort_key(self) -> tuple[int, int, int, str, str]:
        return (
            _ZONE[self.kind],
            self.sort_order,
            KIND_ORDER.index(self.kind),
            # A block's name is not part of its place: a draft keeps a block's text and position
            # but not its name, and the letter must order two tied blocks the same way its
            # revision does. So tied blocks order by their text alone.
            "" if self.kind == "block" else self.label,
            self.body,
        )


@dataclass(frozen=True)
class Shell:
    letterhead: str = ""
    opening_body: str = ""
    closing_body: str = ""
    opening_rich: Any = None
    closing_rich: Any = None


@dataclass(frozen=True)
class Item:
    number: str
    body: str
    parent_number: str = ""
    sort_order: int = 0
    provision: str = ""
    rich: Any = None


@dataclass(frozen=True)
class Citation:
    marker: str
    citation_text: str
    item_number: str = ""
    sort_order: int = 0


@dataclass(frozen=True)
class Block:
    """A shared block already resolved to plain text -- drafts resolve them at creation, so
    editing the block later never changes a letter someone is midway through."""

    name: str
    body: str
    sort_order: int = 0
    rich: Any = None


@dataclass(frozen=True)
class Letter:
    subject: str
    shell: Shell | None = None
    items: tuple[Item, ...] = ()
    citations: tuple[Citation, ...] = ()
    blocks: tuple[Block, ...] = ()
    # Free-form provenance carried into the snapshot: template name, jurisdiction, legal basis.
    # Never any part of the rendered text.
    meta: Mapping[str, str] = field(default_factory=dict)

    def sections(self) -> tuple[Section, ...]:
        """The single linearisation both serialisers consume. Everything downstream of here sees
        a letter only as this tuple."""
        out: list[Section] = []
        if self.shell is not None:
            if self.shell.letterhead:
                out.append(Section("letterhead", self.shell.letterhead))
        if self.subject:
            out.append(Section("subject", self.subject))
        if self.shell is not None and (self.shell.opening_body or self.shell.opening_rich):
            out.append(Section("opening", self.shell.opening_body, rich=self.shell.opening_rich))
        out += [
            Section("block", b.body, b.name, "", b.sort_order, rich=b.rich) for b in self.blocks
        ]
        out += [
            Section(
                "item", i.body, i.number, i.parent_number, i.sort_order, i.provision, rich=i.rich
            )
            for i in self.items
        ]
        out += [
            Section("citation", c.citation_text, c.marker, c.item_number, c.sort_order)
            for c in self.citations
        ]
        if self.shell is not None and (self.shell.closing_body or self.shell.closing_rich):
            out.append(Section("closing", self.shell.closing_body, rich=self.shell.closing_rich))
        return tuple(sorted(out, key=Section.sort_key))


def resolve(text: str, placeholders: Mapping[str, str]) -> str:
    """Substitute `{{NAME}}`. An unknown name is left standing rather than blanked: a letter that
    goes out saying `{{ADDRESS}}` is a visible mistake, one that goes out saying nothing is not.

    One pass over `text`, not one `str.replace` per placeholder. Looping over `placeholders` and
    replacing each name in turn re-scans the text a value just inserted: a value that itself
    contains `{{OTHER_NAME}}` (an override that happens to look like a placeholder, or one
    placeholder's resolved value quoting another literally) could get substituted a second time
    by whichever name the loop reaches next, depending on dict order. A single regex pass
    resolves every `{{NAME}}` against the ORIGINAL text once and never re-reads a value it just
    wrote in.
    """

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        return placeholders[name] if name in placeholders else match.group(0)

    return _PLACEHOLDER.sub(_sub, text)


def unresolved_placeholders(letter: Letter, placeholders: Mapping[str, str]) -> tuple[str, ...]:
    """Which `{{NAME}}` markers would survive rendering, so a caller can refuse to send."""
    rendered = render_text(letter, placeholders)
    found: list[str] = []
    rest = rendered
    while _OPEN in rest:
        _, _, rest = rest.partition(_OPEN)
        name, sep, rest = rest.partition(_CLOSE)
        if sep and name not in found:
            found.append(name)
    # Whatever is left after every well-formed `{{NAME}}` is taken out must hold no brace pair at
    # all: an unclosed `{{ADDRESS` or a stray `}}` is a blank nobody will ever fill.
    remainder = _PLACEHOLDER.sub("", rendered)
    if _OPEN in remainder or _CLOSE in remainder:
        found.append(UNMATCHED_BRACES)
    return tuple(sorted(found))


def render_text(letter: Letter, placeholders: Mapping[str, str] | None = None) -> str:
    """Serialisation one: the letter as it goes out, handed to `reserve_request`."""
    values = placeholders or {}
    return "\n\n".join(resolve(_section_text(s), values) for s in letter.sections())


def snapshot(letter: Letter) -> dict[str, Any]:
    """Serialisation two: what gets frozen into `letter_template_revisions.content`.

    Self-contained by design -- shell text, resolved blocks, items and citations all inline --
    so the row is readable years later without joining anything that may since have changed.
    """
    return {
        "meta": dict(sorted(letter.meta.items())),
        "sections": [_section_dict(s) for s in letter.sections()],
        "version": SNAPSHOT_VERSION,
    }


def section_plain(section: Section) -> str:
    """The words of one piece: from its editor document when it has one, else its text."""
    return rich_text(section.rich) if section.rich else section.body


def _section_text(section: Section) -> str:
    if section.kind == "item":
        return f"{section.label}. {section_plain(section)}"
    if section.kind == "citation":
        return f"[{section.label}] {section.body}"
    return section_plain(section)


def _section_dict(section: Section) -> dict[str, Any]:
    """Always the same six keys in the same order, whatever the kind -- so a diff of two
    snapshots lines up field for field instead of shifting."""
    return {
        "body": section.body,
        "kind": section.kind,
        "label": section.label,
        "parent": section.parent,
        "provision": section.provision,
        "rich": section.rich,
        "sort_order": section.sort_order,
    }


def agency_placeholders(
    *,
    agency_name: str,
    agency_abbreviation: str = "",
    recipient_office: str = "",
    address: str = "",
    street_address: str = "",
    city_state_zip: str = "",
    channel_method: str = "",
    filing_date: str = "",
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """The defaults from the agency directory, the channel and the date, then this recipient's
    exceptions on top.

    Only the exceptions are stored (`letter_recipients.placeholder_overrides`); the defaults are
    recomputed on every render, so an agency that renames itself renames itself in the next
    letter without anything being migrated.

    A blank value is dropped rather than kept as `""`. `resolve()`'s whole design is that an
    unknown name is left standing as `{{NAME}}` rather than blanked, on the theory that a letter
    saying `{{ADDRESS}}` is a visible mistake and one saying nothing is not -- but an agency with
    no verified email, no portal URL and no contact used to supply `""` for every one of those,
    which resolved cleanly and made `unresolved_placeholders()` report nothing wrong. An empty
    resolution is not a resolution: dropping the key here is what puts it back in front of
    `unresolved_placeholders()`, for a default AND for an override that resolves blank alike.
    """
    values = {
        "AGENCY_FULL": agency_name,
        "AGENCY_ABBR": agency_abbreviation or agency_name,
        "RECIPIENT_OFFICE": recipient_office,
        "ADDRESS": address,
        "AGENCY_STREETADDRESS": street_address,
        "AGENCY_CITYSTATEZIP": city_state_zip,
        "CHANNEL_METHOD": channel_method,
        "FILING_DATE": filing_date,
    }
    for name, value in (overrides or {}).items():
        if value is not None and not isinstance(value, str):
            raise ValueError(
                f"The value given for {name} must be text, not {type(value).__name__}."
            )
        values[str(name)] = value or ""
    # Blank means blank: a value of spaces fills nothing in.
    return {name: value.strip() for name, value in values.items() if value and value.strip()}


def letter_from_parts(
    *,
    subject: str,
    shell: Shell | None,
    items: Sequence[Item],
    citations: Sequence[Citation],
    blocks: Sequence[Block] = (),
    meta: Mapping[str, str] | None = None,
) -> Letter:
    """The one constructor the service layer uses, for a template and for a draft alike. Both
    sides build the same value, which is what lets one snapshot describe what one letter said."""
    return Letter(
        subject=subject,
        shell=shell,
        items=tuple(items),
        citations=tuple(citations),
        blocks=tuple(blocks),
        meta=dict(meta or {}),
    )


# ---- P19h: a block's footnotes, renumbered to follow the letter's ----------------------------


def renumber_footnotes(body: str, rich: Any, mapping: Mapping[str, str]) -> tuple[str, Any]:
    """The same words with each footnote marker in `mapping` replaced: `[old]` in the text and
    `footnoteRef` nodes in the document. Markers not in the mapping are left alone."""
    if not mapping:
        return body, rich
    pattern = re.compile(r"\[(" + "|".join(re.escape(k) for k in mapping) + r")\]")
    text = pattern.sub(lambda m: "[" + mapping[m.group(1)] + "]", body or "")
    return text, _map_footnote_refs(rich, mapping) if rich else rich


def _map_footnote_refs(node: Any, mapping: Mapping[str, str]) -> Any:
    if isinstance(node, list):
        return [_map_footnote_refs(n, mapping) for n in node]
    if not isinstance(node, dict):
        return node
    out = dict(node)
    if node.get("type") == "footnoteRef":
        attrs = dict(node.get("attrs") or {})
        old = str(attrs.get("marker") or "")
        if old in mapping:
            attrs["marker"] = mapping[old]
        out["attrs"] = attrs
    if "content" in node:
        out["content"] = _map_footnote_refs(node["content"], mapping)
    return out


# ---- P19b: the editor's document, as text and as HTML -------------------------------------------
#
# The editor stores a ProseMirror document: a tree of nodes (`doc`, `paragraph`, `heading`,
# `bulletList`/`orderedList`/`listItem`, `table`/`tableRow`/`tableCell`/`tableHeader`, `text`
# with marks, `hardBreak`, and two of ours: `placeholder` with `attrs.name`, and `footnoteRef`
# with `attrs.marker`). Both serialisations below walk that tree; anything unknown is walked for
# its text, never dropped.

_BLOCK_NODES = {"paragraph", "heading", "blockquote", "listItem", "tableRow"}


def rich_text(doc: Any) -> str:
    """The document as plain text: paragraphs separated by a blank line, list items marked,
    table cells joined with " | ", a placeholder as `{{NAME}}`, a footnote reference as `[n]`."""
    if not isinstance(doc, dict):
        return ""
    blocks: list[str] = []
    _walk_text(doc, blocks, depth=0, ordered=None)
    return "\n\n".join(b for b in blocks if b.strip())


def _inline_text(node: dict) -> str:
    kind = node.get("type")
    if kind == "text":
        return str(node.get("text") or "")
    if kind == "hardBreak":
        return "\n"
    if kind == "placeholder":
        return _OPEN + str((node.get("attrs") or {}).get("name") or "") + _CLOSE
    if kind == "footnoteRef":
        return "[" + str((node.get("attrs") or {}).get("marker") or "") + "]"
    return "".join(_inline_text(c) for c in node.get("content") or [])


def _walk_text(node: dict, blocks: list[str], *, depth: int, ordered: int | None) -> None:
    kind = node.get("type")
    children = node.get("content") or []
    if kind in ("paragraph", "heading"):
        blocks.append(_inline_text(node).strip())
        return
    if kind == "bulletList":
        for child in children:
            text = _list_item_text(child)
            blocks.append(("  " * depth) + "- " + text)
        return
    if kind == "orderedList":
        start = int((node.get("attrs") or {}).get("start") or 1)
        for offset, child in enumerate(children):
            text = _list_item_text(child)
            blocks.append(("  " * depth) + f"{start + offset}. " + text)
        return
    if kind == "table":
        rows: list[str] = []
        for row in children:
            cells = [_inline_text(cell).strip() for cell in row.get("content") or []]
            rows.append(" | ".join(cells))
        blocks.append("\n".join(rows))
        return
    for child in children:
        _walk_text(child, blocks, depth=depth, ordered=ordered)


def _list_item_text(item: dict) -> str:
    parts: list[str] = []
    for child in item.get("content") or []:
        if child.get("type") in ("bulletList", "orderedList"):
            inner: list[str] = []
            _walk_text(child, inner, depth=1, ordered=None)
            parts.append("\n" + "\n".join(inner))
        else:
            parts.append(_inline_text(child).strip())
    return "".join(parts)


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def rich_html(doc: Any, placeholders: Mapping[str, str] | None = None) -> str:
    """The document as HTML for the page and the PDF. Placeholders resolve as in the text; one
    left standing renders as a marked span so it is visible, never blank."""
    if not isinstance(doc, dict):
        return ""
    return "".join(_node_html(c, placeholders or {}) for c in doc.get("content") or [])


_MARK_TAGS = {"bold": "strong", "italic": "em", "underline": "u", "strike": "s", "code": "code"}


def _inline_html(node: dict, placeholders: Mapping[str, str]) -> str:
    kind = node.get("type")
    if kind == "text":
        html = _escape(str(node.get("text") or ""))
        for mark in node.get("marks") or []:
            tag = _MARK_TAGS.get(mark.get("type"))
            if tag:
                html = f"<{tag}>{html}</{tag}>"
        return html
    if kind == "hardBreak":
        return "<br/>"
    if kind == "placeholder":
        name = str((node.get("attrs") or {}).get("name") or "")
        if name in placeholders:
            return _escape(placeholders[name])
        return f'<span class="placeholder">{_escape(_OPEN + name + _CLOSE)}</span>'
    if kind == "footnoteRef":
        marker = _escape(str((node.get("attrs") or {}).get("marker") or ""))
        return f'<sup class="fn">{marker}</sup>'
    return "".join(_inline_html(c, placeholders) for c in node.get("content") or [])


def _node_html(node: dict, placeholders: Mapping[str, str]) -> str:
    kind = node.get("type")
    children = node.get("content") or []
    inner = lambda: "".join(_node_html(c, placeholders) for c in children)  # noqa: E731
    inline = lambda: "".join(_inline_html(c, placeholders) for c in children)  # noqa: E731
    if kind == "paragraph":
        return f"<p>{inline()}</p>"
    if kind == "heading":
        level = min(max(int((node.get("attrs") or {}).get("level") or 2), 1), 4)
        return f"<h{level}>{inline()}</h{level}>"
    if kind == "bulletList":
        return f"<ul>{inner()}</ul>"
    if kind == "orderedList":
        start = int((node.get("attrs") or {}).get("start") or 1)
        attr = f' start="{start}"' if start != 1 else ""
        return f"<ol{attr}>{inner()}</ol>"
    if kind == "listItem":
        return f"<li>{inner()}</li>"
    if kind == "blockquote":
        return f"<blockquote>{inner()}</blockquote>"
    if kind == "horizontalRule":
        return "<hr/>"
    if kind == "table":
        return f'<table class="grid">{inner()}</table>'
    if kind == "tableRow":
        return f"<tr>{inner()}</tr>"
    if kind in ("tableCell", "tableHeader"):
        tag = "th" if kind == "tableHeader" else "td"
        return f"<{tag}>{inner()}</{tag}>"
    if kind == "text" or kind in ("hardBreak", "placeholder", "footnoteRef"):
        return _inline_html(node, placeholders)
    return inner()


@dataclass(frozen=True)
class PageLayout:
    """How the page looks: the letterhead's font, margins and seal (drafting_db.0004)."""

    font: str = "Times New Roman"
    font_size_pt: int = 12
    margin_inches: float = 1.0
    seal_position: str = "left"
    seal_data_uri: str | None = None
    signer_name: str | None = None
    signer_title: str | None = None


def _text_html(text: str, placeholders: Mapping[str, str]) -> str:
    """Plain text with placeholders, as paragraphs; one left standing is marked."""
    out = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        if not block.strip():
            continue
        resolved = resolve(block, placeholders)
        html = _escape(resolved).replace("\n", "<br/>")
        html = _PLACEHOLDER.sub(lambda m: f'<span class="placeholder">{m.group(0)}</span>', html)
        out.append(f"<p>{html}</p>")
    return "".join(out)


def _section_html(section: Section, placeholders: Mapping[str, str]) -> str:
    if section.rich:
        body = rich_html(section.rich, placeholders)
    else:
        body = _text_html(section.body, placeholders)
    if section.kind == "item":
        return f'<div class="item"><span class="n">{_escape(section.label)}.</span>{body}</div>'
    if section.kind == "citation":
        return (
            f'<li class="footnote"><span class="m">{_escape(section.label)}</span> '
            f"{_text_html(section.body, placeholders)}</li>"
        )
    if section.kind == "letterhead":
        return f'<div class="letterhead">{body}</div>'
    if section.kind == "subject":
        return f'<p class="subject">Re: {_escape(resolve(section.body, placeholders))}</p>'
    return f'<div class="{section.kind}">{body}</div>'


def render_html(
    letter: Letter,
    placeholders: Mapping[str, str] | None = None,
    layout: PageLayout | None = None,
    *,
    for_pdf: bool = False,
) -> str:
    """Serialisation three (P19b): the letter as a page, one HTML the screen and the PDF share.
    Page numbers are the PDF engine's tags when `for_pdf`, and a static footer otherwise."""
    values = placeholders or {}
    layout = layout or PageLayout()
    sections = letter.sections()
    body = "".join(_section_html(s, values) for s in sections if s.kind != "citation")
    notes = "".join(_section_html(s, values) for s in sections if s.kind == "citation")
    seal = ""
    if layout.seal_data_uri and layout.seal_position != "none":
        seal = f'<img class="seal {layout.seal_position}" src="{layout.seal_data_uri}" alt=""/>'
    signer = ""
    if layout.signer_name:
        title = f"<br/>{_escape(layout.signer_title)}" if layout.signer_title else ""
        signer = f'<p class="signer">{_escape(layout.signer_name)}{title}</p>'
    footer = (
        '<div id="footer"><pdf:pagenumber/> of <pdf:pagecount/></div>'
        if for_pdf
        else '<div id="footer" class="screen"></div>'
    )
    css = _page_css(layout, for_pdf)
    footnotes = f'<ol class="footnotes">{notes}</ol>' if notes else ""
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"/>'
        f"<title>{_escape(letter.subject)}</title><style>{css}</style></head>"
        f'<body><div class="page">{seal}{body}{footnotes}{signer}</div>{footer}</body></html>'
    )


def _page_css(layout: PageLayout, for_pdf: bool) -> str:
    size = layout.font_size_pt
    margin = f"{layout.margin_inches}in"
    page = (
        f"@page {{ size: letter; margin: {margin}; @frame footer {{ -pdf-frame-content: footer; "
        f"bottom: 0.4in; margin-left: {margin}; margin-right: {margin}; height: 0.3in; }} }}"
        if for_pdf
        else (
            f".page {{ width: 8.5in; min-height: 11in; padding: {margin}; margin: 0 auto; "
            "background: #fff; box-sizing: border-box; }}"
        )
    )
    return (
        page
        + f" body {{ font-family: '{layout.font}', 'Times New Roman', serif; font-size: {size}pt;"
        " line-height: 1.35; color: #000; }"
        " p { margin: 0 0 0.6em 0; } h1, h2, h3, h4 { margin: 0.6em 0 0.3em 0; }"
        " .letterhead { margin-bottom: 1em; white-space: pre-line; }"
        " .seal { width: 1.1in; display: block; margin-bottom: 0.4em; }"
        " .seal.center { margin-left: auto; margin-right: auto; }"
        " .subject { font-weight: bold; margin: 0.8em 0; }"
        " .item { margin: 0 0 0.5em 0.35in; } .item .n { display: inline-block; width: 0.35in;"
        " margin-left: -0.35in; }"
        " .item p { display: inline; } .item p + p { display: block; margin-left: 0; }"
        " .footnotes { margin-top: 1.2em; padding-top: 0.4em; border-top: 1px solid #000;"
        f" font-size: {max(size - 2, 8)}pt; list-style: none; padding-left: 0; }}"
        " .footnote .m { display: inline-block; min-width: 1.2em; vertical-align: super;"
        " font-size: 80%; } .footnote p { display: inline; }"
        " sup.fn { font-size: 70%; vertical-align: super; }"
        " .placeholder { background: #e8f0fb; color: #0b4ea2; padding: 0 0.2em; }"
        " table.grid { margin: 0.6em 0; }"
        " table.grid td, table.grid th { border: 1px solid #000; padding: 0.2em 0.4em;"
        " vertical-align: top; }"
        " .signer { margin-top: 2.5em; } #footer { text-align: center; font-size: 9pt; }"
    )


def text_page_html(
    text: str, subject: str, layout: PageLayout | None = None, *, for_pdf: bool
) -> str:
    """A frozen letter (a request's `request_text`, exactly as reserved) set on the same page
    the editor renders: the words are not re-rendered, only placed."""
    layout = layout or PageLayout()
    seal = ""
    if layout.seal_data_uri and layout.seal_position != "none":
        seal = f'<img class="seal {layout.seal_position}" src="{layout.seal_data_uri}" alt=""/>'
    signer = ""
    if layout.signer_name:
        title = f"<br/>{_escape(layout.signer_title)}" if layout.signer_title else ""
        signer = f'<p class="signer">{_escape(layout.signer_name)}{title}</p>'
    footer = (
        '<div id="footer"><pdf:pagenumber/> of <pdf:pagecount/></div>'
        if for_pdf
        else '<div id="footer" class="screen"></div>'
    )
    body = _text_html(text, {})
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"/>'
        f"<title>{_escape(subject)}</title><style>{_page_css(layout, for_pdf)}</style></head>"
        f'<body><div class="page">{seal}{body}{signer}</div>{footer}</body></html>'
    )
