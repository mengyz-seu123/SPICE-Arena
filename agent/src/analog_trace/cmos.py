"""Fixed transient fixtures and scoring for the CMOS tasks in basic-cmos-sram-tasks.md."""
from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess
from typing import Iterable

from analog_arena.simulation.ngspice import default_ngspice, default_pdk, ngspice_run_command


CONTRACTS = {
    "inverter": {
        "profile": "sky130-cmos-inverter-transient-v1",
        "subcircuit": ".subckt DUT IN OUT VDD VSS",
        "ports": ["IN", "OUT", "VDD", "VSS"],
        "metrics": {
            "voh_min_v": {"min": 1.5}, "vol_v": {"max": 0.3},
            "tphl_ns": {"min": 0.0, "max": 10.0}, "tplh_ns": {"min": 0.0, "max": 10.0},
        },
        "testbench": "VDD=1.8 V, CLOAD=10 fF; VIN 0->1.8 V at 100 ns and 1.8->0 V at 200 ns, 1 ns edges; 0.05 ns maximum step, 300 ns total.",
    },
    "sram6t": {
        "profile": "sky130-6t-sram-write-hold-v1",
        "subcircuit": ".subckt DUT Q QB BL BLB WL VDD VSS",
        "ports": ["Q", "QB", "BL", "BLB", "WL", "VDD", "VSS"],
        "metrics": {
            "write0_q_v": {"max": 0.3}, "write0_qb_v": {"min": 1.5},
            "hold0_q_v": {"max": 0.3}, "hold0_qb_v": {"min": 1.5},
            "write1_q_v": {"min": 1.5}, "write1_qb_v": {"max": 0.3},
            "hold1_q_v": {"min": 1.5}, "hold1_qb_v": {"max": 0.3},
        },
        "testbench": "VDD=1.8 V; BL/BLB and WL use the specified 1 ns edges; 0.05 ns maximum step, one continuous 400 ns transient run.",
    },
}


def contract(task: str) -> dict:
    if task not in CONTRACTS:
        raise ValueError("not a CMOS transient task")
    value = json.loads(json.dumps(CONTRACTS[task]))
    value["task"] = task
    value["device_syntax"] = "Xname D G S B sky130_fd_pr__nfet_01v8|sky130_fd_pr__pfet_01v8 W=<um> L=<um> [M=<integer>]"
    return value


def _deck(task: str, dut: str) -> str:
    pdk = default_pdk().as_posix()
    common = f'''* Analog-Arena fixed {task} fixture
.lib "{pdk}" tt
VDD VDD 0 1.8
{dut.strip()}
'''
    if task == "inverter":
        return common + '''VIN IN 0 PWL(0 0 100n 0 101n 1.8 200n 1.8 201n 0 300n 0)
XDUT IN OUT VDD 0 DUT
CLOAD OUT 0 10f
.control
set filetype=ascii
tran 0.05n 300n
wrdata waveform.tsv time v(IN) v(OUT)
quit
.endc
.end
'''
    return common + '''VBL BL 0 PWL(0 0 200n 0 201n 1.8 400n 1.8)
VBLB BLB 0 PWL(0 1.8 200n 1.8 201n 0 400n 0)
VWL WL 0 PWL(0 0 10n 0 11n 1.8 100n 1.8 101n 0 210n 0 211n 1.8 300n 1.8 301n 0 400n 0)
XDUT Q QB BL BLB WL VDD 0 DUT
.control
set filetype=ascii
tran 0.05n 400n
wrdata waveform.tsv time v(Q) v(QB) v(BL) v(BLB) v(WL)
quit
.endc
.end
'''


def _read_wave(path: Path, count: int) -> list[tuple[float, ...]]:
    rows: list[tuple[float, ...]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            raw = [float(v) for v in line.split()]
        except ValueError:
            continue
        # ngspice wrdata writes a time column before each requested vector.
        if len(raw) == count * 2:
            rows.append(tuple(raw[2 * i + 1] for i in range(count)))
        elif len(raw) == count:
            rows.append(tuple(raw))
    if len(rows) < 2 or any(not math.isfinite(v) for row in rows for v in row):
        raise ValueError("waveform.tsv has insufficient finite samples")
    if any(b[0] <= a[0] for a, b in zip(rows, rows[1:])):
        raise ValueError("waveform time is not strictly increasing")
    return rows


def _at(rows: Iterable[tuple[float, ...]], t: float, column: int) -> float:
    data = list(rows)
    for left, right in zip(data, data[1:]):
        if left[0] <= t <= right[0]:
            ratio = (t - left[0]) / (right[0] - left[0])
            return left[column] + ratio * (right[column] - left[column])
    raise ValueError(f"requested time {t} is outside waveform")


def _cross(rows: list[tuple[float, ...]], source: int, direction: int, start: float) -> float:
    for left, right in zip(rows, rows[1:]):
        if left[0] < start:
            continue
        a, b = left[source] - 0.9, right[source] - 0.9
        if (direction > 0 and a <= 0 < b) or (direction < 0 and a >= 0 > b):
            return left[0] + (right[0] - left[0]) * (-a) / (b - a)
    raise ValueError("required 0.9 V crossing was not observed")


def _validity(metrics: dict, error: str | None = None) -> dict:
    return {name: {"valid": error is None and isinstance(value, (int, float)) and math.isfinite(value), "reason": error} for name, value in metrics.items()}


def _score_inverter(rows: list[tuple[float, ...]]) -> dict:
    vin_rise = _cross(rows, 1, 1, 99e-9)
    out_fall = _cross(rows, 2, -1, vin_rise)
    vin_fall = _cross(rows, 1, -1, 199e-9)
    out_rise = _cross(rows, 2, 1, vin_fall)
    return {
        "voh_min_v": min(_at(rows, 90e-9, 2), _at(rows, 290e-9, 2)),
        "vol_v": _at(rows, 190e-9, 2),
        "tphl_ns": (out_fall - vin_rise) * 1e9,
        "tplh_ns": (out_rise - vin_fall) * 1e9,
    }


def _score_sram(rows: list[tuple[float, ...]]) -> dict:
    return {
        "write0_q_v": _at(rows, 90e-9, 1), "write0_qb_v": _at(rows, 90e-9, 2),
        "hold0_q_v": _at(rows, 190e-9, 1), "hold0_qb_v": _at(rows, 190e-9, 2),
        "write1_q_v": _at(rows, 290e-9, 1), "write1_qb_v": _at(rows, 290e-9, 2),
        "hold1_q_v": _at(rows, 390e-9, 1), "hold1_qb_v": _at(rows, 390e-9, 2),
    }


def evaluate(task: str, dut: str, output: Path, timeout_s: int = 180) -> dict:
    if task not in CONTRACTS:
        raise ValueError("unknown CMOS task")
    if ".subckt dut" not in dut.lower():
        raise ValueError("DUT must define the required .subckt DUT interface")
    output.mkdir(parents=True, exist_ok=False)
    deck = _deck(task, dut)
    (output / "tb.spice").write_text(deck, encoding="utf-8")
    command = ngspice_run_command(output, executable=default_ngspice())
    try:
        run = subprocess.run(command, cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_s)
        (output / "stdout.txt").write_text(run.stdout, encoding="utf-8")
        (output / "stderr.txt").write_text(run.stderr, encoding="utf-8")
        log = (output / "ngspice.log").read_text(encoding="utf-8", errors="replace") if (output / "ngspice.log").is_file() else ""
        if run.returncode or "error" in log.lower() or not (output / "waveform.tsv").is_file():
            raise RuntimeError(log or run.stderr or run.stdout or "ngspice failed")
        rows = _read_wave(output / "waveform.tsv", 3 if task == "inverter" else 6)
        metrics = _score_inverter(rows) if task == "inverter" else _score_sram(rows)
        result = {"status": "VALID", "profile": CONTRACTS[task]["profile"], "corners": {"TT": {"status": "VALID"}},
                  "metrics": metrics, "metric_validity": _validity(metrics), "candidate_key": None,
                  "command": command, "samples": len(rows)}
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        metrics = {name: None for name in CONTRACTS[task]["metrics"]}
        result = {"status": "INVALID", "profile": CONTRACTS[task]["profile"], "corners": {}, "metrics": metrics,
                  "metric_validity": _validity(metrics, str(exc)), "error": str(exc), "candidate_key": None, "command": command}
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return result
