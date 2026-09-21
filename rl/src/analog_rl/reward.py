"""Stable reward shaping for Analog Arena simulation results.

The simulator emits metrics in engineering units (dB, MHz, degrees, mW and
microseconds).  This module turns those values into bounded, finite scores while
keeping validity and hard pass/fail checks separate.  Invalid or missing values
always contribute zero; a non-finite value can never accidentally receive a high
reward.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class MetricSpec:
    """A threshold and direction for one measured metric."""

    target: float
    direction: str = "min"  # ``min`` means larger is better; ``max`` the reverse
    weight: float = 1.0
    scale: float | None = None

    def __post_init__(self) -> None:
        if self.direction not in {"min", "max"}:
            raise ValueError("direction must be 'min' or 'max'")
        if not math.isfinite(float(self.target)):
            raise ValueError("target must be finite")
        if not math.isfinite(float(self.weight)) or self.weight < 0:
            raise ValueError("weight must be a finite non-negative number")
        if self.scale is not None and (not math.isfinite(float(self.scale)) or self.scale <= 0):
            raise ValueError("scale must be positive and finite")


# The threshold contract used by the amplifier evaluator.  Task 1 is the
# stricter target for gain, GBW and slew rate; the remaining gates are shared.
COMMON_METRICS: dict[str, MetricSpec] = {
    "pm_deg": MetricSpec(45.0, "min"),
    "power_mw": MetricSpec(0.5, "max"),
    "area_score": MetricSpec(150.0, "max"),
    "rise_settling_time_us": MetricSpec(1.0, "max"),
    "fall_settling_time_us": MetricSpec(1.0, "max"),
    "cmrr_db": MetricSpec(80.0, "min"),
    "psrr_plus_db": MetricSpec(80.0, "min"),
    "psrr_minus_db": MetricSpec(80.0, "min"),
}
TASK_METRICS: dict[str, dict[str, MetricSpec]] = {
    "task1": {"gain_db": MetricSpec(120.0), "gbw_mhz": MetricSpec(2.0),
               "sr_min_v_per_us": MetricSpec(0.6), **COMMON_METRICS},
    "task2": {"gain_db": MetricSpec(100.0), "gbw_mhz": MetricSpec(1.0),
               "sr_min_v_per_us": MetricSpec(0.5), **COMMON_METRICS},
}
# Public alias useful to callers that do not need task-specific copies.
DEFAULT_METRICS = TASK_METRICS


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def metric_valid(value: Any, detail: Any = None) -> bool:
    """Return whether a measurement is safe to score.

    A validity marker is deliberately strict: ``{"valid": 1}`` and
    ``{"valid": "true"}`` are not accepted as simulator evidence.
    """

    return _finite(value) and isinstance(detail, Mapping) and detail.get("valid") is True


def normalize_metric(value: Any, target: float, direction: str = "min", scale: float | None = None) -> float:
    """Map a finite metric to a bounded ``[0, 1]`` utility.

    The threshold maps to 0.5 and the score changes smoothly on either side of
    it.  ``tanh`` avoids overflow for extreme ngspice values and makes rewards
    comparable across units.  Invalid values return zero.
    """

    if not _finite(value) or not _finite(target) or direction not in {"min", "max"}:
        return 0.0
    target = float(target)
    # A scale relative to the threshold gives sensible gradients for dB, MHz,
    # mW and timing values alike.  Keep a non-zero floor for a zero target.
    width = float(scale) if scale is not None else max(abs(target) * 0.25, 1e-9)
    if not math.isfinite(width) or width <= 0:
        return 0.0
    margin = (float(value) - target) / width
    if direction == "max":
        margin = -margin
    # tanh itself is safe, but clamping also guards alternate math backends.
    margin = max(-40.0, min(40.0, margin))
    score = 0.5 + 0.5 * math.tanh(margin)
    return max(0.0, min(1.0, float(score)))


def _specs(task: str, specs: Mapping[str, MetricSpec] | None) -> Mapping[str, MetricSpec]:
    if specs is not None:
        return specs
    if task not in TASK_METRICS:
        raise ValueError(f"unknown task: {task!r}")
    return TASK_METRICS[task]


def score_metrics(
    metrics: Mapping[str, Any] | None,
    metric_validity: Mapping[str, Any] | None,
    *,
    task: str = "task2",
    status: str | None = "VALID",
    specs: Mapping[str, MetricSpec] | None = None,
) -> dict[str, Any]:
    """Score a result's metrics and return auditable per-metric details.

    ``reward`` is finite and bounded by 3: a normalized utility term, a
    pass-fraction term and a one-point all-gates bonus.  ``legacy_reward`` is
    also returned for compatibility with the original ``pass + valid/11 +
    passed/11`` pipeline.
    """

    values = metrics if isinstance(metrics, Mapping) else {}
    validity = metric_validity if isinstance(metric_validity, Mapping) else {}
    selected = _specs(task, specs)
    details: dict[str, dict[str, Any]] = {}
    valid_count = passed_count = 0
    weighted_sum = weight_sum = 0.0
    for name, spec in selected.items():
        value = values.get(name)
        valid = metric_valid(value, validity.get(name))
        if valid and name in {"gbw_mhz", "power_mw", "sr_min_v_per_us"}:
            valid = float(value) > 0.0
        if valid and name in {"area_score", "rise_settling_time_us", "fall_settling_time_us"}:
            valid = float(value) >= 0.0
        if valid and name == "pm_deg":
            valid = 0.0 < float(value) <= 180.0
        normalized = normalize_metric(value, spec.target, spec.direction, spec.scale) if valid else 0.0
        passed = bool(valid and ((float(value) >= spec.target) if spec.direction == "min" else (float(value) <= spec.target)))
        if valid:
            valid_count += 1
        if passed:
            passed_count += 1
        weight = float(spec.weight)
        weighted_sum += normalized * weight
        weight_sum += weight
        details[name] = {"value": float(value) if valid else None, "valid": valid,
                         "passed": passed, "normalized": normalized,
                         "target": spec.target, "direction": spec.direction}
    total = len(selected)
    status_valid = status == "VALID"
    utility = weighted_sum / weight_sum if weight_sum > 0 else 0.0
    valid_fraction = valid_count / total if total else 0.0
    passed_fraction = passed_count / total if total else 0.0
    all_passed = bool(status_valid and total and passed_count == total)
    reward = utility + passed_fraction + (1.0 if all_passed else 0.0)
    legacy = (1.0 if all_passed else 0.0) + valid_fraction + passed_fraction
    if not status_valid:
        reward = legacy = 0.0
    # Explicit finite guard: callers can serialize this without allow_nan.
    reward = float(reward) if math.isfinite(reward) else 0.0
    legacy = float(legacy) if math.isfinite(legacy) else 0.0
    return {"reward": reward, "legacy_reward": legacy, "passed": all_passed,
            "status_valid": status_valid, "valid_metrics": valid_count,
            "passed_metrics": passed_count, "total_metrics": total,
            "valid_fraction": valid_fraction, "passed_fraction": passed_fraction,
            "normalized_metrics": {k: v["normalized"] for k, v in details.items()},
            "metrics": details}


def reward_from_result(result: Mapping[str, Any], *, task: str | None = None,
                       specs: Mapping[str, MetricSpec] | None = None) -> dict[str, Any]:
    """Score a simulator result object directly."""

    if not isinstance(result, Mapping):
        raise TypeError("result must be a mapping")
    selected_task = task or str(result.get("task", "task2"))
    return score_metrics(result.get("metrics"), result.get("metric_validity"),
                         task=selected_task, status=result.get("status"), specs=specs)


# Friendly aliases used by rollout code and external users.
compute_reward = reward_from_result
calculate_reward = score_metrics

__all__ = ["MetricSpec", "COMMON_METRICS", "TASK_METRICS", "DEFAULT_METRICS",
           "metric_valid", "normalize_metric", "score_metrics", "reward_from_result",
           "compute_reward", "calculate_reward"]
