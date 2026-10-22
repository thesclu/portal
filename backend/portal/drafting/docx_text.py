"""The words of a letter the drafting tool keeps as a .docx, as plain text for the request's record.

A reserved request carries its letter's words (`requests.request_text`: what the request page,
search, the record PDF and the assistant read). When the letter is written in the drafting tool
the portal reads them from the agency's .docx (P21c; DECISIONS 49): the body's paragraphs in order
(tables included, a cell's paragraphs each on a line), Word's list numbers worked out from the
numbering part (`1.`, `(a)`, `i.` as the template defines them), the accepted view of tracked
changes (insertions in, deletions out), a footnote's place marked `[n]` and its text listed at the
end. Headers and footers (the letterhead's furniture) are left out; the recipient block, the date
and the closing are in the body and stay.

Never more than a size cap is decompressed, so a hostile archive cannot make it read gigabytes.
"""

import io
import zipfile
from xml.etree import ElementTree as ET  # noqa: N817

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_PART_BYTES = 20 * 1024 * 1024
_SKIPPED_FOOTNOTE_TYPES = {"separator", "continuationSeparator", "continuationNotice"}


class NotALetter(ValueError):
    """The bytes are not a .docx this module can read."""


def _part(archive: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_PART_BYTES:
        raise NotALetter(f"{name} is larger than {MAX_PART_BYTES} bytes")
    return archive.read(info)


def _roman(n: int) -> str:
    out = []
    for value, letters in (
        (1000, "m"),
        (900, "cm"),
        (500, "d"),
        (400, "cd"),
        (100, "c"),
        (90, "xc"),
        (50, "l"),
        (40, "xl"),
        (10, "x"),
        (9, "ix"),
        (5, "v"),
        (4, "iv"),
        (1, "i"),
    ):
        while n >= value:
            out.append(letters)
            n -= value
    return "".join(out)


def _letters(n: int) -> str:
    out = ""
    while n > 0:
        n, rest = divmod(n - 1, 26)
        out = chr(ord("a") + rest) + out
    return out


def _formatted(n: int, fmt: str) -> str:
    if fmt == "lowerLetter":
        return _letters(n)
    if fmt == "upperLetter":
        return _letters(n).upper()
    if fmt == "lowerRoman":
        return _roman(n)
    if fmt == "upperRoman":
        return _roman(n).upper()
    if fmt == "bullet":
        return "•"
    if fmt == "none":
        return ""
    return str(n)


class _Numbering:
    """Word's numbering part, enough to write a list paragraph's number as the template shows it."""

    def __init__(self, xml: bytes | None) -> None:
        self.levels: dict[str, dict[int, tuple[str, str, int]]] = {}  # abstract id -> level
        self.abstract_of: dict[str, str] = {}  # numId -> abstractNumId
        self.counters: dict[str, dict[int, int]] = {}
        if not xml:
            return
        root = ET.fromstring(xml)
        for abstract in root.iter(f"{W}abstractNum"):
            levels: dict[int, tuple[str, str, int]] = {}
            for lvl in abstract.iter(f"{W}lvl"):
                ilvl = int(lvl.get(f"{W}ilvl") or 0)
                fmt = lvl.find(f"{W}numFmt")
                text = lvl.find(f"{W}lvlText")
                start = lvl.find(f"{W}start")
                levels[ilvl] = (
                    fmt.get(f"{W}val", "decimal") if fmt is not None else "decimal",
                    text.get(f"{W}val", f"%{ilvl + 1}.") if text is not None else f"%{ilvl + 1}.",
                    int(start.get(f"{W}val", "1")) if start is not None else 1,
                )
            self.levels[abstract.get(f"{W}abstractNumId") or ""] = levels
        for num in root.iter(f"{W}num"):
            ref = num.find(f"{W}abstractNumId")
            if ref is not None:
                self.abstract_of[num.get(f"{W}numId") or ""] = ref.get(f"{W}val") or ""

    def label(self, num_id: str, ilvl: int) -> str:
        levels = self.levels.get(self.abstract_of.get(num_id, ""), {})
        if not levels or num_id == "0":
            return ""
        counts = self.counters.setdefault(num_id, {})
        for deeper in [lvl for lvl in counts if lvl > ilvl]:
            del counts[deeper]
        start = levels.get(ilvl, ("decimal", "", 1))[2]
        counts[ilvl] = counts.get(ilvl, start - 1) + 1
        fmt, text, _ = levels.get(ilvl, ("decimal", f"%{ilvl + 1}.", 1))
        if fmt == "bullet":
            return "•"
        for level in range(ilvl, -1, -1):
            level_fmt = levels.get(level, ("decimal", "", 1))[0]
            value = counts.get(level, levels.get(level, ("decimal", "", 1))[2])
            text = text.replace(f"%{level + 1}", _formatted(value, level_fmt))
        return text


def _numbering_of(paragraph: ET.Element) -> tuple[str, int] | None:
    props = paragraph.find(f"{W}pPr")
    numpr = props.find(f"{W}numPr") if props is not None else None
    if numpr is None:
        return None
    num = numpr.find(f"{W}numId")
    lvl = numpr.find(f"{W}ilvl")
    if num is None:
        return None
    return num.get(f"{W}val") or "0", int(lvl.get(f"{W}val") or 0) if lvl is not None else 0


# Not part of a paragraph's own words: a text box's paragraphs (read on their own), tracked
# deletions (the accepted view), a paragraph's properties.
_NOT_WORDS = {f"{W}txbxContent", f"{W}del", f"{W}pPr", f"{W}rPr"}


def _paragraph_text(paragraph: ET.Element, footnotes: dict[str, int]) -> str:
    parts: list[str] = []

    def walk(node: ET.Element) -> None:
        for child in node:
            tag = child.tag
            if tag in _NOT_WORDS:
                continue
            if tag == f"{W}t":
                parts.append(child.text or "")
            elif tag == f"{W}tab":
                parts.append("\t")
            elif tag in (f"{W}br", f"{W}cr"):
                parts.append("\n")
            elif tag == f"{W}footnoteReference":
                note_id = child.get(f"{W}id") or ""
                footnotes.setdefault(note_id, len(footnotes) + 1)
                parts.append(f"[{footnotes[note_id]}]")
            else:
                walk(child)

    walk(paragraph)
    return "".join(parts)


def extract(data: bytes) -> str:
    """The letter's words. Raises `NotALetter` for bytes that are not a readable .docx."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise NotALetter("not a .docx") from exc
    with archive:
        document = _part(archive, "word/document.xml")
        if document is None:
            raise NotALetter("no word/document.xml")
        numbering = _Numbering(_part(archive, "word/numbering.xml"))
        notes_xml = _part(archive, "word/footnotes.xml")
    try:
        root = ET.fromstring(document)
    except ET.ParseError as exc:
        raise NotALetter("the document part is not XML") from exc
    body = root.find(f"{W}body")
    if body is None:
        raise NotALetter("the document has no body")
    order: dict[str, int] = {}
    lines: list[str] = []
    for paragraph in body.iter(f"{W}p"):
        text = _paragraph_text(paragraph, order)
        numbered = _numbering_of(paragraph)
        if numbered is not None:
            label = numbering.label(*numbered)
            if label:
                text = f"{label} {text}" if text else label
        lines.append(text.rstrip())
    words = "\n".join(lines).strip("\n")
    if order and notes_xml:
        notes: dict[str, str] = {}
        for note in ET.fromstring(notes_xml).iter(f"{W}footnote"):
            if note.get(f"{W}type") in _SKIPPED_FOOTNOTE_TYPES:
                continue
            note_id = note.get(f"{W}id") or ""
            if note_id in order:
                notes[note_id] = " ".join(
                    _paragraph_text(p, {}).strip() for p in note.iter(f"{W}p")
                ).strip()
        listed = [
            f"[{n}] {notes[i]}"
            for i, n in sorted(order.items(), key=lambda kv: kv[1])
            if notes.get(i)
        ]
        if listed:
            words = f"{words}\n\n" + "\n".join(listed)
    # Collapse runs of more than one blank line (a letter's spacing paragraphs).
    collapsed: list[str] = []
    for line in words.split("\n"):
        if line == "" and collapsed and collapsed[-1] == "":
            continue
        collapsed.append(line)
    return "\n".join(collapsed).strip()
