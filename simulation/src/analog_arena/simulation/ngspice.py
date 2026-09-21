"""Portable ngspice execution for arbitrary SPICE decks."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time


def default_pdk() -> Path:
    configured = os.environ.get("ARENA_PDK") or os.environ.get("THREE_DIRECTION_PDK")
    if configured:
        return Path(configured)
    from .environment import DEFAULT_PDK, LIBRARY_RELATIVE
    return DEFAULT_PDK / LIBRARY_RELATIVE


def default_ngspice() -> str:
    return os.environ.get("NGSPICE_EXECUTABLE") or "ngspice"


def uses_wsl(executable: str | None = None) -> bool:
    executable = executable or default_ngspice()
    return os.name == "nt" and not (
        executable.lower().endswith(".exe") or shutil.which(executable)
    )


def sim_path(path: Path, executable: str | None = None) -> str:
    resolved = path.resolve()
    if not uses_wsl(executable):
        return resolved.as_posix()
    if not resolved.drive or ":/" not in resolved.as_posix():
        raise ValueError(f"WSL requires a drive-backed Windows path: {resolved}")
    return f"/mnt/{resolved.drive.rstrip(':').lower()}/" + resolved.as_posix().split(":/", 1)[1]


def ngspice_run_command(
    run_dir: Path, *, executable: str, deck: str = "tb.spice", log: str = "ngspice.log"
) -> list[str]:
    args = [executable, "-b", "-o", log, deck]
    return ["wsl.exe", "--cd", sim_path(run_dir, executable), *args] if uses_wsl(executable) else args


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def version(executable: str | None = None) -> dict:
    executable = executable or default_ngspice()
    command = [executable, "--version"]
    if uses_wsl(executable):
        command = ["wsl.exe", *command]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        return {"ok": result.returncode == 0, "command": command, "version": (result.stdout + result.stderr).strip()}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "command": command, "error": str(exc)}


def run_batch(run_dir: Path, *, executable: str, deck: str = "tb.spice", timeout_s: float = 180) -> dict:
    if timeout_s <= 0:
        raise ValueError("timeout must be positive")
    command = ngspice_run_command(run_dir, executable=executable, deck=deck)
    started = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(command, cwd=run_dir, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_s)
        returncode, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = 124
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
    log_path = run_dir / "ngspice.log"
    log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    errors = ("error:", "fatal error", "simulation interrupted due to error", "timestep too small", "doanalyses:")
    ok = returncode == 0 and bool(log) and not any(marker in log.lower() for marker in errors)
    result = {"status": "VALID" if ok else "INVALID", "returncode": returncode, "timed_out": timed_out,
              "command": command, "wall_time_s": time.monotonic() - started, "stdout": stdout, "stderr": stderr}
    (run_dir / "process.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


# Copy included source into the artifact tree, rewriting only include/lib paths.
# Device syntax and analysis directives are passed to ngspice unchanged.
_INCLUDE = re.compile(r'^(\s*\.(?:inc|include|lib)\s+)("[^"]+"|\x27[^\x27]+\x27|\S+)(.*)$', re.I)


def _stage_deck(source: Path, target: Path, executable: str) -> None:
    seen: dict[Path, Path] = {source: target}
    source_root = source.parent

    def stage(src: Path, dst: Path) -> None:
        lines = []
        for line in src.read_text(encoding="utf-8").splitlines():
            match = _INCLUDE.match(line)
            if match:
                prefix, token, tail = match.groups()
                # .lib section declarations contain no filename/section pair.
                if prefix.strip().lower() == ".lib" and not tail.strip():
                    lines.append(line)
                    continue
                ref = Path(token.strip('"\x27'))
                dependency = ref if ref.is_absolute() else src.parent / ref
                if not dependency.is_file() and not ref.is_absolute():
                    dependency = source_root / ref
                dependency = dependency.resolve()
                if not dependency.is_file():
                    raise FileNotFoundError(f"SPICE dependency: {dependency}")
                if dependency not in seen:
                    copied = target.parent / "includes" / f"{len(seen):04d}-{dependency.name}"
                    seen[dependency] = copied
                    stage(dependency, copied)
                line = f'{prefix}"{sim_path(seen[dependency], executable)}"{tail}'
            lines.append(line)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text("\n".join(lines) + "\n", encoding="utf-8")

    stage(source, target)


def simulate(netlist: str | Path, output: str | Path, *, executable: str | None = None, timeout_s: float = 180) -> dict:
    source, root = Path(netlist).resolve(), Path(output).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if timeout_s <= 0:
        raise ValueError("timeout must be positive")
    root.mkdir(parents=True, exist_ok=False)
    executable = executable or default_ngspice()
    _stage_deck(source, root / "tb.spice", executable)
    result = {"schema": "analog-arena/simulation/v1", "netlist": str(source), "netlist_sha256": sha256(source),
              "artifact_dir": str(root), **run_batch(root, executable=executable, timeout_s=timeout_s)}
    (root / "simulation.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result
