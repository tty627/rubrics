#!/bin/bash
# Phase 4 全量链：回复池 → 判分 → 区分度诊断 → 处置闭环 → 终态选择 → 导出
# 用法: bash scripts/rerun_phase4.sh
#
# 与结构线（rerun_lean_fixed.sh）的关系：结构线产出 s04c_severity.jsonl（452 题），
# Phase 4 在其中 388 道**双回复**题上做实测（硬约束 1：锚 ≠ 待评，单回复题做不了），
# 最后把 388 题的实测终态与 64 道单回复题合并回 452。
#
# 处置必须闭环复测（s11d_remedy → s12_judge 重判 → s11c_consequential 复诊），这不是保险而是必需：
# LLM 重写会**摆动**。388 全量实测 q0221 走出 60%→0%→60%→0% 的 2-循环 ——
# 「收紧」与「放松」是互逆操作，对这类题不存在两头都满足的中间档。所以跑固定
# 轮数（默认 3），再由 s11e_select 在各轮实测证据里挑每题最好的那一版，而不是死等收敛。

set -e
REPO=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO"

: "${RP_M_JUDGE:=cn-judge}"      # 判分器，family 必须 ≠ 生成器（硬约束 2）
: "${RP_M_VETO:=cn-veto}"        # veto 第二票，family 必须 ≠ 判分器与生成器
: "${RP_M_S11LD:=cn-gen}"        # 处置重写
: "${RP_WORKERS:=6}"
: "${RP_ROUNDS:=3}"              # 处置轮数
export RP_M_JUDGE RP_M_VETO RP_M_S11LD RP_WORKERS

echo "=== Phase 4 全量 ==="
echo "判分=$RP_M_JUDGE  veto=$RP_M_VETO  重写=$RP_M_S11LD  并发=$RP_WORKERS  轮数=$RP_ROUNDS"
echo

# ---- 1. 取双回复题 ----
echo "[1/6] 筛双回复题..."
python3 - <<'PY'
import json
src='data/s04c_severity.jsonl'; out='data/s04c_phase4.jsonl'
keep=[]
for l in open(src, encoding='utf-8'):
    r=json.loads(l)
    refs=r.get('ref_responses') or {}
    n=sum(1 for v in refs.values() if isinstance(v,str) and v.strip())
    if n>=2: keep.append(r)
with open(out,'w',encoding='utf-8') as f:
    for r in keep: f.write(json.dumps(r,ensure_ascii=False)+'\n')
print(f'  ✓ {out}  {len(keep)} 题（单回复题按硬约束 1 排除）')
PY
echo

# ---- 2. 回复池 ----
# 6 档：strong / mid / trunc(40%) / cut(删要点) / weak / adv(对抗)
echo "[2/6] 回复池 (s10_pool.py)..."
RP_S10L_SRC=s04c_phase4.jsonl RP_S10L_OUT=s10_pool388.jsonl \
  python3 stages/s10_pool.py
echo

# ---- 3. 判分 ----
echo "[3/6] 判分 (s12_judge.py)..."
RP_S12L_SRC=s10_pool388.jsonl RP_S12L_OUT=s12_judged388.jsonl \
  python3 stages/s12_judge.py
echo

# ---- 4. 区分度诊断 ----
echo "[4/6] 区分度诊断 (s11c_consequential.py)..."
RP_S11LC_SRC=s12_judged388.jsonl RP_S11LC_OUT=s11c_cons388.jsonl \
  python3 stages/s11c_consequential.py
echo

# ---- 5. 处置闭环 ----
ROUNDS="s11c_cons388.jsonl"
CUR="s11c_cons388.jsonl"
for i in $(seq 1 "$RP_ROUNDS"); do
    echo "[5/6] 处置第 $i 轮 (s11d_remedy → s12_judge 重判 → s11c_consequential 复诊)..."
    RP_S11LD_SRC="$CUR" RP_S11LD_OUT="s11d_r$i.jsonl" python3 stages/s11d_remedy.py
    # 只把**被重写**的题送去重判：其余题 rubric 没变，重判是白烧 token
    python3 - "$i" <<'PY'
import json, sys
i=sys.argv[1]
out=[r for r in (json.loads(l) for l in open(f'data/s11d_r{i}.jsonl', encoding='utf-8'))
     if (r.get('s11Ld') or {}).get('rewritten')]
with open(f'data/s11d_r{i}_rw.jsonl','w',encoding='utf-8') as f:
    for r in out: f.write(json.dumps(r,ensure_ascii=False)+'\n')
print(f'  本轮重写 {len(out)} 题，待重判 {sum(len(r["pool"]) for r in out)} 档')
PY
    if [ ! -s "data/s11d_r${i}_rw.jsonl" ]; then
        echo "  本轮无重写，处置收敛，提前结束"
        break
    fi
    RP_S12L_SRC="s11d_r${i}_rw.jsonl" RP_S12L_OUT="s12_r$i.jsonl" python3 stages/s12_judge.py
    RP_S11LC_SRC="s12_r$i.jsonl" RP_S11LC_OUT="s11c_r$i.jsonl" python3 stages/s11c_consequential.py
    ROUNDS="$ROUNDS,s11c_r$i.jsonl"
    CUR="s11c_r$i.jsonl"
    echo
done

# ---- 6. 终态选择 + 合并 + 导出 ----
echo "[6/6] 终态选择 (s11e_select.py)..."
RP_S11LE_ROUNDS="$ROUNDS" RP_S11LE_OUT=s11e_final.jsonl python3 stages/s11e_select.py
echo

# 处置重写会丢掉负项 severity（s11d_remedy 只保正向分值守恒与血缘），补回来
echo "  补负项分级 (s04c_severity.py)..."
RP_S04LC_SRC=s11e_final.jsonl RP_S04LC_OUT=s11e_final_sev.jsonl \
  python3 stages/s04c_severity.py | tail -6
echo

echo "  合并未参与 Phase 4 的单回复题..."
python3 - <<'PY'
import json
base={json.loads(l)['rid']: json.loads(l)
      for l in open('data/s04c_severity.jsonl', encoding='utf-8')}
p4={json.loads(l)['rid']: json.loads(l)
    for l in open('data/s11e_final_sev.jsonl', encoding='utf-8')}
out=[]
for rid, b in base.items():
    if rid in p4:
        r=dict(p4[rid])
        for k in ('judged','pool','consequential'): r.pop(k, None)  # 中间产物不进主线
        out.append(r)
    else:
        out.append({**b, '_s11Le': {'chosen_round': '未参与 Phase 4（单回复题）',
                                    'residual': [], 'skipped': False, 'rounds_seen': 0}})
with open('data/s11e_all452.jsonl','w',encoding='utf-8') as f:
    for r in out: f.write(json.dumps(r,ensure_ascii=False)+'\n')
print(f'  ✓ data/s11e_all452.jsonl  {len(out)} 题 '
      f'{sum(len(r["rubrics"]) for r in out)} 条准则')
PY
echo

if [ -f outputs/rubrics_advisor_lean.jsonl ]; then
    cp outputs/rubrics_advisor_lean.jsonl outputs/rubrics_advisor_lean.jsonl.bak
fi
python3 scripts/export_advisor_schema.py \
    --src data/s11e_all452.jsonl \
    --out outputs/rubrics_advisor_lean.jsonl \
    --full
echo
python3 scripts/fill_xlsx_preserve_format.py | tail -3
echo

echo "=== 质量审计 ==="
if [ -f outputs/rubrics_advisor_lean.jsonl.bak ]; then
    python3 scripts/audit_rubrics.py outputs/rubrics_advisor_lean.jsonl \
        --base outputs/rubrics_advisor_lean.jsonl.bak || true
else
    python3 scripts/audit_rubrics.py outputs/rubrics_advisor_lean.jsonl || true
fi

echo
echo "✅ Phase 4 完成。关键产出："
echo "  data/s10_pool388.jsonl    6 档回复池"
echo "  data/s12_judged388.jsonl  判分结果（含 veto 两票、同源一致性修正）"
echo "  data/s11c_cons388.jsonl   区分度诊断（基线轮）"
echo "  data/s11e_final.jsonl     各轮实测里挑出的每题最优 rubric"
echo "  data/s11e_all452.jsonl    与单回复题合并后的交付源"
