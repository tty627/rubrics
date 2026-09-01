# Numbered pipeline entry points. Run from the repository root.
.PHONY: all tasks rubric evaluate release check export

all:
	bash pipeline/00_run_all.sh

tasks:
	bash pipeline/01_run_task_preparation.sh

rubric:
	bash pipeline/02_run_rubric_generation.sh

evaluate:
	bash pipeline/03_run_response_evaluation.sh

release:
	bash pipeline/04_run_release_verification.sh

check:
	python3 -m py_compile pipeline/*.py lib/*.py tests/*.py
	bash -n pipeline/00_run_all.sh pipeline/01_run_task_preparation.sh pipeline/02_run_rubric_generation.sh pipeline/03_run_response_evaluation.sh pipeline/04_run_release_verification.sh
	python3 -m unittest discover -s tests -v

export:
	python3 pipeline/27_export_rubric_delivery.py --src data/evaluation/26_rubric_delivery_source.jsonl --out outputs/current/rubric_delivery.jsonl
	python3 pipeline/27_export_rubric_delivery.py --src data/evaluation/26_rubric_delivery_source.jsonl --out outputs/current/rubric_internal.jsonl --full
	python3 pipeline/30_export_rubric_xlsx.py
