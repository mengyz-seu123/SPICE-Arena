from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Mapping

from analog_arena.simulation.ngspice import (
    default_ngspice,
    default_pdk as _default_pdk,
    ngspice_run_command,
    sha256 as _sha256,
    sim_path as _sim_path,
)
from .candidate import CandidateInput
from .testbench import TESTBENCH_CONTRACT


PVT_CONDITIONS = (
    ("SS", "ss", 1.62, -25),
    ("FF", "ff", 1.98, 125),
    ("SF", "sf", 1.98, -25),
    ("FS", "fs", 1.62, 125),
)


@dataclass(frozen=True)
class Corner:
    name: str
    model: str
    vdd_v: float
    temperature_c: int


CORNERS: dict[str, Corner] = {"TT": Corner("TT", "tt", 1.80, 27)}
CORNERS.update({name: Corner(name, model, vdd, temperature) for name, model, vdd, temperature in PVT_CONDITIONS})


def _op_names(structure: Mapping[str, Any]) -> list[str]:
    return [
        "a_out",
        "a_outdc",
        "a_inp",
        "a_signal",
        "a_ibias",
        *(f"xa.{node}" for node in structure["internal_nodes"]),
    ]


def render_deck(
    candidate: CandidateInput,
    run_dir: Path,
    corner: Corner,
    *,
    include_core: bool,
    include_rejection: bool,
    include_transient: bool | None = None,
    pdk: Path | None = None,
    executable: str | None = None,
) -> str:
    def sim_file(path):
        return _sim_path(path, executable)

    if include_transient is None:
        include_transient = include_core
    if not include_core and not include_rejection and not include_transient:
        raise ValueError("at least one simulation stage is required")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "dut.expanded.spice").write_text(candidate.expanded_dut, encoding="utf-8", newline="\n")
    (run_dir / "dut.vss.spice").write_text(candidate.vss_dut, encoding="utf-8", newline="\n")
    pdk_path = pdk or _default_pdk()
    vdd = f"{corner.vdd_v:.2f}"
    ac_testbench = TESTBENCH_CONTRACT["unity_gain_ac"]
    transient_testbench = TESTBENCH_CONTRACT["unity_gain_transient"]
    ac_sweep = ac_testbench["sweep_hz"]
    common_mode_v = float(ac_testbench["input_common_mode_v"])
    ac_input_v = float(ac_testbench["ac_input_v"])
    ac_load_pf = float(ac_testbench["load_pf"])
    transient_load_pf = float(transient_testbench["load_pf"])
    validity_contract = TESTBENCH_CONTRACT["validity"]
    fixtures: list[str] = []
    commands: list[str] = ["set wr_vecnames", "set wr_singlescale", "set units=degrees", "op"]

    if include_core:
        fixtures.append(
            f"""
VADDA a_vdd 0 {vdd}
VAINP a_inp 0 {common_mode_v:g}
VAIN a_signal 0 DC {common_mode_v:g} AC {ac_input_v:g}
LA_FB a_out a_outdc 1T
CA_IN a_outdc a_signal 1T
IA_BIAS a_ibias 0 {candidate.ibias_ua:.1f}u
XA a_vdd 0 a_inp a_outdc a_out a_ibias OTA
CA_LOAD a_out 0 {ac_load_pf:g}p
""".strip()
        )
        op_vectors = " ".join(f"v({name})" for name in _op_names(candidate.structure))
        commands.extend(
            [
                f"let p_vdd_delivered_mw = -i(VADDA)*v(a_vdd)*1e3",
                "let p_ibias_delivered_mw = -@IA_BIAS[p]*1e3",
                f"wrdata {sim_file(run_dir / 'op.tsv')} {op_vectors}",
                f"wrdata {sim_file(run_dir / 'power.tsv')} p_vdd_delivered_mw p_ibias_delivered_mw",
            ]
        )

    if include_transient:
        fixtures.append(
            f"""
VTDD t_vdd 0 {vdd}
VTIN t_in 0 PULSE({float(transient_testbench['input_low_v']):g} {float(transient_testbench['input_high_v']):g} {float(transient_testbench['delay_us']):g}u {float(transient_testbench['rise_time_ns']):g}n {float(transient_testbench['fall_time_ns']):g}n {float(transient_testbench['high_time_us']):g}u {float(transient_testbench['period_us']):g}u)
IT_BIAS t_ibias 0 {candidate.ibias_ua:.1f}u
XT t_vdd 0 t_in t_out t_out t_ibias OTA
CT_LOAD t_out 0 {transient_load_pf:g}p
""".strip()
        )

    if include_rejection:
        fixtures.append(
            f"""
VC_VDD c_vdd 0 {vdd}
VC_CM0 c_cm0 0 {common_mode_v:g}
VC_ACP c_inp c_cm0 0 AC {ac_input_v:g}
VC_ACN c_inn c_out 0 AC {ac_input_v:g}
IC_BIAS c_ibias 0 {candidate.ibias_ua:.1f}u
XC c_vdd 0 c_inp c_inn c_out c_ibias OTA
CC_LOAD c_out 0 {ac_load_pf:g}p

VP_VDD p_vdd 0 DC {vdd} AC 1
VP_INP p_inp 0 {common_mode_v:g}
IP_BIAS p_ibias 0 {candidate.ibias_ua:.1f}u
XP p_vdd 0 p_inp p_out p_out p_ibias OTA
CP_LOAD p_out 0 {ac_load_pf:g}p

VN_VDD n_vdd 0 {vdd}
VN_VSS n_vss 0 DC 0 AC 1
VN_INP n_inp 0 {common_mode_v:g}
IN_BIAS n_ibias 0 {candidate.ibias_ua:.1f}u
XN n_vdd n_vss n_inp n_out n_out n_ibias OTA_VSS
CN_LOAD n_out 0 {ac_load_pf:g}p
""".strip()
        )
        commands.append(f"wrdata {sim_file(run_dir / 'rejection_op.tsv')} v(c_out) v(p_out) v(n_out)")

    if include_core or include_rejection:
        if include_rejection and not include_core:
            # Rejection metrics are defined at the first 0.1 Hz sample. A
            # full 0.1 Hz..10 GHz sweep needlessly solves three extra DUTs.
            commands.append("ac lin 1 0.1 0.1")
        else:
            commands.append(
                "ac dec "
                f"{int(ac_sweep['points_per_decade'])} "
                f"{float(ac_sweep['start']):g} {float(ac_sweep['stop']):g}"
            )
        if include_core:
            commands.append(f"wrdata {sim_file(run_dir / 'ac.tsv')} vdb(a_out) vp(a_out)")
        if include_rejection:
            commands.extend(
                [
                    "let cm_transfer_db = 20*log10(mag(v(c_out))+1e-30)",
                    "let psrr_plus_transfer_db = 20*log10(mag(v(p_out))+1e-30)",
                    "let psrr_minus_transfer_db = 20*log10(mag(v(n_out))+1e-30)",
                    f"wrdata {sim_file(run_dir / 'rejection_ac.tsv')} cm_transfer_db psrr_plus_transfer_db psrr_minus_transfer_db",
                ]
            )
    if include_transient:
        commands.extend(
            [
                "tran "
                f"{float(transient_testbench['step_ns']):g}n "
                f"{float(transient_testbench['stop_time_us']):g}u",
                f"wrdata {sim_file(run_dir / 'tran.tsv')} v(t_out) v(t_in) i(vtdd)",
            ]
        )
    commands.extend(["quit", ".endc", ".end"])
    return (
        f"* three-direction staged evaluator; {corner.name}\n"
        f'.lib "{sim_file(pdk_path)}" {corner.model}\n'
        f'.include "{sim_file(run_dir / "dut.expanded.spice")}"\n'
        f'.include "{sim_file(run_dir / "dut.vss.spice")}"\n'
        f".temp {corner.temperature_c}\n"
        + (".option noacct method=gear maxord=2\n\n" if include_transient else ".option noacct\n\n")
        + "\n\n".join(fixtures)
        + "\n\n.control\n"
        + "\n".join(commands)
        + "\n"
    )


class NgspiceAdapter:
    def __init__(
        self,
        *,
        pdk: Path | None = None,
        ngspice: str | None = None,
        timeout_s: int = 180,
        expected_pdk_sha256: str | None = None,
    ):
        self.pdk = (pdk or _default_pdk()).resolve()
        if not self.pdk.is_file():
            raise FileNotFoundError(self.pdk)
        actual_pdk_digest = _sha256(self.pdk) if expected_pdk_sha256 else None
        if expected_pdk_sha256 and actual_pdk_digest != expected_pdk_sha256:
            raise RuntimeError(
                "PDK digest does not match evaluator.expected_pdk_sha256"
            )
        self.pdk_sha256 = actual_pdk_digest
        self.ngspice = ngspice or default_ngspice()
        self.timeout_s = timeout_s


    def _run(
        self,
        candidate: CandidateInput,
        run_dir: Path,
        corner: Corner,
        *,
        include_core: bool,
        include_rejection: bool,
        include_transient: bool,
    ) -> dict[str, Any]:
        # Independent fixtures must not share the transient matrix: otherwise
        # inactive AC feedback elements and four extra DUTs dominate runtime.
        selected = {
            "core": (include_core, ("op.tsv", "power.tsv", "ac.tsv")),
            "rejection": (include_rejection, ("rejection_op.tsv", "rejection_ac.tsv")),
            "transient": (include_transient, ("tran.tsv",)),
        }
        if not any(enabled for enabled, _ in selected.values()):
            raise ValueError("at least one simulation stage is required")
        started = time.perf_counter()
        run_dir.mkdir(parents=True, exist_ok=True)
        stages = {}
        logs = []
        for name, (enabled, tables) in selected.items():
            if not enabled:
                continue
            stage_dir = run_dir / name
            stages[name] = self._run_stage(
                candidate, stage_dir, corner,
                include_core=name == "core",
                include_rejection=name == "rejection",
                include_transient=name == "transient",
            )
            for table in tables:
                target = run_dir / table
                if target.exists():
                    target.unlink()
                source = stage_dir / table
                if source.is_file():
                    shutil.copy2(source, target)
            log_path = stage_dir / "ngspice.log"
            logs.append(f"[{name}]\n" + (log_path.read_text(encoding="utf-8", errors="replace")
                                      if log_path.is_file() else "missing log\n"))
        failed = [stage for stage in stages.values() if not stage["no_ngspice_errors"]]
        result = {
            "structural_pass": True,
            "no_ngspice_errors": not failed,
            "corner": corner.name,
            "model": corner.model,
            "vdd_v": corner.vdd_v,
            "temperature_c": corner.temperature_c,
            "returncode": next((stage["returncode"] for stage in failed if stage["returncode"]), 0),
            "timed_out": any(stage["timed_out"] for stage in stages.values()),
            "artifact_dir": str(run_dir.resolve()),
            "spice_evaluations": len(stages),
            "stages": stages,
            "wall_time_s": time.perf_counter() - started,
        }
        (run_dir / "ngspice.log").write_text("\n".join(logs), encoding="utf-8")
        (run_dir / "execution.json").write_text(
            json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        return result

    def _run_stage(
        self,
        candidate: CandidateInput,
        run_dir: Path,
        corner: Corner,
        *,
        include_core: bool,
        include_rejection: bool,
        include_transient: bool,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        run_dir.mkdir(parents=True, exist_ok=True)
        deck = render_deck(
            candidate,
            run_dir,
            corner,
            include_core=include_core,
            include_rejection=include_rejection,
            include_transient=include_transient,
            pdk=self.pdk,
            executable=self.ngspice,
        )
        (run_dir / "candidate.cir").write_text(deck, encoding="utf-8", newline="\n")
        env = os.environ.copy()
        env.pop("LD_LIBRARY_PATH", None)
        env.pop("PYTHONPATH", None)
        env["OMP_NUM_THREADS"] = "1"
        command = ngspice_run_command(
            run_dir,
            executable=self.ngspice,
            deck="candidate.cir",
            log="ngspice.log",
        )
        timed_out = False
        try:
            completed = subprocess.run(
                command,
                cwd=run_dir,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_s,
            )
            returncode = completed.returncode
            process_text = f"command: {json.dumps(command)}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}\n"
        except subprocess.TimeoutExpired as exc:
            returncode = 124
            timed_out = True
            process_text = f"command: {json.dumps(command)}\nTIMEOUT\nstdout:\n{exc.stdout or ''}\nstderr:\n{exc.stderr or ''}\n"
        (run_dir / "process.txt").write_text(process_text, encoding="utf-8", newline="\n")
        log_path = run_dir / "ngspice.log"
        log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else "missing log"
        has_error = returncode != 0 or any(
            marker in log.lower() for marker in ("error:", "fatal", "timestep too small", "doanalyses:")
        )
        result: dict[str, Any] = {
            "structural_pass": True,
            "no_ngspice_errors": not has_error,
            "corner": corner.name,
            "model": corner.model,
            "vdd_v": corner.vdd_v,
            "temperature_c": corner.temperature_c,
            "returncode": returncode,
            "timed_out": timed_out,
            "artifact_dir": str(run_dir.resolve()),
            "spice_evaluations": 1,
            "integration_method": "gear" if include_transient else "trapezoidal",
        }
        result["wall_time_s"] = time.perf_counter() - started
        (run_dir / "execution.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
            newline="\n",
        )
        return result
