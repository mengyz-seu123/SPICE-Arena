"""Restricted OTA template parser; unrestricted decks use simulate."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from analog_arena.simulation.spice import StructuralError, parse_spice_number
from .diagnostics import NetlistDiagnosticError, diagnostic
PORTS = ("VDD", "GND", "VINP", "VINN", "VOUT", "IBIAS")
MOS_MODELS = {"sky130_fd_pr__nfet_01v8", "sky130_fd_pr__pfet_01v8"}
CONTRACT_VERSION = "unified-nominal-ota-v2"
def _located_lines(text: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    for number, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("*"):
            continue
        if "$" in stripped:
            stripped = stripped.split("$", 1)[0].rstrip()
        if stripped.startswith("+"):
            if not lines:
                raise NetlistDiagnosticError([diagnostic("orphan continuation line", number, stripped)])
            lines[-1] = (lines[-1][0], lines[-1][1] + " " + stripped[1:].strip())
        elif stripped:
            lines.append((number, stripped))
    return lines

def _logical_lines(text: str) -> list[str]:
    return [line for _, line in _located_lines(text)]


def _step_ok(value: float, step: float) -> bool:
    return abs(value / step - round(value / step)) <= 1e-7

def _params(tokens: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for token in tokens:
        if "=" not in token:
            raise StructuralError(f"MOS parameter must use key=value: {token}")
        key, value = token.split("=", 1)
        key = key.lower()
        if key in result:
            raise StructuralError(f"duplicate MOS parameter: {key}")
        result[key] = value
    return result

def expand_and_validate_dut(text: str) -> tuple[str, dict[str, Any]]:
    """Validate the candidate grammar and expand group M to real MOS instances."""
    located = _located_lines(text)
    lines = [line for _, line in located]
    if len(lines) < 3:
        raise NetlistDiagnosticError([diagnostic("DUT is empty or incomplete")])
    head = lines[0].split()
    expected = [".subckt", "OTA", *PORTS]
    if [token.lower() for token in head] != [token.lower() for token in expected]:
        raise NetlistDiagnosticError([diagnostic("subcircuit must be exactly: .subckt OTA VDD GND VINP VINN VOUT IBIAS", located[0][0], lines[0])])
    tail = lines[-1].split()
    if tail[0].lower() != ".ends" or (len(tail) == 2 and tail[1].lower() != "ota") or len(tail) > 2:
        raise NetlistDiagnosticError([diagnostic("DUT must end with .ends OTA", located[-1][0], lines[-1])])

    expanded = [".subckt OTA VDD GND VINP VINN VOUT IBIAS"]
    names: set[str] = set()
    mos_groups: list[dict[str, Any]] = []
    capacitors_pf: list[float] = []
    resistors_kohm: list[float] = []
    circuit_nodes: set[str] = set()
    ibias_referenced = False
    expanded_count = 0

    errors = []
    error_count = 0
    for line_number, line in located[1:-1]:
        try:
            tokens = line.split()
            name = tokens[0]
            lname = name.lower()
            if lname in names:
                raise StructuralError(f"duplicate element name: {name}")
            names.add(lname)
            kind = name[0].lower()
            if kind == "x":
                if len(tokens) < 9:
                    raise StructuralError(f"invalid MOS group: {line}")
                d, g, s, b, model = tokens[1:6]
                circuit_nodes.update((d, g, s, b))
                if model.lower() not in MOS_MODELS:
                    raise StructuralError(f"only SKY130 1.8 V MOS wrappers are allowed: {model}")
                params = _params(tokens[6:])
                if "mult" in params:
                    raise StructuralError("wrapper mult is forbidden because it is electrically ineffective; use M")
                if set(params) != {"w", "l", "m"}:
                    raise StructuralError("each MOS group requires only W, L, and M")
                try:
                    w_um = float(params["w"])
                    l_um = float(params["l"])
                    m_float = float(params["m"])
                except ValueError as exc:
                    raise StructuralError("W, L, and M must be numeric literals") from exc
                m = int(round(m_float))
                if not (0.42 <= w_um <= 10.0 and _step_ok(w_um, 0.01)):
                    raise StructuralError(f"W outside 0.42..10 um or off 0.01 um grid: {w_um}")
                if not (0.15 <= l_um <= 5.0 and _step_ok(l_um, 0.01)):
                    raise StructuralError(f"L outside 0.15..5 um or off 0.01 um grid: {l_um}")
                if m_float != m or not 1 <= m <= 500:
                    raise StructuralError(f"M must be an integer in 1..500: {m_float}")
                mos_groups.append({"name": name, "w_um": w_um, "l_um": l_um, "m": m, "model": model})
                ibias_referenced |= any(node.lower() == "ibias" for node in (d, g, s, b))
                for index in range(1, m + 1):
                    expanded.append(
                        f"{name}__{index:03d} {d} {g} {s} {b} {model} "
                        f"W={w_um:.2f} L={l_um:.2f} nf=1 mult=1"
                    )
                expanded_count += m
            elif kind == "c":
                if len(tokens) != 4:
                    raise StructuralError(f"capacitor must be: Cname node node value: {line}")
                value_pf = parse_spice_number(tokens[3]) / 1e-12
                if not (1.0 <= value_pf <= 100.0 and _step_ok(value_pf, 0.01)):
                    raise StructuralError(f"capacitor outside 1..100 pF or off 0.01 pF grid: {value_pf}")
                capacitors_pf.append(value_pf)
                circuit_nodes.update(tokens[1:3])
                ibias_referenced |= tokens[1].lower() == "ibias" or tokens[2].lower() == "ibias"
                expanded.append(line)
            elif kind == "r":
                if len(tokens) != 4:
                    raise StructuralError(f"resistor must be: Rname node node value: {line}")
                value_kohm = parse_spice_number(tokens[3]) / 1e3
                if not 0.1 <= value_kohm <= 1000.0:
                    raise StructuralError(f"resistor outside 0.1..1000 kohm: {value_kohm}")
                resistors_kohm.append(value_kohm)
                circuit_nodes.update(tokens[1:3])
                ibias_referenced |= tokens[1].lower() == "ibias" or tokens[2].lower() == "ibias"
                expanded.append(line)
            else:
                raise StructuralError(f"forbidden element or directive: {name}")
        except StructuralError as exc:
            error_count += 1
            if len(errors) < 5:
                errors.append(diagnostic(str(exc), line_number, line))
    if errors:
        raise NetlistDiagnosticError(errors, truncated=error_count > len(errors))

    if not mos_groups:
        raise NetlistDiagnosticError([diagnostic("DUT must contain at least one MOS group")])
    if not ibias_referenced:
        raise NetlistDiagnosticError([diagnostic("IBIAS must be used by the DUT")])
    expanded.append(".ends OTA")
    summary = {
        "contract_version": CONTRACT_VERSION,
        "mos_groups": mos_groups,
        "expanded_mos_count": expanded_count,
        "capacitors_pf": capacitors_pf,
        "resistors_kohm": resistors_kohm,
        "internal_nodes": sorted(
            node for node in circuit_nodes
            if node.lower() not in {port.lower() for port in PORTS}
        ),
    }
    return "\n".join(expanded) + "\n", summary
def _normalize_vss(expanded_dut: str) -> str:
    header = next(
        (line for line in expanded_dut.splitlines() if line.strip().lower().startswith(".subckt ota ")),
        "",
    )
    if [token.upper() for token in header.split()] != [
        ".SUBCKT",
        "OTA",
        "VDD",
        "GND",
        "VINP",
        "VINN",
        "VOUT",
        "IBIAS",
    ]:
        raise StructuralError(f"unexpected OTA header for VSS normalization: {header}")
    normalized = re.sub(r"(?i)(?<![A-Za-z0-9_])GND(?![A-Za-z0-9_])", "VSS", expanded_dut)
    normalized = re.sub(r"(?im)^\s*\.subckt\s+OTA\b", ".subckt OTA_VSS", normalized, count=1)
    normalized = re.sub(r"(?im)^\s*\.ends\s+OTA\s*$", ".ends OTA_VSS", normalized, count=1)
    return normalized

def _canonicalize_expanded_dut(expanded_dut: str) -> str:
    """Normalize electrically equivalent numeric formatting for identity and replay."""

    canonical: list[str] = []
    for raw in expanded_dut.splitlines():
        tokens = raw.split()
        if not tokens:
            continue
        kind = tokens[0][0].lower()
        if kind == "c" and len(tokens) == 4:
            value_pf = parse_spice_number(tokens[3]) / 1e-12
            tokens[3] = f"{value_pf:.2f}p"
        elif kind == "r" and len(tokens) == 4:
            value_kohm = parse_spice_number(tokens[3]) / 1e3
            if abs(value_kohm * 100 - round(value_kohm * 100)) > 1e-7:
                raise StructuralError(
                    f"resistor off 0.01 kOhm grid: {value_kohm}"
                )
            tokens[3] = f"{value_kohm:.2f}k"
        canonical.append(" ".join(tokens))
    return "\n".join(canonical) + "\n"

@dataclass(frozen=True)
class CandidateInput:
    source_dir: Path
    dut_text: str
    candidate: Mapping[str, Any]
    expanded_dut: str
    vss_dut: str
    structure: Mapping[str, Any]
    ibias_ua: float
    candidate_key: str

    @classmethod
    def load(cls, source_dir: Path) -> "CandidateInput":
        source = source_dir.resolve()
        dut_path = source / "dut.spice"
        params_path = source / "candidate.json"
        if not dut_path.is_file() or not params_path.is_file():
            raise ValueError("candidate_dir requires dut.spice and candidate.json")
        dut_text = dut_path.read_text(encoding="utf-8")
        candidate = json.loads(params_path.read_text(encoding="utf-8"))
        expanded, structure = expand_and_validate_dut(dut_text)
        expanded = _canonicalize_expanded_dut(expanded)
        ibias_ua = float(candidate["ibias_uA"])
        if not (1.0 <= ibias_ua <= 40.0 and abs(ibias_ua * 10 - round(ibias_ua * 10)) <= 1e-7):
            raise StructuralError("ibias_uA must be on the 1.0..40.0 uA, 0.1 uA grid")
        payload = {
            "schema": "three-direction-normalized-candidate/v2",
            "dut": expanded,
            "ibias_uA": round(ibias_ua, 1),
        }
        key = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return cls(
            source_dir=source,
            dut_text=dut_text,
            candidate=candidate,
            expanded_dut=expanded,
            vss_dut=_normalize_vss(expanded),
            structure=structure,
            ibias_ua=ibias_ua,
            candidate_key=key,
        )
