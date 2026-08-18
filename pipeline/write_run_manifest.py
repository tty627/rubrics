"""Write a reproducible manifest for the current pipeline run."""
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "outputs" / "current"


def task_message_counts(path):
    counts = {}
    try:
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                status = json.loads(line).get("task_message_status", "missing")
                counts[status] = counts.get(status, 0) + 1
    except FileNotFoundError:
        pass
    return counts


def count_jsonl(path):
    try:
        with open(path, encoding="utf-8") as stream:
            return sum(1 for line in stream if line.strip())
    except FileNotFoundError:
        return 0


def main():
    current = CURRENT
    current.mkdir(parents=True, exist_ok=True)
    run_id = os.environ.get("RP_RUN_ID") or datetime.now().strftime("%Y%m%dT%H%M%S")
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                         text=True).strip()
    except Exception:
        commit = "unknown"
    manifest = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": commit,
        "source_jsonl": os.environ.get("RP_SOURCE_JSONL", ""),
        "counts": {
            "tasks": count_jsonl(ROOT / "data/tasks/01_task_dataset.jsonl"),
            "evaluation_tasks": count_jsonl(ROOT / "data/evaluation/20_evaluation_tasks.jsonl"),
            "delivery_tasks": count_jsonl(current / "rubric_delivery.jsonl"),
            "task_message_status": task_message_counts(
                ROOT / "data/tasks/01_task_dataset.jsonl"),
        },
        "artifacts": {
            "delivery": "outputs/current/rubric_delivery.jsonl",
            "internal": "outputs/current/rubric_internal.jsonl",
            "xlsx": "outputs/current/rubric_delivery.xlsx",
            "audit": "data/release/33_delivery_audit.txt",
        },
    }
    target = current / "run_manifest.json"
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    run_dir = ROOT / "outputs/runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_manifest.json").write_text(target.read_text())
    print(target)


if __name__ == "__main__":
    main()
