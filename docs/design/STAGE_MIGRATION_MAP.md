# Stage Migration Map

| Legacy name | Numbered name | Responsibility |
|---|---|---|
| s00_seed.py | 01_build_task_dataset.py | Build task dataset |
| s01_filter.py | 02_filter_tasks.py | Filter tasks |
| s02_context.py | 03_extract_task_context.py | Extract explicit context |
| s02b_route.py | 04_classify_task_type.py | Classify rubric form |
| s03_perspective.py | 05_generate_evaluation_axes.py | Generate evaluation axes |
| s04_rubric.py | 06_generate_rubric_draft.py | Generate draft rubric |
| s11_diagnose.py | 07_diagnose_rubric.py | Diagnose rubric quality |
| s11b_remedy.py | 08_apply_rubric_diagnosis.py | Apply diagnosis decisions |
| s04b_split.py | 09_rewrite_rubric_criteria.py | Split and rewrite criteria |
| s04c_severity.py | 10_classify_negative_criteria.py | Classify penalties and veto |
| s10_pool.py | 21_build_response_pool.py | Build measured response pool |
| s12_judge.py | 22_score_response_pool.py | Score responses |
| s11c_consequential.py | 23_diagnose_rubric_discrimination.py | Diagnose measured discrimination |
| s11d_remedy.py | 24_revise_rubric_from_measurement.py | Revise rubric from evidence |
| s11e_select.py | 25_select_rubric_revision.py | Select best measured revision |
| s12b_draft_judge.py | 30_score_draft_rubric.py | Score baseline draft rubric |
| s12c_pairwise.py | 31_compare_rubric_versions.py | Pairwise release comparison |
| fill_xlsx_preserve_format.py | 32_export_rubric_xlsx.py | Export xlsx |
| audit_rubrics.py | 33_audit_rubric_delivery.py | Audit delivery |

Legacy names remain temporary compatibility entry points. New documentation and commands use numbered names only.
