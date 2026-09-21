"""Run the package's isolated installation without shell activation."""
from pathlib import Path
import os
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
python = root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
if not python.is_file():
    print('Run Python 3.10+ on environment/setup.py first; see README.md.', file=sys.stderr)
    raise SystemExit(2)
raise SystemExit(subprocess.call([str(python), '-m', 'analog_arena', *sys.argv[1:]]))
