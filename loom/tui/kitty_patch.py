import re

import textual._xterm_parser
from textual.events import Key

_KITTY_BATCHED_RE = re.compile(r"\x1b\[(\d+);;([\d:]+)u")
_CSI_TERMINATORS = set("u~ABCDEFHPQRS")

_orig_sequence_to_key_events = textual._xterm_parser.XTermParser._sequence_to_key_events


def _patched_sequence_to_key_events(self, sequence, alt=False):
    if sequence.startswith("\x1b["):
        if sequence[-1] in _CSI_TERMINATORS:
            m = _KITTY_BATCHED_RE.fullmatch(sequence)
            if m:
                try:
                    chars = [chr(int(cp)) for cp in m.group(2).split(":") if cp]
                    if chars:
                        yield Key("space", "".join(chars))
                        return
                except (ValueError, OverflowError):
                    pass
                return _orig_sequence_to_key_events(self, sequence, alt=alt)
        else:
            return
    yield from _orig_sequence_to_key_events(self, sequence, alt=alt)


textual._xterm_parser.XTermParser._sequence_to_key_events = _patched_sequence_to_key_events  # type: ignore[method-assign]
