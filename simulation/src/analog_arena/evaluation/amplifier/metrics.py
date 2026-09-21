"""Pure amplifier measurements; signed-PM contract, no simulator launch."""
from __future__ import annotations
import math
from statistics import median
from typing import Any, Iterable, Sequence
from analog_arena.evaluation.canonical import area_score_metrics, operating_point_metrics
from analog_arena.simulation.amplifier.testbench import (
    AC_INPUT_AMPLITUDE_V, NOMINAL_VDD_V, SUPPLY_VSS_V,
    TRANSIENT_INITIAL_TOLERANCE_FRACTION, TRANSIENT_INPUT_HIGH_V,
    TRANSIENT_INPUT_LOW_V, TRANSIENT_OVERSHOOT_TOLERANCE_V,
    TRANSIENT_RAIL_TOLERANCE_V, TRANSIENT_SETTLING_TOLERANCE_FRACTION,
)
CONTRACT_VERSION = "unified-nominal-ota-v4-signed-pm"
def calculate_area_metrics(summary: dict[str, Any]) -> dict[str, float]:
    mos = sum(item["w_um"] * item["l_um"] * item["m"] for item in summary["mos_groups"])
    return area_score_metrics(
        mos_area_term=mos,
        capacitor_pf=sum(summary["capacitors_pf"]),
        resistor_kohm=sum(summary["resistors_kohm"]),
    )

def calculate_area_score(summary: dict[str, Any]) -> float:
    return calculate_area_metrics(summary)["analoggym_area_score"]

def _unwrap(phases: Iterable[float]) -> list[float]:
    result: list[float] = []
    for phase in phases:
        if not math.isfinite(phase):
            raise ValueError("phase must be finite")
        value = phase
        if result:
            while value - result[-1] > 180.0:
                value -= 360.0
            while value - result[-1] < -180.0:
                value += 360.0
        result.append(value)
    return result

def evaluate_operating_point(node_voltages: dict[str, float]) -> dict[str, Any]:
    return operating_point_metrics(
        node_voltages,
        output_node="a_out",
        required_nodes=tuple(node_voltages),
    )

def _crossings(values: Sequence[float], start: int, end: int, threshold: float) -> tuple[list[int], list[int]]:
    rises, falls = [], []
    for i in range(start, min(end, len(values) - 1)):
        if values[i] < threshold <= values[i + 1]:
            rises.append(i + 1)
        if values[i] > threshold >= values[i + 1]:
            falls.append(i + 1)
    return rises, falls

def _local_slope(times: Sequence[float], values: Sequence[float], index: int, radius: int = 3) -> float:
    lo, hi = max(0, index - radius), min(len(times), index + radius + 1)
    xs, ys = times[lo:hi], values[lo:hi]
    xm, ym = sum(xs) / len(xs), sum(ys) / len(ys)
    denominator = sum((x - xm) ** 2 for x in xs)
    if denominator <= 0:
        return math.nan
    return sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / denominator / 1e6

def _crossing_time(times: Sequence[float], values: Sequence[float], start: int, end: int,
                   threshold: float, rising: bool) -> float | None:
    for i in range(start, min(end, len(values) - 1)):
        hit = values[i] < threshold <= values[i + 1] if rising else values[i] > threshold >= values[i + 1]
        if hit and values[i + 1] != values[i]:
            fraction = (threshold - values[i]) / (values[i + 1] - values[i])
            return times[i] + fraction * (times[i + 1] - times[i])
    return None

def calculate_foms(gbw_mhz: float, power_mw: float, cl_pf: float = 100.0) -> float:
    return gbw_mhz * cl_pf / power_mw

def calculate_foml(sr_v_per_us: float, power_mw: float, cl_pf: float = 100.0) -> float:
    return sr_v_per_us * cl_pf / power_mw
def extract_ac_metrics(
    rows: Sequence[Sequence[float]],
    *,
    input_amplitude_v: float = AC_INPUT_AMPLITUDE_V,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "gain_db": None,
        "gbw_mhz": None,
        "pm_deg": None,
        "valid_first_0db_crossing": False,
        "downward_crossing_count": 0,
    }
    amplitude = float(input_amplitude_v)
    if (
        len(rows) < 2
        or any(len(row) < 3 for row in rows)
        or not math.isfinite(amplitude)
        or amplitude <= 0
    ):
        return result
    freqs = [float(row[0]) for row in rows]
    input_db = 20.0 * math.log10(amplitude)
    gains = [float(row[1]) - input_db for row in rows]
    raw_phases = [float(row[2]) for row in rows]
    if not all(math.isfinite(v) for v in freqs + gains + raw_phases):
        return result
    if any(f <= 0 for f in freqs) or any(a >= b for a, b in zip(freqs, freqs[1:])):
        return result
    phases = _unwrap(raw_phases)
    result["gain_db"] = gains[0]
    crossings = [i for i in range(len(rows) - 1) if gains[i] >= 0.0 and gains[i + 1] < 0.0]
    result["downward_crossing_count"] = len(crossings)
    if not crossings:
        return result
    i = crossings[0]
    if freqs[i] <= 0 or freqs[i + 1] <= 0 or gains[i] == gains[i + 1]:
        return result
    fraction = -gains[i] / (gains[i + 1] - gains[i])
    log_frequency = math.log10(freqs[i]) + fraction * (
        math.log10(freqs[i + 1]) - math.log10(freqs[i])
    )
    phase = phases[i] + fraction * (phases[i + 1] - phases[i])
    gbw_mhz = 10 ** log_frequency / 1e6
    pm_deg = phase
    result.update(
        {
            "gbw_mhz": gbw_mhz,
            "pm_deg": pm_deg,
            "phase_at_crossing_deg": phase,
            "valid_first_0db_crossing": math.isfinite(gbw_mhz)
            and gbw_mhz > 0
            and 0 < pm_deg <= 180,
        }
    )
    return result

def _settling_analoggym(
    times: Sequence[float],
    values: Sequence[float],
    start: int,
    end: int,
    target: float,
    tolerance: float,
) -> tuple[bool, float | None]:
    if end <= start + 1:
        return False, None
    for index in range(start, end):
        if all(abs(values[j] - target) <= tolerance for j in range(index, end)):
            return True, (times[index] - times[start]) * 1e6
    return False, None

def _tracking_summary(
    values: Sequence[float],
    start: int,
    end: int,
    *,
    target_v: float,
    tolerance_v: float,
) -> dict[str, float | bool]:
    window = values[start:end]
    output_v = float(median(window))
    errors = [float(value) - target_v for value in window]
    worst_error_v = max(errors, key=abs)
    return {
        "output_v": output_v,
        "target_v": target_v,
        "error_v": output_v - target_v,
        "tolerance_v": tolerance_v,
        "min_error_v": min(errors),
        "max_error_v": max(errors),
        "worst_error_v": worst_error_v,
        "max_abs_error_v": abs(worst_error_v),
        "all_within_tolerance": all(abs(error) <= tolerance_v for error in errors),
    }

def extract_transient_metrics(
    rows: Sequence[Sequence[float]],
    *,
    low_v: float = TRANSIENT_INPUT_LOW_V,
    high_v: float = TRANSIENT_INPUT_HIGH_V,
    vss_v: float = SUPPLY_VSS_V,
    vdd_v: float = NOMINAL_VDD_V,
    rail_tolerance_v: float = TRANSIENT_RAIL_TOLERANCE_V,
    initial_tolerance_fraction: float = TRANSIENT_INITIAL_TOLERANCE_FRACTION,
    settling_tolerance_fraction: float = TRANSIENT_SETTLING_TOLERANCE_FRACTION,
    overshoot_tolerance_v: float = TRANSIENT_OVERSHOOT_TOLERANCE_V,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "transient_valid": False,
        "slew_measurable": False,
        "sr_rise_v_per_us": None,
        "sr_fall_v_per_us": None,
        "sr_min_v_per_us": None,
        "sr_10_90_rise_v_per_us": None,
        "sr_10_90_fall_v_per_us": None,
        "rise_settling_time_us": None,
        "fall_settling_time_us": None,
        "initial_settled": False,
        "initial_tracking": None,
        "rise_plateau_tracking": None,
        "fall_plateau_tracking": None,
        "output_min_v": None,
        "output_max_v": None,
        "rail_valid": False,
        "overshoot_valid": None,
        "oscillation_free": None,
        "reasons": [],
    }
    reasons: list[str] = result["reasons"]
    if len(rows) < 20 or any(len(row) < 3 for row in rows):
        reasons.append("insufficient_rows")
        return result
    times = [float(row[0]) for row in rows]
    outputs = [float(row[1]) for row in rows]
    inputs = [float(row[2]) for row in rows]
    if not all(math.isfinite(v) for v in times + outputs + inputs):
        reasons.append("non_finite_waveform")
        return result
    if any(times[i] >= times[i + 1] for i in range(len(times) - 1)):
        reasons.append("non_monotonic_time")
        return result
    result["output_min_v"] = min(outputs)
    result["output_max_v"] = max(outputs)

    midpoint = (low_v + high_v) / 2
    input_rises, input_falls = _crossings(inputs, 0, len(inputs) - 1, midpoint)
    if not input_rises or not input_falls:
        reasons.append("missing_input_edge")
        return result
    rise_start = input_rises[0]
    fall_candidates = [i for i in input_falls if i > rise_start]
    if not fall_candidates:
        reasons.append("missing_input_fall")
        return result
    fall_start = rise_end = fall_candidates[0]
    fall_end = len(rows)
    horizon_us = (times[-1] - times[0]) * 1e6

    pre_start = max(0, rise_start - max(4, rise_start // 10))
    rise_plateau_start = max(rise_start, rise_end - max(4, (rise_end - rise_start) // 10))
    fall_plateau_start = max(fall_start, fall_end - max(4, (fall_end - fall_start) // 10))
    initial_tolerance_v = abs(low_v) * initial_tolerance_fraction
    rise_tolerance_v = abs(high_v) * settling_tolerance_fraction
    fall_tolerance_v = abs(low_v) * settling_tolerance_fraction
    result["initial_tracking"] = _tracking_summary(
        outputs,
        pre_start,
        rise_start,
        target_v=low_v,
        tolerance_v=initial_tolerance_v,
    )
    result["rise_plateau_tracking"] = _tracking_summary(
        outputs,
        rise_plateau_start,
        rise_end,
        target_v=high_v,
        tolerance_v=rise_tolerance_v,
    )
    result["fall_plateau_tracking"] = _tracking_summary(
        outputs,
        fall_plateau_start,
        fall_end,
        target_v=low_v,
        tolerance_v=fall_tolerance_v,
    )
    initial_ok = all(
        abs(v - low_v) <= initial_tolerance_v
        for v in outputs[pre_start:rise_start]
    )
    result["initial_settled"] = initial_ok
    if not initial_ok:
        reasons.append("initial_not_settled")

    rise_cross, rise_wrong = _crossings(outputs, rise_start, rise_end, midpoint)
    fall_wrong, fall_cross = _crossings(outputs, fall_start, fall_end, midpoint)
    result["rise_crossing_count"] = len(rise_cross)
    result["fall_crossing_count"] = len(fall_cross)
    rise_slope = _local_slope(times, outputs, rise_cross[0]) if rise_cross else math.nan
    fall_slope = _local_slope(times, outputs, fall_cross[0]) if fall_cross else math.nan
    directional_slew_valid = bool(
        math.isfinite(rise_slope)
        and rise_slope > 0
        and math.isfinite(fall_slope)
        and fall_slope < 0
    )
    if directional_slew_valid:
        result["slew_measurable"] = True
        result["sr_rise_v_per_us"] = rise_slope
        result["sr_fall_v_per_us"] = -fall_slope
        result["sr_min_v_per_us"] = min(rise_slope, -fall_slope)
    else:
        reasons.append("wrong_slew_direction")

    rise_settled, rise_time = _settling_analoggym(
        times,
        outputs,
        rise_start,
        rise_end,
        high_v,
        abs(high_v) * settling_tolerance_fraction,
    )
    fall_settled, fall_time = _settling_analoggym(
        times,
        outputs,
        fall_start,
        fall_end,
        low_v,
        abs(low_v) * settling_tolerance_fraction,
    )
    result["rise_settling_time_us"] = rise_time if rise_settled else horizon_us
    result["fall_settling_time_us"] = fall_time if fall_settled else horizon_us

    rail_ok = (
        min(outputs) >= vss_v - rail_tolerance_v
        and max(outputs) <= vdd_v + rail_tolerance_v
    )
    result["rail_valid"] = rail_ok
    if not rail_ok:
        reasons.append("rail_violation")
    overshoot_ok = (
        max(outputs[rise_start:rise_end]) <= high_v + overshoot_tolerance_v
        and min(outputs[fall_start:fall_end]) >= low_v - overshoot_tolerance_v
    )
    result["overshoot_valid"] = overshoot_ok
    oscillation_free = (
        len(rise_cross) == 1
        and len(fall_cross) == 1
        and not rise_wrong
        and not fall_wrong
    )
    result["oscillation_free"] = oscillation_free

    low10 = low_v + 0.1 * (high_v - low_v)
    high90 = low_v + 0.9 * (high_v - low_v)
    r10 = _crossing_time(times, outputs, rise_start, rise_end, low10, True)
    r90 = _crossing_time(times, outputs, rise_start, rise_end, high90, True)
    f90 = _crossing_time(times, outputs, fall_start, fall_end, high90, False)
    f10 = _crossing_time(times, outputs, fall_start, fall_end, low10, False)
    if r10 is not None and r90 is not None and r90 > r10:
        result["sr_10_90_rise_v_per_us"] = (high90 - low10) / (r90 - r10) / 1e6
    if f90 is not None and f10 is not None and f10 > f90:
        result["sr_10_90_fall_v_per_us"] = (high90 - low10) / (f10 - f90) / 1e6

    if not reasons:
        result["transient_valid"] = True
    return result
