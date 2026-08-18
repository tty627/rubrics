"""Canonical numbered entry point; implementation remains in stages/s02b_route.py during migration."""
from pathlib import Path
import runpy

_SOURCE = Path(__file__).resolve().parents[1] / "stages/s02b_route.py"

if __name__ == "__main__":
    runpy.run_path(str(_SOURCE), run_name="__main__")
