from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

from analog_arena.evaluation.constraints import score_result
from . import metrics as CORE
from .artifacts import parse_artifacts

from analog_arena.simulation.amplifier.runtime import (
    CORNERS,
    TESTBENCH_CONTRACT,
    CandidateInput,
    NgspiceAdapter,
)


METRIC_ANALYSIS = {
    "gain_db": "ac",
    "gbw_mhz": "ac",
    "pm_deg": "ac",
    "power_mw": "op",
    "area_score": "op",
    "sr_min_v_per_us": "transient",
    "rise_settling_time_us": "transient",
    "fall_settling_time_us": "transient",
    "settling_time_us": "transient",
    "foms": "ac",
    "foml": "transient",
    "cmrr_db": "rejection",
    "psrr_plus_db": "rejection",
    "psrr_minus_db": "rejection",
}

DEFAULT_WORST_MODE = {
    "gain_db": "min",
    "gbw_mhz": "min",
    "pm_deg": "min",
    "power_mw": "max",
    "area_score": "max",
    "sr_min_v_per_us": "min",
    "rise_settling_time_us": "max",
    "fall_settling_time_us": "max",
    "settling_time_us": "max",
    "foms": "min",
    "foml": "min",
    "cmrr_db": "min",
    "psrr_plus_db": "min",
    "psrr_minus_db": "min",
}


def software_contract() -> dict[str, Any]:
    testbench = json.loads(json.dumps(TESTBENCH_CONTRACT))
    testbench.pop("validity", None)
    return {
        "schema": "analog-arena/software-contract/v2",
        "profile": "sky130-ota",
        "evaluator_contract_version": CORE.CONTRACT_VERSION,
        "ports": ["VDD", "GND", "VINP", "VINN", "VOUT", "IBIAS"],
        "subcircuit": ".subckt OTA VDD GND VINP VINN VOUT IBIAS",
        "devices": {
            "mos": {
                "prefix": "X",
                "models": ["sky130_fd_pr__nfet_01v8", "sky130_fd_pr__pfet_01v8"],
                "syntax": "Xname D G S B model W=<um> L=<um> M=<integer>",
                "W_um": {"min": 0.42, "max": 10.0, "step": 0.01},
                "L_um": {"min": 0.15, "max": 5.0, "step": 0.01},
                "M": {"min": 1, "max": 500, "integer": True},
                "forbidden": ["mult="],
            },
            "resistor_kohm": {"min": 0.1, "max": 1000.0, "step": 0.01, "suffix": "k"},
            "capacitor_pf": {"min": 1.0, "max": 100.0, "step": 0.01, "suffix": "p"},
        },
        "ibias_uA": {"min": 1.0, "max": 40.0, "step": 0.1},
        "metrics": METRIC_ANALYSIS,
        "testbench": testbench,
        "result_semantics": {
            "valid": "all requested simulations completed without execution or parse errors",
            "feasible": "all constrained metrics are valid and satisfy the current task",
        },
        "metric_validity": {
            "gbw_mhz": "finite first downward 0 dB crossing",
            "pm_deg": (
                "signed unwrapped phase margin in (0, 180] degrees at a valid "
                "first downward 0 dB crossing"
            ),
            "sr_min_v_per_us": "finite in-rail rise and fall with correct directional slopes",
            "foms": "valid GBW and positive finite power",
            "foml": "valid directional slew and positive finite power",
        },
        "notes": [
            "These are evaluator and PDK rules, not an optimization recommendation.",
            "Analysis selection, corners and constraints come from the evaluation config.",
        ],
    }


def validate_metric_requests(task: Mapping[str, Any]) -> None:
    requested = set((task.get("constraints") or {})) | set((task.get("objectives") or {}))
    unknown = requested - set(METRIC_ANALYSIS)
    if unknown:
        raise ValueError(f"unsupported metrics: {sorted(unknown)}")
    analyses = set((task.get("evaluator") or {}).get("analyses") or [])
    missing = {metric: METRIC_ANALYSIS[metric] for metric in requested if METRIC_ANALYSIS[metric] not in analyses}
    if missing:
        raise ValueError(f"metrics require missing analyses: {missing}")
    if ({"foms", "foml"} & requested) and "op" not in analyses:
        raise ValueError("foms and foml also require the op analysis for power")


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


_TRANSIENT_EXECUTION_FAILURES = {
    "insufficient_rows",
    "non_finite_waveform",
    "non_monotonic_time",
    "missing_input_edge",
    "missing_input_fall",
}


def _transient_execution_valid(row: Mapping[str, Any]) -> bool:
    if row.get("transient_valid") is True:
        return True
    transient = row.get("transient")
    if not isinstance(transient, Mapping):
        return False
    reasons = transient.get("reasons")
    reasons = reasons if isinstance(reasons, list) else []
    return not any(str(reason) in _TRANSIENT_EXECUTION_FAILURES for reason in reasons)


def _validity_failures(
    row: Mapping[str, Any], analyses: set[str]
) -> list[str]:
    failed: list[str] = []
    if row.get("no_ngspice_errors") is not True:
        failed.append("no_ngspice_errors")
    if {"op", "ac"} & analyses:
        if row.get("op_finite") is not True:
            failed.append("op_finite")
    if "ac" in analyses and _finite(row.get("gain_db")) is None:
        failed.append("ac_data_finite")
    if "transient" in analyses and not _transient_execution_valid(row):
        failed.append("transient_data_valid")
    return failed


def _validity_report(row: Mapping[str, Any], analyses: set[str]) -> dict[str, Any]:
    checks: dict[str, bool] = {
        "no_ngspice_errors": row.get("no_ngspice_errors") is True,
    }
    if {"op", "ac"} & analyses:
        checks["op_finite"] = row.get("op_finite") is True
    if "ac" in analyses:
        checks["ac_data_finite"] = _finite(row.get("gain_db")) is not None
    if "transient" in analyses:
        checks["transient_data_valid"] = _transient_execution_valid(row)

    analysis_errors = row.get("analysis_errors")
    analysis_errors = analysis_errors if isinstance(analysis_errors, Mapping) else {}
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "analysis_errors": {str(name): str(error) for name, error in analysis_errors.items()},
    }


def _row_metrics(row: Mapping[str, Any], analyses: set[str]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for name in METRIC_ANALYSIS:
        analysis = METRIC_ANALYSIS[name]
        if analysis not in analyses:
            continue
        value = _finite(row.get(name))
        if value is not None:
            metrics[name] = value
    rise = _finite(row.get("rise_settling_time_us"))
    fall = _finite(row.get("fall_settling_time_us"))
    if rise is not None and fall is not None:
        metrics["settling_time_us"] = max(rise, fall)
    return metrics


def _row_metric_validity(
    row: Mapping[str, Any], analyses: set[str]
) -> dict[str, dict[str, Any]]:
    transient = row.get("transient")
    transient = transient if isinstance(transient, Mapping) else {}
    power = _finite(row.get("power_mw"))
    crossing_valid = row.get("valid_first_0db_crossing") is True
    slew_valid = (
        transient.get("slew_measurable") is True
        and transient.get("rail_valid") is True
    )
    reports: dict[str, dict[str, Any]] = {}
    for name, analysis in METRIC_ANALYSIS.items():
        if analysis not in analyses:
            continue
        value = _finite(row.get(name))
        reason: str | None = None
        valid = value is not None
        if name in {"power_mw", "area_score"} and valid and value is not None and value < 0:
            valid = False
            reason = "negative_metric"
        elif name in {"gbw_mhz", "pm_deg"} and not crossing_valid:
            valid = False
            reason = "no_valid_downward_0db_crossing"
        elif name == "foms" and not (crossing_valid and power is not None and power > 0):
            valid = False
            reason = "invalid_gbw_or_power"
        elif name == "sr_min_v_per_us" and not slew_valid:
            valid = False
            reason = "invalid_directional_slew"
        elif name == "foml" and not (
            slew_valid and power is not None and power > 0
        ):
            valid = False
            reason = "invalid_slew_or_power"
        elif analysis == "rejection" and row.get("valid") is not True:
            valid = False
            reason = "invalid_rejection_measurement"
        if not valid and reason is None:
            reason = "metric_unavailable"
        reports[name] = {"valid": bool(valid), "reason": reason}
    if "transient" in analyses:
        rise = _finite(row.get("rise_settling_time_us"))
        fall = _finite(row.get("fall_settling_time_us"))
        reports["settling_time_us"] = {
            "valid": rise is not None and fall is not None,
            "reason": None if rise is not None and fall is not None else "metric_unavailable",
        }
    return reports


def _aggregate_metric_validity(
    reports: Mapping[str, Mapping[str, Mapping[str, Any]]],
    analyses: set[str],
) -> dict[str, dict[str, Any]]:
    aggregated: dict[str, dict[str, Any]] = {}
    names = {
        name for name, analysis in METRIC_ANALYSIS.items() if analysis in analyses
    }
    if "transient" in analyses:
        names.add("settling_time_us")
    for name in sorted(names):
        invalid = [
            (corner, report.get(name, {"valid": False, "reason": "metric_unavailable"}))
            for corner, report in reports.items()
            if report.get(name, {}).get("valid") is not True
        ]
        if invalid:
            corner, detail = invalid[0]
            reason = str(detail.get("reason") or "metric_unavailable")
            aggregated[name] = {
                "valid": False,
                "reason": reason if len(reports) == 1 else f"{corner}:{reason}",
            }
        else:
            aggregated[name] = {"valid": True, "reason": None}
    return aggregated


def _worst(corners: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, float], dict[str, str]]:
    metrics: dict[str, float] = {}
    sources: dict[str, str] = {}
    for name, mode in DEFAULT_WORST_MODE.items():
        values = [(corner, row[name]) for corner, row in corners.items() if name in row]
        if not values:
            continue
        selected = min(values, key=lambda item: item[1]) if mode == "min" else max(values, key=lambda item: item[1])
        sources[name], metrics[name] = selected[0], float(selected[1])
    return metrics, sources


def evaluate_candidate(
    task: Mapping[str, Any],
    candidate_dir: str | Path,
    artifact_dir: str | Path,
) -> dict[str, Any]:
    validate_metric_requests(task)
    evaluator = dict(task["evaluator"])
    analyses = set(evaluator["analyses"])
    candidate = CandidateInput.load(Path(candidate_dir))
    adapter = NgspiceAdapter(
        pdk=Path(evaluator["pdk"]) if evaluator.get("pdk") else None,
        ngspice=evaluator.get("ngspice"),
        timeout_s=int(evaluator.get("timeout_s", 180)),
        expected_pdk_sha256=evaluator.get("expected_pdk_sha256"),
    )
    root = Path(artifact_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)

    corner_rows: dict[str, dict[str, Any]] = {}
    raw_rows: dict[str, dict[str, Any]] = {}
    corner_metric_validity: dict[str, dict[str, dict[str, Any]]] = {}
    for name in evaluator["corners"]:
        row = adapter._run(
            candidate,
            root / name.lower(),
            CORNERS[name],
            include_core=bool({"op", "ac"} & analyses),
            include_rejection="rejection" in analyses,
            include_transient="transient" in analyses,
        )
        row = parse_artifacts(
            row, candidate, CORNERS[name],
            include_core=bool({"op", "ac"} & analyses),
            include_rejection="rejection" in analyses,
            include_transient="transient" in analyses,
        )
        raw_rows[name] = row
        corner_rows[name] = _row_metrics(row, analyses)
        corner_metric_validity[name] = _row_metric_validity(row, analyses)
    metrics, sources = _worst(corner_rows)
    metric_validity = _aggregate_metric_validity(corner_metric_validity, analyses)
    validity = {
        corner: _validity_report(row, analyses)
        for corner, row in raw_rows.items()
    }
    failed = [
        f"{corner}:{check}"
        for corner, row in raw_rows.items()
        for check in _validity_failures(row, analyses)
    ]
    result = {
        "schema": "analog-arena/evaluation/v2",
        "profile": "sky130-ota",
        "candidate_key": candidate.candidate_key,
        "status": "VALID" if not failed else "INVALID",
        "failed_checks": failed,
        "metrics": metrics,
        "metric_validity": metric_validity,
        "worst_corner": sources,
        "corners": corner_rows,
        "validity": validity,
        "spice_evaluations": sum(row["spice_evaluations"] for row in raw_rows.values()),
        "artifact_dir": str(root),
    }
    result.update(score_result(result, task.get("constraints") or {}))
    (root / "result.json").write_text(
        json.dumps(
            {
                **result,
                "raw_corners": raw_rows,
            },
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return result
