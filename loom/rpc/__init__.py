"""JSON-RPC protocol between the Python loom core and external TUI frontends.

The protocol is line-delimited JSON (JSON Lines / NDJSON): each message is a
single JSON object on one line terminated by ``\n``. This is simpler than
framed JSON-RPC over a socket and is a natural fit for stdio communication
(a child process's stdin/stdout are already line-buffered by default).

Message direction:
- Python -> TUI: ``Event`` (streamed, no response expected) and
  ``Response`` (reply to a prior TUI ``Request``).
- TUI -> Python: ``Request`` (expects a ``Response``) and
  ``Notification`` (no response expected — e.g. cancel).
"""
