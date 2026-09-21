"""Compatibility entry point for the agent CLI."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).resolve().parent / 'agent' / 'trace.py'), run_name='__main__')
