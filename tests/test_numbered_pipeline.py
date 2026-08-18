import importlib.util
import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NumberedPipelineTests(unittest.TestCase):
    def test_numbered_entries_exist(self):
        names = [
            "00_run_all.sh", "01_run_task_preparation.sh",
            "02_run_rubric_generation.sh", "03_run_response_evaluation.sh",
            "04_run_release_verification.sh", "01_build_task_dataset.py",
            "02_filter_tasks.py", "03_extract_task_context.py",
            "04_classify_task_type.py", "05_generate_evaluation_axes.py",
            "06_generate_rubric_draft.py", "07_diagnose_rubric.py",
            "08_apply_rubric_diagnosis.py", "09_rewrite_rubric_criteria.py",
            "10_classify_negative_criteria.py", "11_export_rubric_delivery.py",
            "20_resolve_canonical_answers.py", "20_select_evaluation_tasks.py",
            "21_build_response_pool.py",
            "22_score_response_pool.py", "23_diagnose_rubric_discrimination.py",
            "24_revise_rubric_from_measurement.py", "25_select_rubric_revision.py", "26_build_evaluation_delivery_source.py",
            "30_score_draft_rubric.py", "31_compare_rubric_versions.py",
            "32_export_rubric_xlsx.py", "33_audit_rubric_delivery.py",
        ]
        self.assertEqual([], [name for name in names if not (ROOT / "pipeline" / name).is_file()])

    def test_shell_entries_parse(self):
        scripts = sorted((ROOT / "pipeline").glob("*.sh"))
        result = subprocess.run(["bash", "-n", *map(str, scripts)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_canonical_paths_have_no_dataset_counts(self):
        spec = importlib.util.spec_from_file_location("pipeline_paths", ROOT / "lib/paths.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        paths = [*module.TASK_FILES.values(), *module.RUBRIC_FILES.values(),
                 *module.EVALUATION_FILES.values(), *module.RELEASE_FILES.values(),
                 *module.DELIVERY_FILES.values()]
        for path in paths:
            self.assertNotRegex(path.name, r"(?:388|452)")

    def test_candidate_response_isolation(self):
        from stages import s01_filter, s02_context, s03_perspective, s04_rubric
        secret = "SECRET_CANDIDATE_RESPONSE"
        record = {
            "rid": "q-test", "question": "解释测试题", "subject": ["测试"],
            "ref_responses": {"candidate": secret}, "ref_errors": [secret],
            "intent": "解释题目", "implicit_constraints": {},
            "question_type": "open", "rubric_form": "analytic",
            "perspectives": [{"perspective_id": "q-test-p1", "name": "内容", "desc": "解释"}],
        }
        filter_messages, _ = s01_filter.build(record)
        messages = [filter_messages, s02_context.build(record),
                    [{"role": "user", "content": s03_perspective._ctx(record)}],
                    s04_rubric.build(record)]
        text = json.dumps(messages, ensure_ascii=False)
        self.assertNotIn(secret, text)

    def test_source_messages_preserve_constraints_without_assistant(self):
        from lib import task_input
        secret = "SECRET_ASSISTANT_CANDIDATE"
        source = [{"role": "system", "content": "必须输出表格"},
                  {"role": "user", "content": "分析主题"},
                  {"role": "assistant", "content": secret}]
        kept = task_input.task_messages(source)
        record = task_input.attach_source_messages(
            {"question": "分析主题"}, {"分析主题": [{"task_messages": kept,
                                                   "source_session_id": "s1"}]})
        context = task_input.prompt_context(record)
        self.assertIn("必须输出表格", context)
        self.assertIn("分析主题", context)
        self.assertNotIn(secret, context)
        self.assertEqual("matched", record["task_message_status"])

    def test_independent_answer_prompt_excludes_candidate_responses(self):
        path = ROOT / "pipeline/20_resolve_canonical_answers.py"
        spec = importlib.util.spec_from_file_location("answer_stage", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        secret = "SECRET_CANDIDATE_ANSWER"
        record = {"question": "2+2=?", "task_messages": [{"role": "user", "content": "2+2=?"}],
                  "ref_responses": {"candidate": secret}}
        text = json.dumps(module.build(record), ensure_ascii=False)
        self.assertNotIn(secret, text)

    def test_programmatic_judge_requires_independent_answer_source(self):
        source = (ROOT / "stages/s12_judge.py").read_text()
        self.assertIn("r.get('answer_source') == 'independent_solver'", source)

    def test_source_index_marks_ambiguous_matches(self):
        import tempfile
        from lib import task_input
        rows = [
            {"session_id": "a", "messages": [{"role": "user", "content": "same task"}]},
            {"session_id": "b", "messages": [{"role": "user", "content": "same   task"}]},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            index = task_input.load_source_index(path)
            record = task_input.attach_source_messages({"question": "same task"}, index)
        self.assertEqual("ambiguous", record["task_message_status"])
        self.assertEqual([], record["task_messages"])

    def test_unique_truncated_prefix_matches_source(self):
        from lib import task_input
        prefix = "x" * 7900
        index = {prefix + " source tail": [{"task_messages": [{"role": "user", "content": prefix + " source tail"}],
                                             "source_session_id": "s-prefix"}]}
        record = task_input.attach_source_messages({"question": prefix}, index)
        self.assertEqual("matched", record["task_message_status"])
        self.assertEqual("s-prefix", record["source_session_id"])

    def test_question_only_fallback_is_explicit(self):
        from lib import task_input
        record = task_input.attach_source_messages({"question": "fallback task"}, {})
        self.assertEqual("question_only", record["task_message_status"])
        self.assertEqual(["fallback task"], record["user_messages"])

    def test_controlled_stage20_to_stage26_flow(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            fallback = [
                {"rid": "q1", "question": "one", "ref_responses": {"a": "A", "b": "B"},
                 "rubrics": [{"criteria": "old"}]},
                {"rid": "q2", "question": "two", "ref_responses": {"a": "A"},
                 "rubrics": [{"criteria": "fallback"}]},
            ]
            measured = [{"rid": "q1", "question": "one", "rubrics": [{"criteria": "measured"}],
                         "judged": {"strong": {}}, "pool": [], "consequential": {}}]
            def write(path, rows):
                path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
            source, selected, measured_path, merged = [tmp / name for name in
                ("source.jsonl", "selected.jsonl", "measured.jsonl", "merged.jsonl")]
            write(source, fallback)
            write(measured_path, measured)
            select = subprocess.run([
                "python3", str(ROOT / "pipeline/20_select_evaluation_tasks.py"),
                "--src", str(source), "--out", str(selected)], capture_output=True, text=True)
            self.assertEqual(0, select.returncode, select.stderr)
            self.assertEqual(["q1"], [json.loads(line)["rid"] for line in selected.read_text().splitlines()])
            merge = subprocess.run([
                "python3", str(ROOT / "pipeline/26_build_evaluation_delivery_source.py"),
                "--measured", str(measured_path), "--fallback", str(source), "--out", str(merged)],
                capture_output=True, text=True)
            self.assertEqual(0, merge.returncode, merge.stderr)
            rows = [json.loads(line) for line in merged.read_text().splitlines()]
            self.assertEqual(["q1", "q2"], [row["rid"] for row in rows])
            self.assertEqual("measured", rows[0]["rubrics"][0]["criteria"])
            self.assertNotIn("judged", rows[0])
            self.assertEqual("fallback", rows[1]["rubrics"][0]["criteria"])
            compare = subprocess.run([
                "python3", str(ROOT / "pipeline/compare_jsonl.py"), str(merged), str(merged)],
                capture_output=True, text=True)
            self.assertEqual(0, compare.returncode, compare.stderr)

    def test_filter_prompt_excludes_later_user_materials(self):
        from lib import task_input
        record = {"question": "main task", "task_messages": [
            {"role": "system", "content": "format constraint"},
            {"role": "user", "content": "main task"},
            {"role": "user", "content": "LATER_RESEARCH_MATERIAL"},
        ]}
        text = task_input.filter_prompt_context(record)
        self.assertIn("format constraint", text)
        self.assertIn("main task", text)
        self.assertNotIn("LATER_RESEARCH_MATERIAL", text)


if __name__ == "__main__":
    unittest.main()
