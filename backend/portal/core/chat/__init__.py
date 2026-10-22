"""Team chat (P22): channels, private channels and direct messages for the whole portal.

owner's rulings (2026-10-16/17; DECISIONS 32, 33c, 37h, 42d, 45): it belongs to the portal, not
to one tool, and anyone signed in may use it, requesters included; anyone may create a channel;
some channels are private (members only); direct messages; a message may refer to pages in any
tool, and a link to a request shows its title only to a reader allowed to see that request; an
edit shows "edited" and a delete leaves "<name> deleted this message"; every version of every
message is kept, deleted ones included, and only the legal director (and the database) can read
every conversation, for discovery; attachments; @mentions notify; instant delivery.

  access.py     who may read, write in, join and archive a conversation, and who the legal
                director is
  channels.py   creating, joining, leaving and archiving channels; direct conversations; the list
  messages.py   posting, editing, deleting, reading, mentions and attachments
  render.py     a message as one reader sees it: request links resolved for that reader
  live.py       telling the sockets (Django Channels groups), after the write commits
  consumers.py  the websocket a signed-in tab holds open
  discovery.py  the legal director's read-only route over everything
  schemas.py    the API's shapes, shared by the HTTP routes and the socket

Conversations are plain (the server reads them); `ChatChannel.encryption` is `none` and nothing
in this part writes anything else. End-to-end channels are a later part.
"""
