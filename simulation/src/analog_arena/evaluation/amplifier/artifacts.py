"""Offline parsing of amplifier simulation artifacts."""
from __future__ import annotations
import math
from pathlib import Path
from typing import Any
from analog_arena.evaluation.canonical import operating_point_metrics
from analog_arena.simulation.amplifier.testbench import TESTBENCH_CONTRACT
from analog_arena.simulation.amplifier.runtime import Corner, _op_names
from . import metrics as CORE

def _core_metrics_valid(row):
    return row.get("valid_first_0db_crossing") is True

def _read_table(path: Path) -> tuple[list[str], list[list[float]]]:
    rows = [line.strip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    if len(rows) < 2:
        raise ValueError(f"insufficient table data: {path.name}")
    header = rows[0].split()
    values = [[float(token) for token in row.split()] for row in rows[1:]]
    if any(len(row) != len(header) for row in values):
        raise ValueError(f"column mismatch: {path.name}")
    if any(not math.isfinite(value) for row in values for value in row):
        raise ValueError(f"non-finite table data: {path.name}")
    return header, values


def _rejection_metrics(run_dir: Path, corner: Corner) -> dict[str, Any]:
    _, op_rows = _read_table(run_dir / "rejection_op.tsv")
    _, ac_rows = _read_table(run_dir / "rejection_ac.tsv")
    if not op_rows or not ac_rows or len(op_rows[0]) < 4 or len(ac_rows[0]) < 4:
        return {"valid": False, "error": "missing rejection columns"}
    outputs = [float(value) for value in op_rows[0][1:4]]
    frequency, cm_transfer, plus_transfer, minus_transfer = [float(value) for value in ac_rows[0][:4]]
    finite = all(math.isfinite(value) for value in outputs + [frequency, cm_transfer, plus_transfer, minus_transfer])
    in_rails = finite and all(-1e-3 <= value <= corner.vdd_v + 1e-3 for value in outputs)
    centered = finite and all(abs(value - 0.45) <= 0.05 for value in outputs)
    low_frequency_ok = finite and abs(frequency - 0.1) <= 1e-9
    return {
        "valid": bool(finite and low_frequency_ok),
        "frequency_hz": frequency,
        "op_outputs_v": {"cm_out": outputs[0], "psrr_plus_out": outputs[1], "psrr_minus_out": outputs[2]},
        "op_in_rails": in_rails,
        "op_centered_0p45_plusminus_0p05": centered,
        "low_frequency_ok": low_frequency_ok,
        "cmrr_db": -cm_transfer if finite else None,
        "psrr_plus_db": -plus_transfer if finite else None,
        "psrr_minus_db": -minus_transfer if finite else None,
    }


def parse_artifacts(raw, candidate, corner, *, include_core, include_rejection, include_transient):
    result = dict(raw)
    run_dir = Path(result["artifact_dir"])
    transient_testbench = TESTBENCH_CONTRACT["unity_gain_transient"]
    validity_contract = TESTBENCH_CONTRACT["validity"]
    analysis_errors: dict[str, str] = {}
    if include_core:
        try:
            op_header, op_rows = _read_table(run_dir / "op.tsv")
            power_header, power_rows = _read_table(run_dir / "power.tsv")
            if power_header[1:] != ["p_vdd_delivered_mw", "p_ibias_delivered_mw"]:
                raise ValueError("power.tsv requires VDD and IBIAS delivered-power columns")
            _, ac_rows = _read_table(run_dir / "ac.tsv")
            op_names = _op_names(candidate.structure)
            op_row = op_rows[0]
            if len(op_row) != len(op_names) + 1:
                raise ValueError(f"OP column mismatch: expected {len(op_names) + 1}, got {len(op_row)}")
            op = operating_point_metrics(
                dict(zip(op_names, op_row[1:])),
                output_node="a_out",
                required_nodes=tuple(op_names),
                output_target_v=float(validity_contract["op_output_target_v"]),
                output_tolerance_v=float(validity_contract["op_output_tolerance_v"]),
                vss_v=float(TESTBENCH_CONTRACT["supplies"]["vss_v"]),
                vdd_v=corner.vdd_v,
                rail_tolerance_v=float(validity_contract["op_rail_tolerance_v"]),
            )
            op["header"] = op_header
            power_parts = [float(value) for value in power_rows[-1][1:]]
            power_mw = sum(max(0.0, value) for value in power_parts)
            ac = CORE.extract_ac_metrics(
                [[row[0], row[1], row[2]] for row in ac_rows],
                input_amplitude_v=float(
                    TESTBENCH_CONTRACT["unity_gain_ac"]["ac_input_v"]
                ),
            )
            result.update(ac)
            result.update(
                {
                    "op": op,
                    "power_components_mw": power_parts,
                    "power_mw": power_mw,
                    "area_score": CORE.calculate_area_score(candidate.structure),
                    "op_finite": bool(op["gates"]["op_finite"]),
                    "op_output_centered": bool(op["gates"]["op_output_centered"]),
                    "op_nodes_within_rails": bool(op["gates"]["op_nodes_within_rails"]),
                }
            )
            if (
                _core_metrics_valid(result)
                and power_mw > 0
                and isinstance(result.get("gbw_mhz"), (int, float))
            ):
                result["foms"] = CORE.calculate_foms(float(result["gbw_mhz"]), power_mw)
        except (OSError, ValueError, IndexError) as exc:
            analysis_errors["core"] = str(exc)
    if include_transient:
        try:
            _, tran_rows = _read_table(run_dir / "tran.tsv")
            transient = CORE.extract_transient_metrics(
                [[row[0], row[1], row[2]] for row in tran_rows],
                low_v=float(transient_testbench["input_low_v"]),
                high_v=float(transient_testbench["input_high_v"]),
                vss_v=float(TESTBENCH_CONTRACT["supplies"]["vss_v"]),
                vdd_v=corner.vdd_v,
                rail_tolerance_v=float(validity_contract["transient_rail_tolerance_v"]),
                initial_tolerance_fraction=float(
                    validity_contract["transient_initial_tolerance_fraction"]
                ),
                settling_tolerance_fraction=float(
                    validity_contract["transient_settling_tolerance_fraction"]
                ),
                overshoot_tolerance_v=float(
                    validity_contract["transient_overshoot_audit_tolerance_v"]
                ),
            )
            result.update(
                {
                    "transient": transient,
                    "transient_valid": bool(transient["transient_valid"]),
                    "sr_min_v_per_us": transient.get("sr_min_v_per_us"),
                    "rise_settling_time_us": transient.get("rise_settling_time_us"),
                    "fall_settling_time_us": transient.get("fall_settling_time_us"),
                }
            )
            if (
                transient.get("slew_measurable") is True
                and transient.get("rail_valid") is True
                and result.get("power_mw", 0) > 0
                and isinstance(transient.get("sr_min_v_per_us"), (int, float))
            ):
                result["foml"] = CORE.calculate_foml(
                    float(transient["sr_min_v_per_us"]), float(result["power_mw"])
                )
        except (OSError, ValueError, IndexError) as exc:
            analysis_errors["transient"] = str(exc)
    if include_rejection:
        try:
            rejection = _rejection_metrics(run_dir, corner)
            result["rejection"] = rejection
            result.update(
                {
                    "valid": rejection.get("valid", False),
                    "cmrr_db": rejection.get("cmrr_db"),
                    "psrr_plus_db": rejection.get("psrr_plus_db"),
                    "psrr_minus_db": rejection.get("psrr_minus_db"),
                }
            )
        except (OSError, ValueError, IndexError) as exc:
            analysis_errors["rejection"] = str(exc)
    if analysis_errors:
        result["analysis_errors"] = analysis_errors
        result["no_ngspice_errors"] = False
    return result
