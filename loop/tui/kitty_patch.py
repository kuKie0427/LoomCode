import re

import textual._xterm_parser
from textual.events import Key

_KITTY_BATCHED_RE = re.compile(r"\x1b\[(\d+);;([\d:]+)u")

_orig_parse_extended_key = textual._xterm_parser.XTermParser._parse_extended_key


def _patched_parse_extended_key(self, sequence: str):
    m = _KITTY_BATCHED_RE.fullmatch(sequence)
    if m:
        try:
            chars = [chr(int(cp)) for cp in m.group(2).split(":") if cp]
            if chars:
                return Key("space", "".join(chars))
        except (ValueError, OverflowError):
            return None
        return None
    return _orig_parse_extended_key(self, sequence)


textual._xterm_parser.XTermParser._parse_extended_key = _patched_parse_extended_key  # type: ignore[assignment,method-assign]
