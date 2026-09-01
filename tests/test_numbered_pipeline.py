import importlib.util
import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_stage(name):
    """按文件名加载编号阶段模块（模块名以数字开头，不能用 import）。"""
    spec = importlib.util.spec_from_file_location(
        "stage_" + name.replace(".py", ""), ROOT / "pipeline" / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NumberedPipelineTests(unittest.TestCase):
    def test_numbered_entries_exist(self):
        names = [
            "00_run_all.sh", "01_run_task_preparation.sh",
            "02_run_rubric_generation.sh", "03_run_response_evaluation.sh",
            "04_run_release_verification.sh", "01_build_task_dataset.py",
            "02_filter_tasks.py", "03_extract_task_context.py",
            "04_classify_task_type.py", "05_generate_evaluation_axes.py",
            "06_generate_rubric.py", "07_diagnose_rubric.py",
            "08_apply_rubric_diagnosis.py", "09_rewrite_rubric_criteria.py",
            "10_classify_negative_criteria.py",
            "20_resolve_canonical_answers.py", "21_build_response_pool.py",
            "22_score_response_pool.py", "23_diagnose_rubric_discrimination.py",
            "24_revise_rubric_from_measurement.py", "25_select_rubric_revision.py",
            "26_build_evaluation_delivery_source.py", "27_export_rubric_delivery.py",
            "30_export_rubric_xlsx.py", "31_audit_rubric_delivery.py",
        ]
        self.assertEqual([], [name for name in names if not (ROOT / "pipeline" / name).is_file()])

    def test_every_stage_imports(self):
        """每个阶段都必须能 import —— 光 py_compile 抓不到死导入。

        `stages/` 删掉后，24/10/31 三个阶段仍在 `from stages import ...`，
        语法完全合法、编译通过，只有真正加载时才 ImportError。这条守住的是
        「删了目录但没删引用」这类只在运行时暴露的断链。
        """
        broken = []
        for path in sorted((ROOT / "pipeline").glob("[0-9]*.py")):
            try:
                load_stage(path.name)
            except Exception as exc:
                broken.append(f"{path.name}: {type(exc).__name__}: {exc}")
        self.assertEqual([], broken)

    def test_shell_runners_reference_existing_stages(self):
        """runner 里 `python3 pipeline/X.py` 的 X 必须真实存在。

        `bash -n` 只查语法，脚本引用不存在的文件它一声不响。02_run_rubric_generation.sh
        曾长期调用 `06_generate_rubric_draft.py` 和 `11_export_rubric_delivery.py`
        两个已不存在的文件 —— 编号重构时改了文件名没改 runner，只有真跑才会
        `can't open file`。这条守住 runner 与 pipeline/ 的一致性。
        """
        missing = []
        for script in sorted((ROOT / "pipeline").glob("*.sh")):
            for ref in re.findall(r"python3 (pipeline/[\w./]+\.py)", script.read_text()):
                if not (ROOT / ref).is_file():
                    missing.append(f"{script.name} -> {ref}")
        self.assertEqual([], missing)

    def test_full_export_never_targets_the_delivery_path(self):
        """`--full` 出的是内部档，绝不能落在交付档路径上。

        阶段 27 两种模式都只写 `--out`。若调用方给 `--out rubric_delivery.jsonl --full`，
        交付档就变成带 `_` 血缘字段的内部档 —— 血缘外泄给交付方，同时
        rubric_internal.jsonl 空缺（manifest 和 lib/paths 都指着它）。
        """
        bad = []
        for path in [*(ROOT / "pipeline").glob("*.sh"), ROOT / "Makefile"]:
            for line in path.read_text().splitlines():
                if "27_export_rubric_delivery.py" not in line or "--full" not in line:
                    continue
                out = re.search(r"--out\s+(\S+)", line)
                if out and "rubric_delivery" in out.group(1):
                    bad.append(f"{path.name}: {out.group(1)}")
        self.assertEqual([], bad)

    def test_frozen_rubric_is_produced_before_evaluation_needs_it(self):
        """阶段 02 必须产出阶段 03 要的冻结文件，否则实测线第一步就退出。"""
        generation = (ROOT / "pipeline/02_run_rubric_generation.sh").read_text()
        evaluation = (ROOT / "pipeline/03_run_response_evaluation.sh").read_text()
        frozen = re.search(r"FROZEN=(\S+)", evaluation).group(1)
        # 必须是写入目标（cp/> 的右侧或 RP_*_OUT），光在 echo 或注释里提一句不算。
        written = re.search(rf"(?:cp\s+\S+\s+|>\s*|_OUT=){re.escape(frozen)}\b", generation)
        self.assertIsNotNone(written, f"阶段 02 没有写出 {frozen}")

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
        filter_stage = load_stage("02_filter_tasks.py")
        context_stage = load_stage("03_extract_task_context.py")
        axes_stage = load_stage("05_generate_evaluation_axes.py")
        rubric_stage = load_stage("06_generate_rubric.py")
        secret = "SECRET_CANDIDATE_RESPONSE"
        record = {
            "rid": "q-test", "question": "解释测试题", "subject": ["测试"],
            "ref_responses": {"candidate": secret}, "ref_errors": [secret],
            "intent": "解释题目", "implicit_constraints": {},
            "question_type": "open", "rubric_form": "analytic",
            "perspectives": [{"perspective_id": "q-test-p1", "name": "内容", "desc": "解释"}],
        }
        messages = [filter_stage.build(record), context_stage.build(record),
                    [{"role": "user", "content": axes_stage._ctx(record)}],
                    rubric_stage.build(record)]
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
        source = (ROOT / "pipeline/22_score_response_pool.py").read_text()
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

    def test_controlled_stage26_merge_flow(self):
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
            source, measured_path, merged = [tmp / name for name in
                ("source.jsonl", "measured.jsonl", "merged.jsonl")]
            write(source, fallback)
            write(measured_path, measured)
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

    def test_response_pool_generates_strong_instead_of_reusing_candidates(self):
        """strong 档必须现场生成：拿题目自带候选回答当上界＝用被测对象当测量基准。"""
        source = (ROOT / "pipeline/21_build_response_pool.py").read_text()
        self.assertNotIn("def strong_of", source)
        self.assertNotIn("pool_shared", source)
        # 六档都必须在生成任务里，不能有任何一档来自 ref_responses
        self.assertIn("for tier in ('strong', 'mid', 'weak', 'adv')", source)

    def test_delivery_merge_flags_unmeasured_instead_of_excusing_it(self):
        """缺实测的题必须标 measured=False，而不是伪装成「未参与」的正常状态。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            paths = {name: tmp / f"{name}.jsonl"
                     for name in ("measured", "fallback", "out")}
            def write(path, rows):
                path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                                        for r in rows))
            write(paths["fallback"], [{"rid": "q1", "rubrics": []},
                                      {"rid": "q2", "rubrics": []}])
            write(paths["measured"], [{"rid": "q1", "rubrics": [], "judged": {"strong": {}}}])
            run = subprocess.run([
                "python3", str(ROOT / "pipeline/26_build_evaluation_delivery_source.py"),
                "--measured", str(paths["measured"]), "--fallback", str(paths["fallback"]),
                "--out", str(paths["out"])], capture_output=True, text=True)
            self.assertEqual(0, run.returncode, run.stderr)
            rows = {r["rid"]: r for r in
                    (json.loads(l) for l in paths["out"].read_text().splitlines())}
        self.assertTrue(rows["q1"]["measured"])
        self.assertNotIn("judged", rows["q1"])
        self.assertFalse(rows["q2"]["measured"])
        self.assertIn("measured_missing_reason", rows["q2"])

    def test_canonical_answer_requires_unanimous_cross_check(self):
        stage = load_stage("20_resolve_canonical_answers.py")
        ground = type("M", (), {"name": "by-ground", "family": "anthropic"})()
        check_a = type("M", (), {"name": "by-judge", "family": "google"})()
        check_b = type("M", (), {"name": "by-gen", "family": "openai"})()
        record = {"rid": "q1", "question": "2+2=?", "question_type": "verifiable",
                  "task_messages": [{"role": "user", "content": "2+2=?"}]}

        def answers(*canonicals):
            queue = list(canonicals)

            def solve(rec, model):
                return {"model": model.name, "family": model.family, "answer": "4",
                        "answer_kind": "numeric", "answer_canonical": queue.pop(0),
                        "answer_confidence": "high", "answer_evidence": "", "_meta": {}}
            return solve

        # 三方一致 → 准入
        original = stage.solve
        try:
            stage.solve = answers("4", "4", "4")
            ok = stage.resolve_record(record, ground, [check_a, check_b])
            self.assertTrue(ok["answer_admitted"])
            self.assertEqual("4", ok["answer_canonical"])
            self.assertNotIn("answer_dispute", ok)

            # 多数票成立但非全一致 → 挂起，canonical 不下发
            stage.solve = answers("4", "4", "5")
            bad = stage.resolve_record(record, ground, [check_a, check_b])
            self.assertFalse(bad["answer_admitted"])
            self.assertEqual("", bad["answer_canonical"])
            self.assertTrue(bad["answer_dispute"])
        finally:
            stage.solve = original

    def test_grounder_must_be_closed_source_family(self):
        from lib import config
        base = {"model_id": "m", "base_url": "http://x/v1", "roles": ["grounder"]}
        models = {"g": type("M", (), {**base, "name": "g", "family": "qwen",
                                      "roles": ["grounder"]})()}
        errors = config.inspect(models, allow_open_grounder=False)
        self.assertTrue(any("闭源" in e for e in errors), errors)

    def test_prep_runner_never_clobbers_fresh_task_outputs(self):
        """阶段 01/02 直写编号路径；runner 不得把旧种子/旧过滤结果回填覆盖它们。

        曾犯：else 分支 `cp data/seed.jsonl data/tasks/01_task_dataset.jsonl` 和
        `cp data/s01_filter.jsonl data/tasks/02_filtered_tasks.jsonl` 把 10 条新产物
        覆盖成 237 条旧种子（旧种子含 ref_responses，且绕过 stage 01 的清洗）。
        这两行只有在真跑端到端时才暴露，`bash -n` 和阶段引用检查都抓不到。
        """
        runner = (ROOT / "pipeline/01_run_task_preparation.sh").read_text()
        self.assertNotIn("cp data/seed.jsonl data/tasks/01_task_dataset.jsonl", runner)
        self.assertNotIn("cp data/s01_filter.jsonl data/tasks/02_filtered_tasks.jsonl", runner)

    def test_stage05_reads_explicit_constraints_from_stage03(self):
        """阶段 03 产出 explicit_constraints，阶段 05 必须读它而非已废弃的 implicit_constraints。

        字段名漂移是静默 bug：阶段 05 用 .get("implicit_constraints", {}) 恒取空字典，
        题面明确约束根本到不了评价视角生成，不崩但数据丢失。
        """
        s03 = (ROOT / "pipeline/03_extract_task_context.py").read_text()
        s05 = (ROOT / "pipeline/05_generate_evaluation_axes.py").read_text()
        self.assertIn("explicit_constraints", s03)
        self.assertIn("explicit_constraints", s05)
        self.assertNotIn("implicit_constraints", s05)

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
