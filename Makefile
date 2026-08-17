# 常用入口（纯标准库，无安装步骤；全部在仓库根目录执行）
.PHONY: check seed phase4 checkpoint2 export

check:            ## 静态检查 + 语义核心单测（零 LLM）
	python3 -m py_compile stages/*.py scripts/*.py lib/*.py tests/*.py
	python3 tests/test_rubric.py

seed:             ## Phase 0：xlsx → 种子集 + 草稿基线
	python3 stages/s00_seed.py

phase4:           ## Phase 4 实测全量（LLM 密集）
	bash scripts/rerun_phase4.sh

checkpoint2:      ## 检查点 2：新 vs 草稿 pairwise 放行闸门
	bash scripts/rerun_checkpoint2.sh

export:           ## 交付档 + 内部档 + xlsx（依赖流水线末端数据）
	python3 scripts/export_advisor_schema.py --src data/s11e_all452.jsonl --full
	python3 scripts/fill_xlsx_preserve_format.py
