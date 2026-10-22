"""How many pages a PDF has. Counting never fails an ingest: anything it cannot count is None.

No text is extracted and nothing is rendered. An encrypted, truncated or malformed file, or one
that is not a PDF at all, has no page count, and that is all that happens.
"""

import io
import logging

from pypdf import PdfReader

logger = logging.getLogger(__name__)


def count_pdf_pages(content: bytes) -> int | None:
    if not content.lstrip()[:5].startswith(b"%PDF-"):
        return None
    # pypdf logs a warning for each oddity in a file; a scanned letter from an agency's copier has
    # many, and none of them is this system's business.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        return len(PdfReader(io.BytesIO(content), strict=False).pages)
    except Exception:  # noqa: BLE001 -- any failure to read a file somebody sent us means "unknown"
        logger.info("could not count the pages of a PDF attachment", exc_info=True)
        return None
