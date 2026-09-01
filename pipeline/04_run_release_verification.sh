#!/bin/bash
# 阶段 04：交付前结构闸门 + 导出 + 审计。
#
# 无 checkpoint 2：它的定义是「新 rubric vs 草稿 rubric 的 pairwise 一致率」，
# 本流水线从题目零起点生成，不存在草稿 rubric，该闸门没有比对对象。
# 放行依据改为实测证据（阶段 03）加下面的结构完整性校验。
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
mkdir -p data/release outputs/current outputs/excel cache/numbered
export RP_CACHE=${RP_CACHE:-$ROOT/cache/numbered}
export RP_DATA_ROOT=${RP_DATA_ROOT:-$ROOT/data}

echo '[1/4] 结构完整性闸门...'
python3 - <<'PY'
import json
import sys
sys.path.insert(0, '.')
from lib import paths

REQUIRED_TIERS = {'strong', 'mid', 'trunc', 'cut', 'weak', 'adv'}
# gated_answer 只造四档：trunc/cut 是结构性弱档，对「删论点/截断」无效的可核验题
# 不适用（阶段 21 同口径）。闸门必须与造法一致，否则 gated 题恒被误判缺档。
GATED_TIERS = {'strong', 'mid', 'weak', 'adv'}


def rows(path):
    if not path.exists():
        sys.exit(f'  ✗ 缺少产物：{paths.relative(path)}')
    with path.open(encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


tasks = rows(paths.EVALUATION_FILES['tasks'])
pool = {r['rid']: r for r in rows(paths.EVALUATION_FILES['pool'])}
scores = {r['rid']: r for r in rows(paths.EVALUATION_FILES['scores'])}
selected = {r['rid']: r for r in rows(paths.EVALUATION_FILES['selected'])}
expected = {r['rid'] for r in tasks}
if not expected:
    sys.exit('  ✗ 没有可测题目')

for label, got in (('回复池', pool), ('判分', scores), ('终态', selected)):
    missing = sorted(expected - set(got))
    if missing:
        sys.exit(f'  ✗ {label}缺题：{missing[:8]}')

for rid in sorted(expected):
    required = GATED_TIERS if pool[rid].get('rubric_form') == 'gated_answer' \
        else REQUIRED_TIERS
    tiers = {p.get('tier') for p in (pool[rid].get('pool') or [])}
    tiers.update((pool[rid].get('pool_errors') or {}).keys())
    if required - tiers:
        sys.exit(f'  ✗ {rid} 回复池缺档：{sorted(required - tiers)}')
    ungraded = tiers - set(scores[rid].get('judged') or {})
    if ungraded:
        sys.exit(f'  ✗ {rid} 判分缺档：{sorted(ungraded)}')

print(f'  ✓ {len(expected)} 题产物完整，档位齐全')
PY

echo '[2/4] 数据边界断言（冻结前不得引入候选回答）...'
python3 - <<'PY'
import json
import sys
sys.path.insert(0, '.')
from lib import paths

# 冻结前的每个阶段产物都不得出现候选回答文本。逐题比对：题目自带的
# ref_responses 内容若出现在 rubric 准则里，说明生成链读了待评对象。
frozen = paths.RUBRIC_FILES['frozen']
if not frozen.exists():
    sys.exit(f'  ✗ 缺少产物：{paths.relative(frozen)}')

leaked = []
with frozen.open(encoding='utf-8') as f:
    for line in f:
        if not line.strip():
            continue
        r = json.loads(line)
        refs = [v for v in (r.get('ref_responses') or {}).values()
                if isinstance(v, str) and len(v.strip()) >= 40]
        if not refs:
            continue
        text = json.dumps(r.get('rubrics') or [], ensure_ascii=False)
        for ref in refs:
            # 取候选回答中一段足够长的连续片段作指纹，避免常见短语误报。
            probe = ref.strip()[:60]
            if probe and probe in text:
                leaked.append(r.get('rid'))
                break

if leaked:
    sys.exit(f'  ✗ {len(leaked)} 题的 rubric 含候选回答文本：{leaked[:8]}')
print('  ✓ 冻结前 rubric 无候选回答泄漏')
PY

echo '[3/4] 导出 xlsx...'
RP_FILL_SRC=outputs/current/rubric_delivery.jsonl python3 pipeline/30_export_rubric_xlsx.py
LATEST_XLSX=$(ls -1t outputs/excel/*.xlsx | head -1)
cp "$LATEST_XLSX" outputs/current/rubric_delivery.xlsx

echo '[4/4] 审计 + 单测...'
python3 pipeline/31_audit_rubric_delivery.py outputs/current/rubric_delivery.jsonl \
    > data/release/31_delivery_audit.txt || true
python3 -m unittest discover -s tests
