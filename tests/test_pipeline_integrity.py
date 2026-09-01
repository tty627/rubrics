import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import stage


def load_stage(name):
    """按文件名加载编号阶段模块（模块名以数字开头，不能用 import）。"""
    spec = importlib.util.spec_from_file_location(
        'stage_' + name.replace('.py', ''), ROOT / 'pipeline' / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


diagnose = load_stage('23_diagnose_rubric_discrimination.py').diagnose
_select = load_stage('25_select_rubric_revision.py')


class PipelineIntegrityTests(unittest.TestCase):
    def test_stage_errors_are_append_only_and_deduplicated(self):
        original = {'rid': 'q0001', '_stage_errors': [
            stage.error_entry('s01', 'q0001', 'old failure')
        ]}
        updated = stage.add_stage_error(original, 's12L', 'strong', 'new failure')
        updated = stage.add_stage_error(updated, 's12L', 'strong', 'new failure')

        self.assertEqual(len(original['_stage_errors']), 1)
        self.assertEqual(len(updated['_stage_errors']), 2)
        self.assertEqual(updated['_stage_errors'][-1]['key'], 'strong')
        self.assertEqual(stage.errors_by_index([(3, ValueError('bad'))]),
                         {3: 'bad'})
        with tempfile.TemporaryDirectory() as td:
            manifest = Path(td) / 'failures.jsonl'
            old_manifest = os.environ.get('RP_FAILURE_MANIFEST')
            os.environ['RP_FAILURE_MANIFEST'] = str(manifest)
            try:
                stage.write_failure_manifest(
                    'test-stage', [{'rid': 'q0001'}, ('q0002', 'strong')],
                    [(0, 'timeout'), (1, RuntimeError('bad json'))])
            finally:
                if old_manifest is None:
                    os.environ.pop('RP_FAILURE_MANIFEST', None)
                else:
                    os.environ['RP_FAILURE_MANIFEST'] = old_manifest
            failures = [json.loads(line) for line in manifest.read_text(encoding='utf-8').splitlines()]
            self.assertEqual([x['key'] for x in failures], ['q0001', 'q0002/strong'])
            self.assertEqual(failures[0]['stage'], 'test-stage')

    def test_missing_pool_role_fails_instead_of_falling_back(self):
        """回复池档位角色缺失必须报错，不许静默退回 judge/generator。

        静默退回会让 mid 档与 strong 档同模型，档序失效且不可见 —— 388 全量
        实测踩过（65% 的题 mid ≤ weak）。缺配置就停，别猜。
        """
        model = type('Model', (), {'name': 'by-pool-mid'})()

        def by_role(role, path=None):
            return {'pool_mid': [model], 'judge': [model]}[role]

        with mock.patch.dict(os.environ, {'RP_TEST_POOL_MODEL': ''}), \
                mock.patch.object(stage.config, 'by_role', side_effect=by_role):
            self.assertIs(stage.pick('RP_TEST_POOL_MODEL', 'pool_mid'), model)

        def empty(role, path=None):
            return []

        with mock.patch.dict(os.environ, {'RP_TEST_POOL_MODEL': ''}), \
                mock.patch.object(stage.config, 'by_role', side_effect=empty):
            with self.assertRaises(ValueError):
                stage.pick('RP_TEST_POOL_MODEL', 'pool_mid')

    def test_configured_models_satisfy_role_hard_constraints(self):
        """config/models.json 若存在，必须自带全部必需角色且判分器异源。"""
        from lib import config
        path = ROOT / 'config/models.json'
        if not path.exists():
            self.skipTest('无本地 models.json')
        # check=False 才能拿到完整错误列表；check=True 会在第一次校验时抛异常。
        self.assertEqual([], config.inspect(config.load(path, check=False)))

    def test_consequential_skips_missing_or_failed_tiers(self):
        base = {
            'rid': 'q0001',
            'pool': [{'tier': 'strong'}, {'tier': 'weak'}],
            'judged': {
                'strong': {'rate': 0.8, 'raw_rate': 0.8, 'items': []},
            },
        }
        self.assertIn('判分档缺失', diagnose(base)['skip_reason'])

        failed = {
            **base,
            'judged': {
                'strong': {'rate': 0.8, 'raw_rate': 0.8, 'items': []},
                'weak': {'rate': None, 'raw_rate': None, 'items': [],
                         'judge_error': 'timeout', 'judge_incomplete': True},
            },
        }
        self.assertIn('判分失败', diagnose(failed)['skip_reason'])

    def test_gated_answer_correct_weak_does_not_pollute_gap(self):
        def judged(rate):
            return {'rate': rate, 'raw_rate': rate, 'items': [], 'vetoed': False}

        record = {
            'rid': 'q0001',
            'rubric_form': 'gated_answer',
            'pool': [{'tier': 'strong'}, {'tier': 'weak'}],
            'judged': {'strong': judged(0.8), 'weak': judged(0.9)},
        }
        result = diagnose(record)
        self.assertIsNone(result['low_signal']['weak_mean'])
        self.assertIsNone(result['low_signal']['gap'])
        self.assertTrue(result['low_signal']['no_weak'])

    def test_revision_selection_prefers_fewest_defects_then_later_round(self):
        """选优必须单调：缺陷少者优先，同分取靠后轮次（表述打磨更多）。

        skip（本轮测不出结论）的代价刻意落在 1 与 2 之间：干净版本必须赢过它，
        它必须赢过「2 个以上缺陷」，但输给「只有 1 个缺陷」—— 选 skip 等于交付
        质量未知的 rubric，而单缺陷版本的缺陷是记录在案、下游看得见的。
        """
        self.assertLess(_select.cost(set()), _select.cost({'skip'}))
        self.assertLess(_select.cost({'H'}), _select.cost({'skip'}))
        self.assertLess(_select.cost({'skip'}), _select.cost({'H', 'F'}))
        self.assertEqual({'H'}, _select.flags({'hackable': {'is_defective': True}}))
        self.assertEqual({'F'}, _select.flags({'calibration': {'issue': 'floor'}}))
        self.assertEqual({'skip'}, _select.flags({'skip_reason': '无法判定'}))

    def test_round_defaults_point_at_numbered_diagnostics(self):
        """轮次默认值必须指向编号产物，且不带写死的题量。"""
        for name in _select.ROUNDS:
            self.assertTrue(name.startswith('evaluation/23_'), name)
            self.assertNotRegex(name, r'(?:388|452)')

    def test_release_gate_rejects_incomplete_pool(self):
        """阶段 04 的结构闸门必须拦下缺档的回复池。"""
        out = self._run_release_gate(
            pool=[{'rid': 'q0001', 'pool': [
                {'tier': t} for t in ('strong', 'mid', 'trunc', 'cut', 'weak')]}],
            scores=[{'rid': 'q0001', 'judged': {
                t: {} for t in ('strong', 'mid', 'trunc', 'cut', 'weak')}}])
        self.assertNotEqual(0, out.returncode)
        self.assertIn('回复池缺档', out.stdout + out.stderr)

    def test_release_gate_accepts_gated_four_tiers(self):
        """gated_answer 只造四档；闸门不得按六档误杀（q0215 类平凡可核验题）。"""
        four = ('strong', 'mid', 'weak', 'adv')
        out = self._run_release_gate(
            pool=[{'rid': 'q0001', 'rubric_form': 'gated_answer',
                   'pool': [{'tier': t} for t in four]}],
            scores=[{'rid': 'q0001', 'judged': {t: {} for t in four}}])
        text = out.stdout + out.stderr
        self.assertNotIn('回复池缺档', text)
        self.assertIn('档位齐全', text)

    def test_release_gate_still_requires_gated_core_tiers(self):
        """gated 豁免 trunc/cut，不豁免 weak/adv。"""
        out = self._run_release_gate(
            pool=[{'rid': 'q0001', 'rubric_form': 'gated_answer',
                   'pool': [{'tier': t} for t in ('strong', 'mid', 'adv')]}],
            scores=[{'rid': 'q0001', 'judged': {
                t: {} for t in ('strong', 'mid', 'adv')}}])
        self.assertNotEqual(0, out.returncode)
        self.assertIn('回复池缺档', out.stdout + out.stderr)

    def test_gated_short_strong_is_not_degenerate(self):
        """可核验题正确短答是有效 strong，篇幅下限会误杀「你好世界」这类题。"""
        stage21 = load_stage('21_build_response_pool.py')
        gated, why = stage21.strong_degenerate(
            {'rid': 'q0215', 'rubric_form': 'gated_answer'}, '你好世界')
        self.assertFalse(gated)
        self.assertEqual('', why)
        open_deg, open_why = stage21.strong_degenerate(
            {'rid': 'q0007', 'rubric_form': 'analytic'}, '短')
        self.assertTrue(open_deg)
        self.assertIn('短于下限', open_why)

    def _run_release_gate(self, pool, scores):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for sub in ('tasks', 'rubric', 'evaluation', 'release'):
                (root / sub).mkdir()
            rows = {
                'evaluation/20_evaluation_tasks.jsonl': [{'rid': 'q0001'}],
                'evaluation/21_response_pool.jsonl': pool,
                'evaluation/22_response_scores.jsonl': scores,
                'evaluation/25_selected_rubrics.jsonl': [{'rid': 'q0001'}],
            }
            for name, value in rows.items():
                (root / name).write_text(
                    ''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in value),
                    encoding='utf-8')
            env = os.environ.copy()
            env['RP_DATA_ROOT'] = str(root)
            return subprocess.run(
                ['bash', 'pipeline/04_run_release_verification.sh'], cwd=ROOT,
                env=env, text=True, capture_output=True)
