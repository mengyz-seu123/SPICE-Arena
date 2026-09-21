"""Compatibility entry point for the simulation CLI."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).resolve().parent / 'simulation' / 'arena.py'), run_name='__main__')
