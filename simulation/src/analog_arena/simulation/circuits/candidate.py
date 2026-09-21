from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping

from analog_arena.simulation.spice import StructuralError, parse_spice_number

from .specs import BJT, MOS_HV, MOS_1V8, CircuitSpec


CONTRACT_VERSION = "sky130-generic-candidate-v1"
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _logical_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = raw.strip()
        if not stripped or stripped.startswith("*"):
            continue
        if "$" in stripped:
            stripped = stripped.split("$", 1)[0].rstrip()
        if stripped.startswith("+"):
            if not lines:
                raise StructuralError("orphan continuation line")
            lines[-1] += " " + stripped[1:].strip()
        else:
            lines.append(stripped)
    return lines


def _params(tokens: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for token in tokens:
        if "=" not in token:
            raise StructuralError(f"device parameter must use key=value: {token}")
        key, value = token.split("=", 1)
        lowered = key.lower()
        if lowered in result:
            raise StructuralError(f"duplicate device parameter: {key}")
        result[lowered] = value
    return result


def _finite_literal(value: str, name: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise StructuralError(f"{name} must be a numeric literal") from exc
    if not math.isfinite(number):
        raise StructuralError(f"{name} must be finite")
    return number


def _subcircuits(lines: list[str]) -> dict[str, tuple[list[str], list[str]]]:
    blocks: dict[str, tuple[list[str], list[str]]] = {}
    current_name: str | None = None
    current_ports: list[str] = []
    body: list[str] = []
    for line in lines:
        tokens = line.split()
        directive = tokens[0].lower()
        if directive == ".subckt":
            if current_name is not None or len(tokens) < 3:
                raise StructuralError("nested or incomplete .subckt is forbidden")
            current_name = tokens[1]
            if not NAME_RE.fullmatch(current_name):
                raise StructuralError(f"invalid subcircuit name: {current_name}")
            current_ports = tokens[2:]
            body = []
            continue
        if directive == ".ends":
            if current_name is None:
                raise StructuralError("orphan .ends")
            if len(tokens) > 2 or (len(tokens) == 2 and tokens[1].lower() != current_name.lower()):
                raise StructuralError(f".ends must match {current_name}")
            key = current_name.lower()
            if key in blocks:
                raise StructuralError(f"duplicate subcircuit: {current_name}")
            blocks[key] = (list(current_ports), list(body))
            current_name = None
            current_ports = []
            body = []
            continue
        if current_name is None:
            raise StructuralError(f"content outside .subckt is forbidden: {tokens[0]}")
        if directive.startswith("."):
            raise StructuralError(f"directive is forbidden in candidate: {tokens[0]}")
        body.append(line)
    if current_name is not None:
        raise StructuralError(f"missing .ends {current_name}")
    if not blocks:
        raise StructuralError("candidate requires at least one .subckt")
    return blocks


def validate_candidate_text(text: str, spec: CircuitSpec) -> tuple[str, dict[str, Any]]:
    blocks = _subcircuits(_logical_lines(text))
    top = blocks.get(spec.top_subcircuit.lower())
    if top is None:
        raise StructuralError(f"candidate requires .subckt {spec.top_subcircuit}")
    if tuple(token.upper() for token in top[0]) != tuple(token.upper() for token in spec.ports):
        expected = " ".join((spec.top_subcircuit, *spec.ports))
        raise StructuralError(f"top subcircuit must be exactly: .subckt {expected}")

    allowed = {model.lower(): model for model in spec.allowed_models}
    helpers = {name: ports for name, (ports, _) in blocks.items()}
    expanded: list[str] = []
    model_counts: dict[str, int] = {}
    element_count = 0

    for block_name, (ports, body) in blocks.items():
        expanded.append(f".subckt {block_name.upper()} {' '.join(ports)}")
        names: set[str] = set()
        for line in body:
            tokens = line.split()
            name = tokens[0]
            lowered_name = name.lower()
            if lowered_name in names:
                raise StructuralError(f"duplicate element in {block_name}: {name}")
            names.add(lowered_name)
            kind = name[0].lower()
            if kind == "x":
                parameter_start = next(
                    (index for index, token in enumerate(tokens[1:], start=1) if "=" in token),
                    len(tokens),
                )
                model_index = parameter_start - 1
                if model_index < 2:
                    raise StructuralError(f"{name}: incomplete X instance")
                if (
                    tokens[model_index].lower() not in allowed
                    and tokens[model_index].lower() not in helpers
                ):
                    raise StructuralError(f"{name}: unsupported device or helper model")
                nodes = tokens[1:model_index]
                model_token = tokens[model_index]
                model_key = model_token.lower()
                params = _params(tokens[parameter_start:])
                if model_key in helpers and model_key not in allowed:
                    if len(nodes) != len(helpers[model_key]) or params:
                        raise StructuralError(
                            f"{name}: helper {model_token} requires {len(helpers[model_key])} nodes and no parameters"
                        )
                    expanded.append(line)
                    element_count += 1
                    continue
                canonical_model = allowed[model_key]
                if canonical_model in MOS_1V8 or canonical_model in MOS_HV:
                    if len(nodes) != 4 or set(params) != {"w", "l", "m"}:
                        raise StructuralError(
                            f"{name}: MOS requires four nodes and W, L, M"
                        )
                    width = _finite_literal(params["w"], "W")
                    length = _finite_literal(params["l"], "L")
                    multiplicity_value = _finite_literal(params["m"], "M")
                    multiplicity = int(multiplicity_value)
                    minimum_length = 0.50 if canonical_model in MOS_HV else 0.15
                    if not 0.42 <= width <= 10.0:
                        raise StructuralError(f"{name}: W must be in 0.42..10.0 um")
                    if not minimum_length <= length <= 5.0:
                        raise StructuralError(
                            f"{name}: L must be in {minimum_length:.2f}..5.0 um"
                        )
                    if multiplicity_value != multiplicity or not 1 <= multiplicity <= 500:
                        raise StructuralError(f"{name}: M must be an integer in 1..500")
                    for index in range(1, multiplicity + 1):
                        expanded.append(
                            f"{name}__{index:03d} {' '.join(nodes)} {canonical_model} "
                            f"W={width:g} L={length:g} nf=1 mult=1"
                        )
                    model_counts[canonical_model] = model_counts.get(canonical_model, 0) + multiplicity
                    element_count += multiplicity
                    continue
                if canonical_model in BJT:
                    if len(nodes) != 3 or set(params) not in (set(), {"m"}):
                        raise StructuralError(f"{name}: BJT requires three nodes and optional M")
                    multiplicity_value = _finite_literal(params.get("m", "1"), "M")
                    multiplicity = int(multiplicity_value)
                    if multiplicity_value != multiplicity or not 1 <= multiplicity <= 500:
                        raise StructuralError(f"{name}: M must be an integer in 1..500")
                    for index in range(1, multiplicity + 1):
                        expanded.append(f"{name}__{index:03d} {' '.join(nodes)} {canonical_model}")
                    model_counts[canonical_model] = model_counts.get(canonical_model, 0) + multiplicity
                    element_count += multiplicity
                    continue
                raise StructuralError(f"{name}: unsupported model {model_token}")
            if kind in {"r", "c"}:
                if len(tokens) != 4:
                    raise StructuralError(f"{name}: passive requires two nodes and one value")
                value = parse_spice_number(tokens[3])
                lower, upper = ((1.0, 100e6) if kind == "r" else (1e-15, 10e-6))
                if not lower <= value <= upper:
                    raise StructuralError(f"{name}: passive value outside generic contract")
                expanded.append(line)
                element_count += 1
                continue
            raise StructuralError(f"{name}: forbidden element; only X, R, and C are allowed")
        expanded.append(f".ends {block_name.upper()}")
    if element_count == 0:
        raise StructuralError("candidate must contain at least one device")
    for group in spec.required_model_groups:
        if not any(model_counts.get(model, 0) > 0 for model in group):
            raise StructuralError(
                "candidate requires at least one model from: " + ", ".join(sorted(group))
            )
    structure = {
        "contract_version": CONTRACT_VERSION,
        "top_subcircuit": spec.top_subcircuit,
        "ports": list(spec.ports),
        "subcircuits": sorted(blocks),
        "models": model_counts,
        "expanded_element_count": element_count,
    }
    return "\n".join(expanded) + "\n", structure


@dataclass(frozen=True)
class GenericCandidate:
    source_dir: Path
    dut_text: str
    candidate: Mapping[str, Any]
    expanded_dut: str
    structure: Mapping[str, Any]
    candidate_key: str
    ibias_ua: None = None

    @classmethod
    def load(cls, source_dir: Path, spec: CircuitSpec) -> "GenericCandidate":
        source = source_dir.resolve()
        dut_path = source / "dut.spice"
        params_path = source / "candidate.json"
        if not dut_path.is_file() or not params_path.is_file():
            raise ValueError("candidate_dir requires dut.spice and candidate.json")
        dut_text = dut_path.read_text(encoding="utf-8")
        candidate = json.loads(params_path.read_text(encoding="utf-8"))
        if not isinstance(candidate, Mapping) or candidate:
            raise StructuralError("generic candidate.json must be an empty object")
        expanded, structure = validate_candidate_text(dut_text, spec)
        fixture_sha256 = hashlib.sha256(spec.fixture_path.read_bytes()).hexdigest()
        payload = {
            "contract": CONTRACT_VERSION,
            "circuit": spec.id,
            "fixture_sha256": fixture_sha256,
            "dut": expanded,
        }
        key = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        structure = {**structure, "fixture_sha256": fixture_sha256}
        return cls(source, dut_text, candidate, expanded, structure, key)
