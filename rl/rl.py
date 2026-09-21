"""Run the RL workflow from the repository checkout."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rl" / "src"))
from analog_rl.pipeline import main

raise SystemExit(main())
