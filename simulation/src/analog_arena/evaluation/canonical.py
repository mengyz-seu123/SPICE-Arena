from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


AREA_CAPACITOR_PF_WEIGHT = 1089.0
AREA_RESISTOR_KOHM_WEIGHT = 5.0
DEFAULT_VSS_V = 0.0
DEFAULT_VDD_V = 1.8
DEFAULT_OUTPUT_TARGET_V = 0.45
DEFAULT_OUTPUT_TOLERANCE_V = 0.02
DEFAULT_RAIL_TOLERANCE_V = 1e-3


def area_score_metrics(
    *,
    mos_area_term: float,
    capacitor_pf: float,
    resistor_kohm: float = 0.0,
) -> dict[str, float]:
    """Return the one canonical AnalogGym-style area decomposition and score."""
    values = (float(mos_area_term), float(capacitor_pf), float(resistor_kohm))
    if not all(math.isfinite(value) and value >= 0.0 for value in values):
        raise ValueError("area terms must be finite and non-negative")
    capacitor_area_term = AREA_CAPACITOR_PF_WEIGHT * values[1]
    resistor_area_term = AREA_RESISTOR_KOHM_WEIGHT * values[2]
    score_squared = values[0] + capacitor_area_term + resistor_area_term
    return {
        "mos_area_term": values[0],
        "capacitor_pf": values[1],
        "capacitor_area_term": capacitor_area_term,
        "resistor_kohm": values[2],
        "resistor_area_term": resistor_area_term,
        "area_score_squared": score_squared,
        "analoggym_area_score": math.sqrt(score_squared),
    }


def operating_point_metrics(
    node_voltages: Mapping[str, float],
    *,
    output_node: str,
    required_nodes: Sequence[str] | None = None,
    output_target_v: float = DEFAULT_OUTPUT_TARGET_V,
    output_tolerance_v: float = DEFAULT_OUTPUT_TOLERANCE_V,
    vss_v: float = DEFAULT_VSS_V,
    vdd_v: float = DEFAULT_VDD_V,
    rail_tolerance_v: float = DEFAULT_RAIL_TOLERANCE_V,
) -> dict:
    required = tuple(required_nodes or node_voltages.keys())
    missing = sorted(name for name in required if name not in node_voltages)
    present = {name: float(node_voltages[name]) for name in required if name in node_voltages}
    finite = not missing and all(math.isfinite(value) for value in present.values())
    within_rails = finite and all(
        vss_v - rail_tolerance_v <= value <= vdd_v + rail_tolerance_v
        for value in present.values()
    )
    output = present.get(output_node)
    output_centered = (
        output is not None
        and math.isfinite(output)
        and abs(output - output_target_v) <= output_tolerance_v
    )
    finite_values = [value for value in present.values() if math.isfinite(value)]
    finite_output = output is not None and math.isfinite(output)
    return {
        "node_voltages_v": present,
        "missing_nodes": missing,
        "minimum_node_v": min(finite_values) if finite_values else None,
        "maximum_node_v": max(finite_values) if finite_values else None,
        "output_target_v": output_target_v,
        "output_error_v": output - output_target_v if finite_output else None,
        "gates": {
            "op_finite": finite,
            "op_output_centered": output_centered,
            "op_nodes_within_rails": within_rails,
        },
    }
