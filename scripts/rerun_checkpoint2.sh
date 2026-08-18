#!/bin/bash
# Phase 4 检查点 2：新 rubric vs 草稿 rubric 的 pairwise 一致率（放行闸门）。
# 用法: bash scripts/rerun_checkpoint2.sh
#
# 判分器必须异于生成器（硬约束第 2 条），且 config 里第一个 judge 角色
# by-judge（35.220.164.252 代理）自 2026-08-17 起持续 401 —— 固定 cn-judge。

set -e
REPO=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO"

# 判分器：优先 cn-judge；精简开发机明确回退 deepseek，避免日志只显示空值。
cfg_has() { python3 - "$1" <<'PYEOF'
import json, os, sys
cfg = json.load(open(os.environ.get('RP_MODELS', 'config/models.json'), encoding='utf-8'))
print('1' if any(m.get('name') == sys.argv[1] for m in cfg) else '0')
PYEOF
}
set_default() {
    if [ -z "${!1}" ] && [ "$(cfg_has "$2")" = "1" ]; then
        export "$1=$2"
    fi
}
set_default RP_M_JUDGE cn-judge
set_default RP_M_JUDGE deepseek
: "${RP_WORKERS:=8}"
export RP_M_JUDGE RP_WORKERS

echo "=== Phase 4 检查点 2：新 rubric vs 草稿 rubric ==="
echo "判分=$RP_M_JUDGE  并发=$RP_WORKERS"
echo

echo "[0/2] 校验 Phase 4 产物完整性..."
python3 - <<'PY'
import json, os, sys
from pathlib import Path

DATA = Path(os.environ.get('RP_OUT', 'data'))
paths = {name: DATA / name for name in (
    's04c_phase4.jsonl', 's10_pool388.jsonl', 's12_judged388.jsonl',
    's11c_cons388.jsonl', 's11e_all452.jsonl')}


def fail(message):
    print(f'  ✗ {message}', file=sys.stderr)
    raise SystemExit(2)


def rows(path):
    with path.open(encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]

missing = [name for name, path in paths.items() if not path.exists()]
if missing:
    fail('缺少 Phase 4 产物：' + ', '.join(missing) + '；先成功运行 rerun_phase4.sh')

source = paths['s04c_phase4.jsonl']
stale = [name for name, path in paths.items()
         if name != 's04c_phase4.jsonl'
         and path.stat().st_mtime_ns < source.stat().st_mtime_ns]
if stale:
    fail('检测到旧产物：' + ', '.join(stale) + '；上一次 Phase 4 未完整跑完')

expected = {r['rid'] for r in rows(source)}
if not expected:
    fail('没有双回复题，检查点 2 无可测对象')
pool = {r['rid']: r for r in rows(paths['s10_pool388.jsonl'])}
judged = {r['rid']: r for r in rows(paths['s12_judged388.jsonl'])}
final = {r['rid']: r for r in rows(paths['s11e_all452.jsonl'])}
for label, records in (('回复池', pool), ('判分', judged), ('终态', final)):
    absent = sorted(expected - set(records))
    extra = sorted(set(records) - expected) if label != '终态' else []
    if absent or extra:
        fail(f'{label}题集不一致：缺 {absent[:8]}，多 {extra[:8]}')

required = {'strong', 'mid', 'trunc', 'cut', 'weak', 'adv'}
for rid in sorted(expected):
    represented = {p.get('tier') for p in (pool[rid].get('pool') or [])}
    represented.update((pool[rid].get('pool_errors') or {}).keys())
    if required - represented:
        fail(f'{rid} 回复池缺档：{sorted(required - represented)}')
    missing_judged = represented - set((judged[rid].get('judged') or {}))
    if missing_judged:
        fail(f'{rid} 判分缺档：{sorted(missing_judged)}')
    chosen = (final[rid].get('_s11Le') or {}).get('chosen_round')
    if not chosen or chosen == '未参与 Phase 4（单回复题）':
        fail(f'{rid} 缺 Phase 4 终态选择')

print(f'  ✓ Phase 4 产物完整：{len(expected)} 题')
PY
echo

echo "[1/2] 草稿 rubric 判分（strong+weak，s12_judge 同口径）..."
RP_S12LB_SRC=s10_pool388.jsonl RP_S12LB_OUT=s12b_draft388.jsonl \
  python3 stages/s12b_draft_judge.py
echo

echo "[2/2] pairwise 一致率对比 + 放行判据..."
RP_S12LC_OUT=s12c_pairwise.jsonl python3 stages/s12c_pairwise.py
echo
echo "✅ 检查点 2 跑完。逐题明细: data/s12c_pairwise.jsonl"
