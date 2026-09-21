from __future__ import annotations
import math
import re
from pathlib import Path
from typing import Any, Mapping
from analog_arena.simulation.circuits.specs import CircuitSpec
MEASURE_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)(?:\s|$)",
    re.MULTILINE,
)
def _parse_measures(log: str) -> dict[str, float]:
    return {
        key: float(value)
        for key, value in MEASURE_RE.findall(log)
        if key.lower() not in {"stack"}
    }


def _derive_metrics(spec: CircuitSpec, measures: Mapping[str, float]) -> dict[str, float]:
    metrics = dict(measures)
    if spec.id == "dac3":
        codes = [measures.get(f"code{index}_v") for index in range(8)]
        if all(value is not None and math.isfinite(value) for value in codes):
            values = [float(value) for value in codes if value is not None]
            span = values[-1] - values[0]
            ideal_step = span / 7.0
            metrics["output_span_v"] = span
            metrics["min_code_step_v"] = min(
                right - left for left, right in zip(values, values[1:])
            )
            if ideal_step > 0:
                metrics["max_dnl_lsb"] = max(
                    abs((right - left) / ideal_step - 1.0)
                    for left, right in zip(values, values[1:])
                )
                metrics["max_inl_lsb"] = max(
                    abs((value - values[0]) / ideal_step - index)
                    for index, value in enumerate(values)
                )
    elif spec.id == "logic-combinational-3stage":
        low = measures.get("output_for_input_high_v")
        high = measures.get("output_for_input_low_v")
        if low is not None and high is not None:
            metrics["logic_pass_count"] = float((low <= 0.36) + (high >= 1.44))
    elif spec.id == "bandgap-reference":
        nominal = measures.get("vref_25c_v")
        span = measures.get("temp_span_mv")
        if nominal is not None and nominal > 0 and span is not None:
            metrics["tempco_ppm_per_c"] = span * 1000.0 / (nominal * 165.0)
    return metrics


def _parse_wave(path: Path) -> list[tuple[float, float]]:
    samples: list[tuple[float, float]] = []
    if not path.is_file():
        return samples
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            values = [float(token) for token in raw.split()]
        except ValueError:
            continue
        if len(values) >= 2 and all(math.isfinite(value) for value in values):
            samples.append((values[0], values[-1]))
    return samples


def _derive_dac_wave_metrics(
    samples: list[tuple[float, float]], measures: Mapping[str, float]
) -> dict[str, float]:
    """Worst sampled settling across 0->1 through 6->7, within 0.5 endpoint LSB.

    Observe until 990 ns after each nominal code update (before the next
    transition starts). Require coverage and continued residence in the band;
    a single threshold crossing is insufficient.
    """
    codes = [measures.get(f"code{index}_v") for index in range(8)]
    if not all(value is not None and math.isfinite(value) for value in codes):
        return {}
    values = [float(value) for value in codes if value is not None]
    tolerance = (values[7] - values[0]) / 14.0
    if tolerance <= 0 or not samples:
        return {}
    if any(not math.isfinite(t) or not math.isfinite(v) for t, v in samples):
        return {}
    if any(right[0] <= left[0] for left, right in zip(samples, samples[1:])):
        return {}
    delays = []
    for code in range(1, 8):
        start = code * 1e-6
        stop = start + 0.99e-6
        if samples[0][0] > start or samples[-1][0] < stop:
            return {}
        window = [(t, v) for t, v in samples if start <= t <= stop]
        # The fixture's maximum time step is 0.5 ns. Reject incomplete windows.
        if not window or window[0][0] > start + 0.51e-9 or window[-1][0] < stop - 0.51e-9:
            return {}
        if any(right[0] - left[0] > 0.51e-9 for left, right in zip(window, window[1:])):
            return {}
        last_outside = next(
            (i for i in range(len(window) - 1, -1, -1)
             if abs(window[i][1] - values[code]) > tolerance), None
        )
        if last_outside is None:
            delays.append(max(0.0, window[0][0] - start))
        elif last_outside + 1 < len(window):
            delays.append(window[last_outside + 1][0] - start)
        else:
            return {}
    return {"settling_time_ns": max(delays) * 1e9}


def _derive_ldo_wave_metrics(samples: list[tuple[float, float]]) -> dict[str, float]:
    def values_between(start: float, stop: float) -> list[float]:
        return [value for when, value in samples if start <= when <= stop]

    light_values = values_between(2e-6, 3.5e-6)
    heavy_values = values_between(6e-6, 6.8e-6)
    step_samples = [(when, value) for when, value in samples if 4e-6 <= when <= 6.8e-6]
    if not light_values or not heavy_values or not step_samples:
        return {}
    light = sum(light_values) / len(light_values)
    heavy = sum(heavy_values) / len(heavy_values)
    droop = max(0.0, light - min(value for _, value in step_samples)) * 1e3
    tolerance = max(abs(heavy) * 0.01, 0.005)
    last_outside = next(
        (
            index
            for index in range(len(step_samples) - 1, -1, -1)
            if abs(step_samples[index][1] - heavy) > tolerance
        ),
        None,
    )
    if last_outside is None:
        settled_at = 4e-6
    elif last_outside + 1 < len(step_samples):
        settled_at = step_samples[last_outside + 1][0]
    else:
        return {
            "vout_light_v": light,
            "vout_heavy_v": heavy,
            "droop_mv": droop,
        }
    return {
        "vout_light_v": light,
        "vout_heavy_v": heavy,
        "droop_mv": droop,
        "settling_time_us": max(0.0, settled_at - 4e-6) * 1e6,
    }


def _aggregate(
    spec: CircuitSpec, corner_metrics: Mapping[str, Mapping[str, float]]
) -> tuple[dict[str, float], dict[str, str]]:
    metrics: dict[str, float] = {}
    sources: dict[str, str] = {}
    for name, definition in spec.metrics.items():
        values = [
            (corner, float(row[name]))
            for corner, row in corner_metrics.items()
            if name in row and math.isfinite(float(row[name]))
        ]
        if not values:
            continue
        selected = max(values, key=lambda item: item[1]) if definition.worst == "max" else min(values, key=lambda item: item[1])
        sources[name], metrics[name] = selected
    return metrics, sources

def _score_corner_constraints(
    corner_metrics: Mapping[str, Mapping[str, float]],
    constraints: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    margins: dict[str, float | None] = {}
    for name, rule in constraints.items():
        values = [
            float(row[name])
            for row in corner_metrics.values()
            if name in row and math.isfinite(float(row[name]))
        ]
        complete = len(values) == len(corner_metrics)
        metric_margins: list[float] = []
        if complete and "min" in rule:
            limit = float(rule["min"])
            metric_margins.extend(value - limit for value in values)
        if complete and "max" in rule:
            limit = float(rule["max"])
            metric_margins.extend(limit - value for value in values)
        margin = min(metric_margins) if metric_margins else None
        checks[name] = bool(complete and margin is not None and margin >= 0.0)
        margins[name] = margin
    return {
        "constraint_checks": checks,
        "constraint_margins": margins,
        "feasible": all(checks.values()) if checks else True,
    }
