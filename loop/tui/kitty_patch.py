import os
import re

import textual._xterm_parser
from textual.events import Key

_KITTY_BATCHED_RE = re.compile(r"\x1b\[(\d+);;([\d:]+)u")
_CSI_TERMINATORS = set("u~ABCDEFHPQRS")

_orig_sequence_to_key_events = textual._xterm_parser.XTermParser._sequence_to_key_events

_DEBUG_LOG = os.environ.get("LOOP_KITTY_DEBUG")


def _patched_sequence_to_key_events(self, sequence, alt=False):
    if _DEBUG_LOG:
        with open(_DEBUG_LOG, "a") as f:
            f.write(f"CALL seq={sequence!r}\n")
    if sequence.startswith("\x1b["):
        if sequence[-1] in _CSI_TERMINATORS:
            m = _KITTY_BATCHED_RE.fullmatch(sequence)
            if m:
                try:
                    chars = [chr(int(cp)) for cp in m.group(2).split(":") if cp]
                    if chars:
                        key = Key("space", "".join(chars))
                        if _DEBUG_LOG:
                            with open(_DEBUG_LOG, "a") as f:
                                f.write(f"YIELD char={key.character!r}\n")
                        yield key
                        return
                except (ValueError, OverflowError):
                    pass
                return _orig_sequence_to_key_events(self, sequence, alt=alt)
        else:
            if _DEBUG_LOG:
                with open(_DEBUG_LOG, "a") as f:
                    f.write(f"PARTIAL seq={sequence!r}\n")
            return
    for ev in _orig_sequence_to_key_events(self, sequence, alt=alt):
        if _DEBUG_LOG:
            with open(_DEBUG_LOG, "a") as f:
                f.write(f"FALLTHROUGH char={ev.character!r} key={ev.key!r}\n")
        yield ev


textual._xterm_parser.XTermParser._sequence_to_key_events = _patched_sequence_to_key_events  # type: ignore[method-assign]
