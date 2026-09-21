from __future__ import annotations

import re


NUMBER_RE = re.compile(
    r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)(meg|[tgkmunpf]?)$",
    re.I,
)
SUFFIX = {
    "": 1.0,
    "t": 1e12,
    "g": 1e9,
    "meg": 1e6,
    "k": 1e3,
    "m": 1e-3,
    "u": 1e-6,
    "n": 1e-9,
    "p": 1e-12,
    "f": 1e-15,
}


class StructuralError(ValueError):
    """Candidate netlist violates its selected circuit profile contract."""


def parse_spice_number(token: str) -> float:
    match = NUMBER_RE.fullmatch(token.strip())
    if not match:
        raise StructuralError(f"non-literal SPICE number: {token}")
    return float(match.group(1)) * SUFFIX[match.group(2).lower()]
