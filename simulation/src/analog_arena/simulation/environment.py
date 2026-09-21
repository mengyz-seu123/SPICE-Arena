from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[4]
SIMULATION_ROOT = REPO_ROOT / "simulation"
DEFAULT_ARCHIVE = SIMULATION_ROOT / "resources" / "benchmarks" / "analoggym-nmcf" / "mosfet_model" / "sky130_pdk.zip"
DEFAULT_PDK = DEFAULT_ARCHIVE.parent / "sky130_pdk"
LIBRARY_RELATIVE = Path("libs.tech") / "ngspice" / "sky130.lib.spice"


def setup_pdk(archive: str | Path | None = None, output: str | Path | None = None) -> dict[str, object]:
    source = Path(archive).resolve() if archive else DEFAULT_ARCHIVE.resolve()
    target = Path(output).resolve() if output else DEFAULT_PDK.resolve()
    library = target / LIBRARY_RELATIVE
    if library.is_file():
        return {"ok": True, "status": "available", "pdk": str(target), "library": str(library)}
    if target.exists():
        raise RuntimeError(f"incomplete PDK directory already exists: {target}")
    if not source.is_file():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="analog-arena-pdk-", dir=target.parent) as temporary:
        temporary_root = Path(temporary).resolve()
        with zipfile.ZipFile(source) as bundle:
            for member in bundle.infolist():
                destination = (temporary_root / member.filename).resolve()
                try:
                    destination.relative_to(temporary_root)
                except ValueError as exc:
                    raise RuntimeError(f"unsafe PDK archive member: {member.filename}") from exc
            bundle.extractall(temporary_root)
        extracted = temporary_root / "sky130_pdk"
        if not (extracted / LIBRARY_RELATIVE).is_file():
            raise RuntimeError("PDK archive does not contain the ngspice library")
        shutil.copytree(extracted, target)
    return {"ok": True, "status": "extracted", "pdk": str(target), "library": str(library)}
