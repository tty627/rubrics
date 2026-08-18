"""Canonical numbered entry point; implementation remains in scripts/audit_rubrics.py."""
from pathlib import Path
import runpy

_SOURCE = Path(__file__).resolve().parents[1] / "scripts/audit_rubrics.py"

if __name__ == "__main__":
    runpy.run_path(str(_SOURCE), run_name="__main__")
