# Numbered Pipeline

The current delivery pipeline uses stable numbered stages grouped by responsibility.

## 00: Full pipeline

- 00_run_all.sh: task preparation, rubric generation, response evaluation, and release verification.

## 01-05: Task preparation

1. 01_build_task_dataset.py
2. 02_filter_tasks.py
3. 03_extract_task_context.py
4. 04_classify_task_type.py
5. 05_generate_evaluation_axes.py

Only task text and explicit task constraints may enter these stages. Candidate responses are evaluation objects and must remain isolated.

## 06-11: Rubric generation

6. 06_generate_rubric_draft.py
7. 07_diagnose_rubric.py
8. 08_apply_rubric_diagnosis.py
9. 09_rewrite_rubric_criteria.py
10. 10_classify_negative_criteria.py
11. 11_export_rubric_delivery.py

## 20-26: Response evaluation

20 selects tasks with sufficient evaluation responses. 21 builds the response pool, 22 scores it, 23 diagnoses discrimination, 24 revises from measurements, and 25 selects the best measured revision. 26 is the evaluation-backed delivery source.

## 30-33: Release verification

30 scores the draft rubric, 31 performs pairwise comparison, 32 exports xlsx, and 33 audits delivery artifacts.

## Storage

- data/tasks: task preparation artifacts.
- data/rubric: rubric generation artifacts.
- data/evaluation: response pools, scores, diagnostics, and measured revisions.
- data/release: release comparison and audit artifacts.
- outputs/current: current delivery artifacts.
- outputs/runs/<run_id>: immutable run snapshots and manifest.
- cache/<numbered-stage>: caches produced by new entry points. Existing cache/sXX directories are legacy caches.

Record counts do not appear in filenames. Counts, Git commit, and run id belong in run_manifest.json.
