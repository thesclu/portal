"""Inbound mail: a message arrives, is kept untouched, and is placed on a request or flagged.

    postmark.py  the webhook. Authenticates, stores the delivery exactly as it came, makes the
                 source row and defers the job. It decides nothing about the message.
    parse.py     Postmark's JSON -> `InboundMessage`.
    eml.py       an .eml file -> the same `InboundMessage`. The import boundary.
    match.py     which request a message is for, from the aliases it was sent to.
    ingest.py    the job's work: attachments, then one event on one request, or a flag.
    pages.py     how many pages a PDF has, or None.

Nothing here sends anything (sending is P11), and nothing here writes a status: a message that
lands on a request is a `correspondence` event, and saying what it MEANS is a person's
classification afterwards (non-negotiable 6). Ingest runs as the system actor
`postmark_webhook`, which needs no place on a privileged request's access list; who may then SEE
the message is decided where it always is (`services/access.py`).
"""
