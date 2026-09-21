"""Run SFT utilities from the source checkout."""
from pathlib import Path
import sys
root = Path(__file__).resolve().parents[1]
for component in ("sft", "agent", "simulation"):
    sys.path.insert(0, str(root / component / "src"))
from spice_sft.cli import main
raise SystemExit(main())
