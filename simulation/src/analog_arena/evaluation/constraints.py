"""Finite-value constraints, independent of simulator and agent state."""
from __future__ import annotations
import math
from typing import Any, Mapping


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def validate_constraints(constraints: Mapping) -> dict:
    if not isinstance(constraints, Mapping):
        raise ValueError("constraints must be a mapping")
    result = {}
    for name, rule in constraints.items():
        if not isinstance(rule, Mapping) or not rule or not set(rule) <= {"min", "max"}:
            raise ValueError(f"constraint {name} requires min and/or max")
        if any(finite_number(v) is None for v in rule.values()):
            raise ValueError(f"constraint {name} requires finite numeric limits")
        if "min" in rule and "max" in rule and rule["min"] > rule["max"]:
            raise ValueError(f"constraint {name} has min > max")
        result[name] = dict(rule)
    return result


def score_metrics(metrics: Mapping[str, Any], constraints: Mapping) -> dict:
    constraints = validate_constraints(constraints)
    checks, margins = {}, {}
    for name, rule in constraints.items():
        value = finite_number(metrics.get(name))
        distances = [] if value is None else [value - rule["min"]] if "min" in rule else []
        if value is not None and "max" in rule:
            distances.append(rule["max"] - value)
        margin = min(distances) if distances else None
        checks[name] = margin is not None and margin >= 0
        margins[name] = margin
    return {"constraint_checks": checks, "constraint_margins": margins, "feasible": all(checks.values())}


def score_result(result: Mapping, constraints: Mapping) -> dict:
    """Both bounds must pass at every corner and on valid measurements."""
    constraints = validate_constraints(constraints)
    corners = result.get("corners")
    rows = list(corners.values()) if isinstance(corners, Mapping) and corners else [result.get("metrics", result)]
    reports = [score_metrics(row, constraints) for row in rows]
    validity = result.get("metric_validity") or {}
    if not isinstance(validity, Mapping):
        validity = {}
    requires_validity = any(key in result for key in ("metrics", "corners", "profile", "metric_validity"))
    if not validity and ("valid_first_0db_crossing" in result or "transient_valid" in result):
        requires_validity = True
        from .amplifier.profile import _row_metric_validity
        is_ac = "valid_first_0db_crossing" in result
        row = result if is_ac else {**result, "transient": result}
        validity = _row_metric_validity(row, {"ac"} if is_ac else {"transient"})
    checks, margins = {}, {}
    for name in constraints:
        detail = validity.get(name)
        valid = (isinstance(detail, Mapping) and detail.get("valid") is True) if requires_validity else True
        checks[name] = valid and all(row["constraint_checks"][name] for row in reports)
        values = [row["constraint_margins"][name] for row in reports]
        margins[name] = min(values) if all(v is not None for v in values) else None
    is_evaluation = "profile" in result or "corners" in result
    execution_ok = result.get("status", None if is_evaluation else "VALID") == "VALID"
    return {"constraint_checks": checks, "constraint_margins": margins,
            "feasible": execution_ok and all(checks.values())}
