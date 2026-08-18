import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import stage
from stages.s11c_consequential import diagnose
from stages.s11e_select import canonical_round_name
from stages.s12b_draft_judge import draft_rubrics
from stages.s12c_pairwise import state_of


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

    def test_draft_without_rubrics_is_explicitly_empty(self):
        self.assertEqual(draft_rubrics({'rid': 'q0001', 'draft_rubric': {}}), [])
        self.assertEqual(draft_rubrics({'rid': 'q0001'}), [])

    def test_round_name_compatibility(self):
        self.assertEqual(canonical_round_name('s11Lc_r2.jsonl'), 's11c_r2.jsonl')
        self.assertEqual(state_of(0.8, 0.2), 'win')
        self.assertEqual(state_of(0.2, 0.8), 'rev')
        self.assertEqual(state_of(0.5, 0.5), 'tie')

    def test_empty_checkpoint_writes_exclusions_and_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            records = [{
                'rid': 'q0001',
                '_s11Le': {'chosen_round': 's11c_cons388.jsonl'},
            }]
            round_record = [{
                'rid': 'q0001',
                'judged': {
                    'strong': {'rate': 0.8, 'raw_rate': 0.8},
                    'weak': {'rate': 0.2, 'raw_rate': 0.2},
                },
            }]
            draft_record = [{
                'rid': 'q0001',
                '_checkpoint2': {'excluded': True, 'exclude_reason': '缺草稿 rubric'},
                'draft_judged': {},
            }]
            for name, value in (
                ('s11e_all452.jsonl', records),
                ('s11c_cons388.jsonl', round_record),
                ('s12b_draft388.jsonl', draft_record),
            ):
                (root / name).write_text(
                    ''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in value),
                    encoding='utf-8')

            output = root / 's12c_pairwise.jsonl'
            env = os.environ.copy()
            env['RP_OUT'] = str(root)
            env['RP_S12LC_OUT'] = str(output)
            result = subprocess.run(
                [sys.executable, 'stages/s12c_pairwise.py'],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            row = json.loads(output.read_text(encoding='utf-8').splitlines()[0])
            self.assertTrue(row['excluded'])
            self.assertIn('草稿侧已排除', row['exclude_reason'])


if __name__ == '__main__':
    unittest.main()
