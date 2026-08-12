#!/usr/bin/env python3
"""健壮的 Phase 1-4 驱动：断点续跑、自动重试、状态持久化。

用法：
  RP_PHASE=1 python3 run_pipeline.py    # 跑 Phase 1（20 条）
  RP_PHASE=2 python3 run_pipeline.py    # 跑 Phase 2（453 条）
  RP_PHASE=3 python3 run_pipeline.py    # Phase 3
  RP_PHASE=4 python3 run_pipeline.py    # Phase 4

状态文件：.pipeline_state.json，记录每个 stage 的完成情况。
挂了重跑会从上次断点继续。
"""
import json, os, sys, time, subprocess
from collections import Counter

STATE_FILE = '.pipeline_state.json'

PHASES = {
    '1': {
        'desc': 'Phase 1: 20条试跑，验证checkpoint',
        'seed': 'seed_phase1.jsonl',
        'stages': [
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
        ],
        'validate': 'validate_phase1',
    },
    '2': {
        'desc': 'Phase 2: 453条结构全量',
        'seed': 'seed.jsonl',
        'stages': [
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
        ],
        'validate': 'validate_phase2',
    },
}

MAX_RETRY = 3           # stage 失败后重试次数
MIN_YIELD = 0.85        # 产出条数 / 输入条数，低于此值算失败


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_state(st):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def count_records(path):
    if not os.path.exists(f'data/{path}'):
        return 0
    with open(f'data/{path}', encoding='utf-8') as f:
        return sum(1 for _ in f)


def run_stage(mod, desc, seed, retry=0):
    """跑一个 stage，返回 (成功, 产出条数)。"""
    print(f'\n▶ [{desc}] stages/{mod}.py')
    t0 = time.time()
    env = dict(os.environ)
    if seed:
        env['RP_SEED'] = seed
    # s00b 自己读 seed.jsonl 做抽样，不受 RP_SEED 影响
    if mod == 's00b_sample':
        env.pop('RP_SEED', None)

    logf = f'/tmp/rp_{mod}_r{retry}.log'
    ret = subprocess.call(f'python3 stages/{mod}.py > {logf} 2>&1',
                          shell=True, env=env, cwd='/home/tantianye/rubrics')
    dt = time.time() - t0

    # 读产出文件判断成功与否
    out_map = {
        's00b_sample': 'seed_phase1.jsonl',
        's01_filter': 's01_filter.jsonl',
        's02_context': 's02_context.jsonl',
        's02_5_route': 's02_5_route.jsonl',
        's03_perspective': 's03_perspective_hybrid.jsonl',
        's03b_merge': 's03b_merged_hybrid.jsonl',
        's04_criteria': 's04_criteria.jsonl',
        's07_difficulty': 's07_evolved.jsonl',
        's08_penalties': 's08_penalties.jsonl',
        's09_normalize': 's09_normalized.jsonl',
        's11_diagnose': 's11_diagnosed.jsonl',
    }
    outf = out_map.get(mod)
    nc = count_records(outf) if outf else 0

    if ret != 0:
        print(f'  ✗ 非零退出码 {ret}，用时 {dt:.0f}s，日志 {logf}')
        return False, nc

    # 产出条数校验（s00b 除外，它是生成而非变换）
    if mod != 's00b_sample':
        # 取前序步骤的产出作为输入基准
        inp_map = {
            's01_filter': seed,
            's02_context': 's01_filter.jsonl',
            's02_5_route': 's02_context.jsonl',
            's03_perspective': 's02_5_route.jsonl',
            's03b_merge': 's03_perspective_hybrid.jsonl',
            's04_criteria': 's03b_merged_hybrid.jsonl',
            's07_difficulty': 's04_criteria.jsonl',
            's08_penalties': 's07_evolved.jsonl',
            's09_normalize': 's08_penalties.jsonl',
            's11_diagnose': 's09_normalized.jsonl',
        }
        inp = count_records(inp_map.get(mod, seed))
        ratio = nc / max(inp, 1)
        if ratio < MIN_YIELD and nc < inp:
            print(f'  ✗ 产出率过低 {nc}/{inp}={ratio:.0%}，用时 {dt:.0f}s')
            return False, nc

    print(f'  ✓ 完成，产出 {nc} 条，用时 {dt:.0f}s')
    return True, nc


def validate_phase1():
    """Phase 1 四个 checkpoint。"""
    print('\n=== Phase 1 Checkpoint ===')
    from lib import stage
    final = stage.read_jsonl('s11_diagnosed.jsonl')
    baseline = json.load(open('data/baseline.json', encoding='utf-8'))

    # 1. 维度去重数 ≥ 4
    dims = len({c['perspective_id'] for r in final for c in r['criteria']
                if c.get('perspective_id')})
    dims_base = baseline.get('dimension_uniq', baseline.get('perspective_distinct', 0))
    pass1 = dims >= 4
    print(f'  1. 维度去重数: {dims} (基线={dims_base}, {"✓" if pass1 else "✗ FAIL"} 目标≥4)')

    # 2. 准则总数 > baseline
    nc = sum(len(r['criteria']) for r in final)
    nc_base = baseline.get('n_criteria_total', baseline.get('total_criteria', 0))
    print(f'  2. 准则总数  : {nc} (基线={nc_base})')

    # 3. 题型判定错误 ≤ 2
    route = stage.read_jsonl('s02_5_route.jsonl')
    errors = sum(1 for r in route
                 if r.get('question_type_draft') and
                 r['question_type'] != r['question_type_draft'])
    pass3 = errors <= 2
    print(f'  3. 题型错误  : {errors} ({"✓" if pass3 else "✗ FAIL"} 目标≤2)')

    # 4. multi_part 空壳 = 0
    empty_mp = sum(1 for r in route
                   if r['rubric_form'] == 'multi_part' and len(r.get('blocks', [])) < 2)
    pass4 = empty_mp == 0
    print(f'  4. 空 multi_part: {empty_mp} ({"✓" if pass4 else "✗ FAIL"} 目标=0)')

    all_pass = pass1 and pass3 and pass4
    print(f'\nCheckpoint: {"✓ PASS" if all_pass else "✗ FAIL"}')
    if not pass1:
        print('  !!! 维度去重数 <4，PLAN 说后面全部无意义，需调 prompt 或换方法')
    return all_pass


def validate_phase2():
    """Phase 2 指标报告，对比 baseline。"""
    print('\n=== Phase 2 指标 ===')
    from lib import stage
    final = stage.read_jsonl('s11_diagnosed.jsonl')
    baseline = json.load(open('data/baseline.json', encoding='utf-8'))

    dims = len({c['perspective_id'] for r in final for c in r['criteria']
                if c.get('perspective_id')})
    nc = sum(len(r['criteria']) for r in final)
    route = stage.read_jsonl('s02_5_route.jsonl')
    errors = sum(1 for r in route
                 if r.get('question_type_draft') and
                 r['question_type'] != r['question_type_draft'])
    empty_mp = sum(1 for r in route
                   if r['rubric_form'] == 'multi_part' and len(r.get('blocks', [])) < 2)

    diag_stats = {'subjective': 0, 'non-atomic': 0, 'ungrounded': 0}  # 键要带连字符
    for r in final:
        for c in r['criteria']:
            for k in diag_stats:
                if c.get('diagnostics', {}).get(k, {}).get('verdict') == 'defective':
                    diag_stats[k] += 1
    rift_rate = {k: v / max(nc, 1) * 100 for k, v in diag_stats.items()}

    rewrite = sum(1 for r in stage.read_jsonl('s01_filter.jsonl') if r.get('query_eff_flag'))
    rw_rate = rewrite / max(len(final), 1) * 100

    metrics = {
        'perspective_distinct': dims,
        'perspective_distinct_baseline': baseline.get('dimension_uniq', 0),
        'perspective_distinct_pass': dims >= baseline.get('dimension_uniq', 0) * 1.5,
        'total_criteria': nc,
        'total_criteria_baseline': baseline.get('n_criteria_total', 0),
        'route_errors': errors,
        'multi_part_empty': empty_mp,
        'rift_subjective_pct': rift_rate['subjective'],
        'rift_non_atomic_pct': rift_rate['non-atomic'],
        'rift_ungrounded_pct': rift_rate['ungrounded'],
        'rewrite_rate_pct': rw_rate,
    }

    os.makedirs('reports', exist_ok=True)
    with open('reports/phase2_metrics.json', 'w', encoding='utf-8') as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    print(f'  维度去重数     : {dims} (基线={baseline.get("dimension_uniq", 0)}, '
          f'{"✓" if metrics["perspective_distinct_pass"] else "✗"} 目标≥1.5倍)')
    print(f'  准则总数       : {nc} (基线={baseline.get("n_criteria_total", 0)})')
    print(f'  题型判定错误   : {errors}')
    print(f'  multi_part空壳 : {empty_mp}')
    print(f'  RIFT Subj={rift_rate["subjective"]:.1f}% '
          f'NonAtom={rift_rate["non-atomic"]:.1f}% Ungnd={rift_rate["ungrounded"]:.1f}%')
    print(f'  改写率         : {rw_rate:.1f}%')
    print(f'\n✓ 指标已写入 reports/phase2_metrics.json')
    return True


def main():
    phase = os.environ.get('RP_PHASE', '1')
    if phase not in PHASES:
        print(f'未知 Phase: {phase}，可用: {list(PHASES)}')
        return 1

    cfg = PHASES[phase]
    print(f'=== {cfg["desc"]} ===\n')
    print(f'状态文件: {STATE_FILE}')
    print(f'种子文件: {cfg["seed"]}')
    print(f'步骤数  : {len(cfg["stages"])}')
    print()

    st = load_state()
    key = f'phase{phase}'
    if key not in st:
        st[key] = {}
    pst = st[key]

    t0 = time.time()
    for i, (mod, desc) in enumerate(cfg['stages'], 1):
        sid = f'{i:02d}_{mod}'
        if pst.get(sid, {}).get('done'):
            print(f'[{i}/{len(cfg["stages"])}] {desc} — 已完成，跳过')
            continue

        ok, nc = False, 0
        for att in range(MAX_RETRY):
            ok, nc = run_stage(mod, desc, cfg['seed'], retry=att)
            if ok:
                break
            if att < MAX_RETRY - 1:
                print(f'  重试 {att + 2}/{MAX_RETRY}...')
                time.sleep(5 ** (att + 1))

        if not ok:
            print(f'\n✗ Stage {mod} 失败 {MAX_RETRY} 次，中止')
            pst[sid] = {'done': False, 'nc': nc}
            save_state(st)
            return 1

        pst[sid] = {'done': True, 'nc': nc}
        save_state(st)

    dt = time.time() - t0
    print(f'\n✓ 全流程完成，用时 {dt / 60:.0f} 分钟')

    # Validate
    val_fn = globals().get(cfg['validate'])
    if val_fn:
        val_fn()
    return 0


if __name__ == '__main__':
    sys.exit(main())
