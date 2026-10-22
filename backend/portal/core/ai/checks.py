"""The rail's Checks (P19d): deterministic, local, no model. Placeholders left standing per
agency, house style (a word list and straight quotes, both from settings), an item naming a
provision no rule covers, an empty piece, a citation nothing references."""

import re
from dataclasses import dataclass
from uuid import UUID

from portal.core.services.settings import get_setting
from portal.drafting import render
from portal.drafting.models import LetterDraft, LetterRecipient
from portal.drafting.services import filing
from portal.drafting.services.common import letter_of_draft
from portal.drafting.services.letter import draft_view
from portal.records.services.common import rule_data


@dataclass(frozen=True)
class Fix:
    """P19h: what the rail may apply in the editor with one click. `word`: replace `find` with
    `replace` as a whole word; `quotes`: turn straight quotes into typographic ones."""

    mode: str
    find: str = ""
    replace: str = ""


@dataclass(frozen=True)
class Check:
    kind: str
    title: str
    detail: str
    piece_id: UUID | None = None
    fix: Fix | None = None


def house_style_pairs() -> list[tuple[str, str]]:
    out = []
    for line in str(get_setting("ai.house_style") or "").splitlines():
        if "=>" in line:
            a, b = line.split("=>", 1)
            if a.strip():
                out.append((a.strip(), b.strip()))
    return out


def checks_for(draft: LetterDraft) -> list[Check]:
    out: list[Check] = []
    view = draft_view(draft)
    letter = letter_of_draft(draft)
    # Placeholders standing, per agency.
    for recipient in LetterRecipient.objects.filter(draft_id=draft.pk).select_related("agency"):
        values = filing.placeholders_for(recipient, recipient.agency)
        standing = render.unresolved_placeholders(letter, values)
        if standing:
            out.append(
                Check(
                    "placeholder",
                    f"Blanks for {recipient.agency.name}",
                    "Still standing: " + ", ".join(standing),
                )
            )
    pairs = house_style_pairs()
    straight = re.compile(r"[\"']")
    provisions = {r.provision for r in rule_data().rules if r.provision}
    # A footnote is referenced when a piece or the shell's own words point at it, not when the
    # footnote list at the foot of the page names it.
    shell_words = [view.shell.opening_body, view.shell.closing_body] if view.shell else []
    written = "\n".join([w or "" for w in shell_words] + [p.body or "" for p in view.pieces])
    referenced = set(re.findall(r"\[([^\]]+)\]", written))
    for piece in view.pieces:
        text = piece.body or ""
        if not text.strip():
            out.append(Check("empty", "Empty piece", "A piece with nothing in it.", piece.id))
            continue
        for src, dst in pairs:
            if re.search(r"\b" + re.escape(src) + r"\b", text, re.I):
                out.append(
                    Check(
                        "house_style",
                        f"“{src}” → {dst}" if dst else f"“{src}”",
                        f"In piece {piece.item_number or 'paragraph'}.",
                        piece.id,
                        Fix("word", src, dst) if dst else None,
                    )
                )
        if straight.search(text):
            out.append(
                Check(
                    "house_style",
                    "Straight quote",
                    f"In piece {piece.item_number or 'paragraph'}.",
                    piece.id,
                    Fix("quotes"),
                )
            )
        if piece.provision and provisions and piece.provision not in provisions:
            out.append(
                Check(
                    "provision",
                    "No rule for this clock",
                    f"Item {piece.item_number} names {piece.provision}, which no rule covers.",
                    piece.id,
                )
            )
    for cite in view.citations:
        if cite.marker not in referenced:
            out.append(
                Check(
                    "citation",
                    f"Footnote {cite.marker} is not referenced",
                    "No item or paragraph points at it.",
                )
            )
    return out
