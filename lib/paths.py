"""Canonical paths for the numbered pipeline.

Environment variables may override roots, while stage filenames remain stable.
Legacy flat paths are read only through explicit compatibility helpers.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = Path(os.environ.get("RP_DATA_ROOT", os.environ.get("RP_OUT", ROOT / "data")))
CACHE_ROOT = Path(os.environ.get("RP_CACHE", ROOT / "cache"))
OUTPUT_ROOT = Path(os.environ.get("RP_OUTPUT_ROOT", ROOT / "outputs"))

TASK_DATA = DATA_ROOT / "tasks"
RUBRIC_DATA = DATA_ROOT / "rubric"
EVALUATION_DATA = DATA_ROOT / "evaluation"
RELEASE_DATA = DATA_ROOT / "release"

OUTPUT_CURRENT = OUTPUT_ROOT / "current"
OUTPUT_RUNS = OUTPUT_ROOT / "runs"

TASK_FILES = {
    "seed": TASK_DATA / "01_task_dataset.jsonl",
    "filtered": TASK_DATA / "02_filtered_tasks.jsonl",
    "context": TASK_DATA / "03_task_context.jsonl",
    "types": TASK_DATA / "04_task_types.jsonl",
    "axes": TASK_DATA / "05_evaluation_axes.jsonl",
}
RUBRIC_FILES = {
    "draft": RUBRIC_DATA / "06_rubric_draft.jsonl",
    "diagnosed": RUBRIC_DATA / "07_rubric_diagnosed.jsonl",
    "revised": RUBRIC_DATA / "08_rubric_revised.jsonl",
    "rewrite_queue": RUBRIC_DATA / "08_criteria_rewrite_queue.jsonl",
    "criteria_rewritten": RUBRIC_DATA / "09_rubric_criteria_rewritten.jsonl",
    "penalties_classified": RUBRIC_DATA / "10_negative_criteria_classified.jsonl",
    "delivery_source": RUBRIC_DATA / "11_rubric_delivery_source.jsonl",
}
EVALUATION_FILES = {
    "answer_resolved": EVALUATION_DATA / "20_answer_resolved_tasks.jsonl",
    "tasks": EVALUATION_DATA / "20_evaluation_tasks.jsonl",
    "pool": EVALUATION_DATA / "21_response_pool.jsonl",
    "scores": EVALUATION_DATA / "22_response_scores.jsonl",
    "diagnostics": EVALUATION_DATA / "23_discrimination_diagnostics.jsonl",
    "selected": EVALUATION_DATA / "25_selected_rubrics.jsonl",
    "delivery_source": EVALUATION_DATA / "26_rubric_delivery_source.jsonl",
}
RELEASE_FILES = {
    "draft_scores": RELEASE_DATA / "30_draft_rubric_scores.jsonl",
    "comparison": RELEASE_DATA / "31_pairwise_comparison.jsonl",
    "report": RELEASE_DATA / "31_release_report.json",
    "audit": RELEASE_DATA / "33_delivery_audit.txt",
}
DELIVERY_FILES = {
    "rubric": OUTPUT_CURRENT / "rubric_delivery.jsonl",
    "internal": OUTPUT_CURRENT / "rubric_internal.jsonl",
    "xlsx": OUTPUT_CURRENT / "rubric_delivery.xlsx",
    "manifest": OUTPUT_CURRENT / "run_manifest.json",
}


def ensure_directories():
    for path in (TASK_DATA, RUBRIC_DATA, EVALUATION_DATA, RELEASE_DATA,
                 OUTPUT_CURRENT, OUTPUT_RUNS, CACHE_ROOT):
        path.mkdir(parents=True, exist_ok=True)


def relative(path):
    """Return a stable repository-relative display path when possible."""
    path = Path(path)
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)
