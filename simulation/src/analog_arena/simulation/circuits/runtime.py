from __future__ import annotations

from pathlib import Path
from typing import Any

from analog_arena.simulation.ngspice import run_batch, sim_path

from .candidate import GenericCandidate
from .specs import CircuitSpec


EXECUTION_ERRORS = (
    "fatal error",
    "simulation interrupted due to error",
    "doanalyses: no such",
    "timestep too small",
    "singular matrix",
)


def _render_fixture(
    spec: CircuitSpec, candidate: GenericCandidate, pdk: Path, corner: str, executable: str | None = None
) -> str:
    template = spec.fixture_path.read_text(encoding="utf-8")
    return (
        template.replace("{{PDK_LIB}}", f'"{sim_path(pdk, executable)}"')
        .replace("{{CORNER}}", corner.lower())
        .replace("{{DUT}}", candidate.expanded_dut.rstrip())
    )

def _run_corner(
    spec: CircuitSpec,
    candidate: GenericCandidate,
    *,
    pdk: Path,
    executable: str,
    timeout_s: int,
    corner: str,
    run_dir: Path,
) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    deck = _render_fixture(spec, candidate, pdk, corner, executable)
    (run_dir / "tb.spice").write_text(deck, encoding="utf-8", newline="\n")
    completed = run_batch(run_dir, executable=executable, timeout_s=timeout_s)
    log_path = run_dir / "ngspice.log"
    log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    lowered = log.lower()
    execution_valid = (
        completed["status"] == "VALID"
        and bool(log)
        and not any(marker in lowered for marker in EXECUTION_ERRORS)
    )
    return {
        "execution_valid": execution_valid,
        "returncode": completed["returncode"],
        "timed_out": completed["timed_out"],
        "wall_time_s": completed["wall_time_s"],
        "command": completed["command"],
        "artifact_dir": str(run_dir.resolve()),
        "stdout": completed["stdout"][-4000:],
        "stderr": completed["stderr"][-4000:],
    }
