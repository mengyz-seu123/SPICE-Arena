from __future__ import annotations

from pathlib import Path
import hashlib
from typing import Any, Mapping

from analog_arena.simulation.circuits.specs import get_circuit_spec


def validate_task_config(task: Mapping[str, Any]) -> None:
    evaluator = task.get("evaluator") or {}
    circuit = str(evaluator.get("circuit") or "")
    spec = get_circuit_spec(circuit)
    analyses = tuple(evaluator.get("analyses") or [])
    if set(analyses) != set(spec.analyses):
        raise ValueError(
            f"evaluator.analyses must be {list(spec.analyses)} for circuit {circuit}"
        )
    if evaluator.get("prefilter") is not None:
        raise ValueError("evaluator.prefilter is only supported by sky130-ota")
    requested = set(task.get("constraints") or {}) | set(task.get("objectives") or {})
    unknown = requested - set(spec.metrics)
    if unknown:
        raise ValueError(f"unsupported metrics for {circuit}: {sorted(unknown)}")


def software_contract(circuit: str) -> dict[str, Any]:
    spec = get_circuit_spec(circuit)
    return {
        "schema": "analog-arena/software-contract/v3",
        "profile": "sky130-generic",
        "circuit": spec.id,
        "description": spec.description,
        "evaluator_contract_version": "sky130-generic-v1",
        "subcircuit": f".subckt {spec.top_subcircuit} {' '.join(spec.ports)}",
        "ports": list(spec.ports),
        "analyses": list(spec.analyses),
        "devices": {
            "models": sorted(spec.allowed_models),
            "required_model_groups": [
                sorted(group) for group in spec.required_model_groups
            ],
            "mos_syntax": "Xname D G S B model W=<um> L=<um> M=<integer>",
            "bjt_syntax": "Xname C B E model [M=<integer>]",
            "passives": ["R", "C"],
            "numeric_limits": {
                "mos_w_um": {"min": 0.42, "max": 10.0},
                "mos_l_um_1v8": {"min": 0.15, "max": 5.0},
                "mos_l_um_hv": {"min": 0.50, "max": 5.0},
                "multiplicity": {"integer_min": 1, "integer_max": 500},
                "resistance_ohm": {"min": 1.0, "max": 100_000_000.0},
                "capacitance_f": {"min": 1e-15, "max": 1e-5},
            },
            "forbidden": ["independent/dependent sources", ".include", ".lib", ".model", "analysis/control directives"],
        },
        "metrics": {
            name: {
                "analysis": metric.analysis,
                "worst": metric.worst,
                "unit": metric.unit,
            }
            for name, metric in spec.metrics.items()
        },
        "testbench": {
            "owned_by_evaluator": True,
            "fixture": spec.fixture,
            "fixture_sha256": hashlib.sha256(spec.fixture_path.read_bytes()).hexdigest(),
            "evidence_level": spec.evidence_level,
        },
        "result_semantics": {
            "valid": "all requested corner simulations execute successfully",
            "metric_validity": "each requested metric is finite at every requested corner",
            "feasible": "all constrained metrics are valid and pass the evaluation constraints",
        },
    }


from analog_arena.simulation.circuits.candidate import GenericCandidate
from analog_arena.simulation.circuits.runtime import _run_corner
from analog_arena.simulation.ngspice import default_pdk, default_ngspice, sha256
from .circuits import _aggregate, _score_corner_constraints, _parse_measures, _derive_metrics, _derive_ldo_wave_metrics, _derive_dac_wave_metrics, _parse_wave
import json
import math
def evaluate_candidate(
    task: Mapping[str, Any], candidate_dir: str | Path, artifact_dir: str | Path
) -> dict[str, Any]:
    evaluator = dict(task["evaluator"])
    spec = get_circuit_spec(str(evaluator["circuit"]))
    candidate = GenericCandidate.load(Path(candidate_dir), spec)
    pdk = Path(evaluator["pdk"]) if evaluator.get("pdk") else default_pdk()
    if not pdk.is_file():
        raise FileNotFoundError(pdk)
    expected = evaluator.get("expected_pdk_sha256")
    actual = sha256(pdk) if expected else None
    if expected and actual != expected:
        raise RuntimeError(f"PDK SHA-256 mismatch: expected {expected}, got {actual}")
    executable = str(evaluator.get("ngspice") or default_ngspice())
    timeout_s = int(evaluator.get("timeout_s", 180))
    root = Path(artifact_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)

    raw: dict[str, dict[str, Any]] = {}
    corner_metrics: dict[str, dict[str, float]] = {}
    validity: dict[str, dict[str, Any]] = {}
    for corner in evaluator["corners"]:
        row = _run_corner(
            spec,
            candidate,
            pdk=pdk,
            executable=executable,
            timeout_s=timeout_s,
            corner=corner,
            run_dir=root / corner.lower(),
        )
        corner_dir = Path(row["artifact_dir"])
        log_file = corner_dir / "ngspice.log"
        log = log_file.read_text(encoding="utf-8", errors="replace") if log_file.is_file() else ""
        row["metrics"] = _derive_metrics(spec, _parse_measures(log))
        if spec.id == "dac3":
            row["metrics"].update(_derive_dac_wave_metrics(
                _parse_wave(corner_dir / "dac_wave.dat"), row["metrics"]
            ))
        if spec.id in {"digital-ldo", "analog-ldo"}:
            row["metrics"].update(_derive_ldo_wave_metrics(_parse_wave(corner_dir / "ldo_wave.dat")))
            if spec.id == "analog-ldo":
                light = row["metrics"].get("vout_light_v")
                heavy = row["metrics"].get("vout_heavy_v")
                if light is not None and heavy is not None:
                    row["metrics"]["load_regulation_mv"] = abs(light - heavy) * 1e3
        raw[corner] = row
        corner_metrics[corner] = {
            name: float(value)
            for name, value in row["metrics"].items()
            if isinstance(value, (int, float)) and math.isfinite(float(value))
        }
        validity[corner] = {
            "passed": row["execution_valid"],
            "checks": {
                "ngspice_returncode_zero": row["returncode"] == 0,
                "ngspice_log_without_fatal_error": row["execution_valid"],
            },
        }

    metrics, worst_corner = _aggregate(spec, corner_metrics)
    scored = _score_corner_constraints(corner_metrics, task.get("constraints") or {})
    requested = set(task.get("constraints") or {}) | set(task.get("objectives") or {})
    metric_validity = {
        name: {
            "valid": all(name in corner_metrics[corner] for corner in evaluator["corners"]),
            "reason": None
            if all(name in corner_metrics[corner] for corner in evaluator["corners"])
            else "metric_unavailable",
        }
        for name in requested
    }
    failed = [
        f"{corner}:execution"
        for corner, report in validity.items()
        if report["passed"] is not True
    ]
    scored["feasible"] = scored["feasible"] and not failed and all(v["valid"] for v in metric_validity.values())
    result = {
        "schema": "analog-arena/evaluation/v2",
        "profile": "sky130-generic",
        "circuit": spec.id,
        "candidate_key": candidate.candidate_key,
        "status": "VALID" if not failed else "INVALID",
        "failed_checks": failed,
        "metrics": metrics,
        "metric_validity": metric_validity,
        "worst_corner": worst_corner,
        **scored,
        "corners": corner_metrics,
        "validity": validity,
        "spice_evaluations": len(raw),
        "artifact_dir": str(root),
        "evidence_level": spec.evidence_level,
        "fixture_sha256": candidate.structure["fixture_sha256"],
    }
    (root / "result.json").write_text(
        json.dumps({**result, "raw_corners": raw}, ensure_ascii=False, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return result
