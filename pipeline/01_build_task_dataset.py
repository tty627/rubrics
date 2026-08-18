"""Canonical numbered entry point; implementation remains in stages/s00_seed.py."""
from pathlib import Path
import runpy

_SOURCE = Path(__file__).resolve().parents[1] / "stages/s00_seed.py"

if __name__ == "__main__":
    runpy.run_path(str(_SOURCE), run_name="__main__")
