"""Export the normalized rubric delivery and internal artifacts."""
from pathlib import Path
import runpy

_SOURCE = Path(__file__).resolve().parents[1] / "scripts/export_advisor_schema.py"

if __name__ == "__main__":
    runpy.run_path(str(_SOURCE), run_name="__main__")
