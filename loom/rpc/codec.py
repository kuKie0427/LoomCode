"""Line-based JSON codec for stdio communication between Python core and TUI.

The protocol is JSON Lines (NDJSON): one JSON object per line, terminated
by ``\\n``. This module handles:

- **Writing**: serialize an :class:`Event` / :class:`Request` / :class:`Response`
  to a single line + newline + flush. Thread-safe via a write lock.
- **Reading**: read one line, parse it, skip blank lines, raise on malformed
  input. Returns ``None`` on EOF.
- **Broken pipe handling**: if the TUI process dies mid-stream, writes are
  swallowed (logged) rather than raising — the agent loop must not crash
  just because the user closed the TUI window.
"""

from __future__ import annotations

import threading
from typing import TextIO

from loguru import logger

from loom.rpc.protocol import _Message


class LineCodec:
    """Serialize/deserialize JSON-RPC messages over a text stream.

    Args:
        reader: a text-mode file-like object (typically ``sys.stdin``).
        writer: a text-mode file-like object (typically ``sys.stdout``).
    """

    def __init__(self, reader: TextIO | None = None, writer: TextIO | None = None):
        self._reader = reader
        self._writer = writer
        self._write_lock = threading.Lock()

    def write_event(self, event: _Message) -> None:
        """Serialize ``event`` to one JSON line + newline + flush.

        Thread-safe. Swallows BrokenPipeError (TUI died) — the agent loop
        must not crash when the user closes the window mid-stream.
        """
        if self._writer is None:
            return
        line = event.to_jsonl() + "\n"
        with self._write_lock:
            try:
                self._writer.write(line)
                self._writer.flush()
            except (BrokenPipeError, OSError) as exc:
                # TUI process gone — swallow so the agent loop can finish
                # its current turn gracefully. The next read on stdin will
                # return EOF and trigger shutdown.
                logger.debug("codec write failed (TUI gone?): {}", exc)

    def read_message(self) -> _Message | None:
        """Read one JSON-RPC message from the stream.

        Returns:
            The parsed message, or ``None`` on EOF.

        Raises:
            ValueError: if the line is not valid JSON or not a valid
                protocol message.
        """
        if self._reader is None:
            return None
        while True:
            line = self._reader.readline()
            if line == "":  # EOF
                return None
            line = line.strip()
            if not line:
                continue  # skip blank lines
            # Delegate parsing to the protocol module — from_jsonl raises
            # ValueError on malformed input, which we let propagate.
            return _Message.from_jsonl(line)
