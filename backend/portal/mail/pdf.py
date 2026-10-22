"""The letter as a PDF: the request's frozen text, set on a page, signed.

Plain paragraphs in a serif face. The text is `requests.request_text` as reserved, so what is
attached is exactly what the request says was sent; nothing is re-rendered here. The shell's
signer and page furniture (drafting_db.0003) are the only layout inputs.
"""

from dataclasses import dataclass
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


@dataclass(frozen=True)
class Layout:
    signer_name: str | None = None
    signer_title: str | None = None
    font_size_pt: int | None = None
    margin_mm: int | None = None


def letter_pdf(text: str, layout: Layout | None = None, *, title: str = "Letter") -> bytes:
    layout = layout or Layout()
    size = layout.font_size_pt or 11
    margin = (layout.margin_mm or 25) * mm
    body = ParagraphStyle("body", fontName="Times-Roman", fontSize=size, leading=size * 1.4)
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin,
        title=title,
    )
    flow = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        if not block.strip():
            continue
        flow.append(Paragraph(escape(block).replace("\n", "<br/>"), body))
        flow.append(Spacer(1, size * 0.8))
    if layout.signer_name:
        flow.append(Spacer(1, size * 2))
        flow.append(Paragraph(escape(layout.signer_name), body))
        if layout.signer_title:
            flow.append(Paragraph(escape(layout.signer_title), body))
    document.build(flow)
    return buffer.getvalue()


# ---- P19b: the letter as a page -----------------------------------------------------------------


def pdf_from_html(html: str) -> bytes:
    """One HTML (`drafting.render.render_html(..., for_pdf=True)`) → one PDF, through a pure-Python
    engine so the image needs no system libraries (DECISIONS 25)."""
    from xhtml2pdf import pisa

    buffer = BytesIO()
    result = pisa.CreatePDF(html, dest=buffer, encoding="utf-8")
    if result.err:
        raise ValueError(f"The letter could not be set on the page ({result.err} error(s)).")
    return buffer.getvalue()


def page_count(data: bytes) -> int:
    from pypdf import PdfReader

    return len(PdfReader(BytesIO(data)).pages)
