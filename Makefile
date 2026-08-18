# Numbered pipeline entry points. Run from the repository root.
.PHONY: all tasks rubric evaluate release check seed phase4 checkpoint2 export

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
	python3 -m py_compile pipeline/*.py stages/*.py scripts/*.py lib/*.py tests/*.py
	bash -n pipeline/00_run_all.sh pipeline/01_run_task_preparation.sh pipeline/02_run_rubric_generation.sh pipeline/03_run_response_evaluation.sh pipeline/04_run_release_verification.sh
	python3 -m unittest discover -s tests -v

# Compatibility aliases. Prefer the semantic targets above.
seed: tasks
phase4: evaluate
checkpoint2: release

export:
	python3 scripts/export_advisor_schema.py --src data/evaluation/26_rubric_delivery_source.jsonl --out outputs/current/rubric_delivery.jsonl --full
	python3 pipeline/32_export_rubric_xlsx.py
