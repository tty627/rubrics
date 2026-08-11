"""Phase 1 完整流程驱动 —— 串起 s00b→s01→...→s11，产出指标报告。

运行：
    python3 run_phase1.py

产出：
  - data/s11_diagnosed.jsonl（最终 rubric）
  - reports/phase1_metrics.json（六个验收指标，对比 baseline.json）
  - data/filled.xlsx（回填 A/B/C 列）
"""
import json, os, sys, time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import stage

STAGES = [
    ('s00b_sample', '抽样'),
    ('s01_filter', '入口过滤'),
    ('s02_context', '上下文标签'),
    ('s02_5_route', '题型路由'),
    ('s03_perspective', '视角展开'),
    ('s03b_merge', '视角归并'),
    ('s04_criteria', '准则实例化'),
    ('s07_difficulty', '难度演化'),
    ('s08_penalties', '惩罚项'),
    ('s09_normalize', '归一化'),
    ('s11_diagnose', 'RIFT 免池诊断'),
]


def main():
    print('=== Phase 1 完整流程 ===\n')
    t0 = time.time()

    # Phase 1 只跑 20 条子集：s00b 抽样后，后续步骤必须读 seed_phase1.jsonl，
    # 否则会跑全量 453 条（那是 Phase 2 的事，调用量 20 倍）
    env = dict(os.environ)
    env['RP_SEED'] = 'seed_phase1.jsonl'

    for i, (mod, desc) in enumerate(STAGES, 1):
        print(f'[{i}/{len(STAGES)}] {desc}...')
        t1 = time.time()
        # s00b 自己读 seed.jsonl 做抽样，不受 RP_SEED 影响
        cmd = f'RP_SEED=seed_phase1.jsonl python3 stages/{mod}.py > /tmp/rp_{mod}.log 2>&1'
        if mod == 's00b_sample':
            cmd = f'python3 stages/{mod}.py > /tmp/rp_{mod}.log 2>&1'
        os.system(cmd)
        dt = time.time() - t1
        print(f'  完成，用时 {dt:.0f}s\n')

    print(f'✓ 全流程完成，总用时 {time.time() - t0:.0f}s\n')

    # 读最终产出
    final = stage.read_jsonl('s11_diagnosed.jsonl')
    baseline = json.load(open('data/baseline.json', encoding='utf-8'))

    # 指标 1：维度去重数
    dims_base = baseline['perspective_distinct']
    dims_now = len({c['perspective_id'] for r in final for c in r['criteria']
                    if c.get('perspective_id')})

    # 指标 2：准则总数
    nc_base = baseline['total_criteria']
    nc_now = sum(len(r['criteria']) for r in final)

    # 指标 3：题型判定错误数（从 s02_5 里读草稿标签）
    route = stage.read_jsonl('s02_5_route.jsonl')
    errors = sum(1 for r in route
                 if r.get('question_type_draft') and
                 r['question_type'] != r['question_type_draft'])

    # 指标 4：multi_part 空壳数（block < 2）
    empty_mp = sum(1 for r in route
                   if r['rubric_form'] == 'multi_part' and len(r.get('blocks', [])) < 2)

    # 指标 5：RIFT 检出率
    diag_stats = {'subjective': 0, 'non_atomic': 0, 'ungrounded': 0}
    for r in final:
        for c in r['criteria']:
            for k in diag_stats:
                if c.get('diagnostics', {}).get(k, {}).get('verdict') == 'defective':
                    diag_stats[k] += 1
    rift_rate = {k: v / max(nc_now, 1) * 100 for k, v in diag_stats.items()}

    # 指标 6：改写率
    rewrite = sum(1 for r in stage.read_jsonl('s01_filter.jsonl') if r.get('query_eff_flag'))
    rw_rate = rewrite / max(len(final), 1) * 100

    metrics = {
        'perspective_distinct': dims_now,
        'perspective_distinct_baseline': dims_base,
        'perspective_distinct_pass': dims_now >= 4,
        'total_criteria': nc_now,
        'total_criteria_baseline': nc_base,
        'route_errors': errors,
        'route_errors_pass': errors <= 2,
        'multi_part_empty': empty_mp,
        'multi_part_empty_pass': empty_mp == 0,
        'rift_subjective_pct': rift_rate['subjective'],
        'rift_non_atomic_pct': rift_rate['non_atomic'],
        'rift_ungrounded_pct': rift_rate['ungrounded'],
        'rewrite_rate_pct': rw_rate,
    }

    os.makedirs('reports', exist_ok=True)
    with open('reports/phase1_metrics.json', 'w', encoding='utf-8') as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    print('=== Phase 1 验收指标 ===')
    print(f'  1. 维度去重数     : {dims_now} (基线={dims_base}, '
          f'{"✓" if metrics["perspective_distinct_pass"] else "✗"} 目标≥4)')
    print(f'  2. 准则总数       : {nc_now} (基线={nc_base})')
    print(f'  3. 题型判定错误   : {errors} ({"✓" if metrics["route_errors_pass"] else "✗"} 目标≤2)')
    print(f'  4. multi_part空壳 : {empty_mp} ({"✓" if metrics["multi_part_empty_pass"] else "✗"} 目标=0)')
    print(f'  5. RIFT 检出率    : Subj={rift_rate["subjective"]:.1f}% '
          f'NonAtom={rift_rate["non_atomic"]:.1f}% Ungnd={rift_rate["ungrounded"]:.1f}%')
    print(f'  6. 改写率         : {rw_rate:.1f}% (PLAN 预期 4-8%)')

    print(f'\n✓ 指标已写入 reports/phase1_metrics.json')
    print(f'  最终 rubric: data/s11_diagnosed.jsonl ({len(final)} 条)')


if __name__ == '__main__':
    main()
