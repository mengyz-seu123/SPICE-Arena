"""Portable source checkout entry; prefer the package's virtual environment."""
from pathlib import Path
import os
import sys
import subprocess

ROOT = Path(__file__).resolve().parents[1]
python = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
if python.is_file() and Path(sys.executable).absolute() != python.absolute():
    raise SystemExit(subprocess.call([str(python), str(Path(__file__).resolve()), *sys.argv[1:]]))
sys.path.insert(0, str(ROOT / 'agent/src'))
sys.path.insert(0, str(ROOT / 'simulation/src'))
from analog_trace.cli import main
raise SystemExit(main())
