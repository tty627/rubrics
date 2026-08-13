"""步骤 11Lb：诊断处置（lean 流程版）—— 按失效模式分级处置。

输入: s11L_diagnosed.jsonl
输出: s11Lb_remedied.jsonl + _defect_queue.jsonl（待拆队列，供 s04Lb_split 消费）

**2026-08-13 改版原因**（旧版一律删除，实测后果）：
  RIFT 判 defective 1511/2452 = 61.6%，其中 non-atomic 占 1399（57%）。
  一律删的结果：344/452 题因「删完不足 3 条」被静默跳过，只有 108 题真处置了；
  而真删的那 108 题里，4 道 gated_answer 的**答案项被删掉**
  （q0008 满分 12→3，那条 +8 的「列出全部六十四卦」没了），
  8 道题满分腰斩。删除对 non-atomic 是错误处置 —— 非原子的正确解法是拆，不是删。

处置策略（按失效模式分级，优先级 factual > drop > split）：
  factual     → **绝不删**，落队列拿真值重写。事实错误的准则主题是对的、内容写错了，
                删掉这道题就没有答案判据了。诊断器只凭自身知识判定的（basis=
                model_knowledge）另打 needs_review，下游只做保守改写或转人工 ——
                实测它会在同一题内自相矛盾（q0045 一条判由说产物三种、另一条说两种）。
  subjective  → 删。主观准则无法客观判定，留着只会增加判分方差。（实测仅 12 条）
  ungrounded  → 删。脱靶准则在评不相干的东西，删了不损失覆盖。（实测 318 条）
  non-atomic  → **不删**，落 _defect_queue.jsonl 等 s04Lb_split 拆分。
  闸门项       → 一律豁免。gated_answer 的答案项是这类题的唯一判据，
                 删了整道题就失去「答对没答对」的能力，宁可带缺陷保留。

删除后正向不足 3 条 → 保留原始 rubrics 并打 needs_regen=true。
旧版这里是静默 skip，外部看不出这题没处置成功；现在显式标记，供下游重生成。

满分 s_max = sum(正向 score)。导师 2026-08-13 明确 score 直接当权重，不归一。
"""
import json, os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

SRC = os.environ.get('RP_S11LB_SRC', 's11L_diagnosed.jsonl')
MIN_POS = int(os.environ.get('RP_MIN_POS', 3))      # 处置后至少保留几条正向准则

# 失效模式 → 处置动作
ACTION = {
    'subjective': 'drop',
    'ungrounded': 'drop',
    'non-atomic': 'split',
    'factual': 'split',      # 走重写队列，绝不删 —— 见下方说明
}


def gate_cid(r):
    """gated_answer 题的答案项 criterion_id；非该题型或无法确定时返回 None。"""
    if r.get('rubric_form') != 'gated_answer':
        return None
    pos = [c for c in r.get('rubrics') or [] if c.get('is_positive')]
    if not pos:
        return None
    top = max(c['score'] for c in pos)
    if top < 4 or sum(1 for c in pos if c['score'] == top) != 1:
        return None
    return next(c.get('_criterion_id') for c in pos if c['score'] == top)


def decide(r):
    """返回 (要删的 cid 集合, 要重写的 cid→任务信息, 豁免的 cid 集合)。

    优先级：factual > drop > split。

    factual 必须压过 drop —— 实测那 57 条「其实是事实错误」的准则当初就是被
    塞进 ungrounded 才暴露出来的，新检测器上线后它们会同时背上两个标签。
    若让 drop 优先，这批照样被删，加检测器就白加了。事实错误的准则**主题是对的、
    内容写错了**，正确处置是拿真值重写，不是删。
    """
    protect = {gate_cid(r)} - {None}
    drop, rewrite = set(), {}

    for d in r.get('diagnoses') or []:
        cid = d.get('_criterion_id')
        if not cid or not d.get('is_defective'):
            continue
        modes = d.get('failure_modes') or []
        det = d.get('details') or {}

        if 'factual' in modes:
            f = det.get('factual') or {}
            rewrite[cid] = {
                'modes': modes, 'kind': 'factual',
                'basis': f.get('basis', 'model_knowledge'),
                'reason': f.get('reason', ''),
                'correct_value': f.get('correct_value', ''),
                # 诊断器凭自己记忆下的结论不可全信（实测它会在同一题里自相矛盾），
                # 标出来让下游只做保守改写 / 转人工，不要直接写死一个新答案
                'needs_review': f.get('basis') not in ('draft', 'question'),
            }
            continue

        acts = {ACTION.get(m) for m in modes}
        if cid in protect:
            if 'split' in acts:
                rewrite[cid] = {'modes': modes, 'kind': 'split'}
            continue
        if 'drop' in acts:
            drop.add(cid)
        elif 'split' in acts:
            rewrite[cid] = {'modes': modes, 'kind': 'split'}

    return drop, rewrite, protect


def main():
    recs = stage.read_jsonl(SRC)
    print(f'步骤 11Lb 诊断处置: {len(recs)} 条, 源={SRC}')
    print(f'  策略: subjective/ungrounded→删, non-atomic→送拆, 闸门项→豁免')

    res, queue = [], []
    n_drop = n_split = n_regen = n_protect = 0
    mode_stat = Counter()
    kind_stat = Counter()

    for r in recs:
        rubrics = r.get('rubrics') or []
        drop, split, protect = decide(r)

        for d in r.get('diagnoses') or []:
            if d.get('is_defective'):
                for m in d.get('failure_modes') or []:
                    mode_stat[m] += 1

        kept = [c for c in rubrics if c.get('_criterion_id') not in drop]
        removed = [c for c in rubrics if c.get('_criterion_id') in drop]
        kept_pos = [c for c in kept if c['is_positive']]

        # 删完正向不足 → 整题回滚，打 needs_regen
        if removed and len(kept_pos) < MIN_POS:
            n_regen += 1
            res.append({**r,
                        'criteria_before_remedy': len(rubrics),
                        'criteria_after_remedy': len(rubrics),
                        'criteria_removed': 0,
                        'criteria_dropped_proposed': len(removed),
                        'criteria_pending_split': len(split),
                        'needs_regen': True,
                        'remedy_skipped': True,
                        'skip_reason': f'删{len(removed)}条后仅剩{len(kept_pos)}条正向，'
                                       f'已回滚待重生成'})
            continue

        n_drop += len(removed)
        n_split += len(split)
        n_protect += len(protect & (set(split) | drop))

        # 待重写条目落盘，供 s04Lb_split 消费
        for c in kept:
            cid = c.get('_criterion_id')
            if cid in split:
                t = split[cid]
                kind_stat[t['kind']] += 1
                queue.append({'rid': r['rid'], '_criterion_id': cid,
                              'criteria': c['criteria'], 'score': c['score'],
                              'dimension': c['dimension'],
                              'is_positive': c['is_positive'],
                              'failure_modes': t['modes'],
                              'kind': t['kind'],
                              'basis': t.get('basis', ''),
                              'diag_reason': t.get('reason', ''),
                              'correct_value': t.get('correct_value', ''),
                              'needs_review': t.get('needs_review', False),
                              'is_gate': cid in protect})
                c['_pending_split'] = True
                if t['kind'] == 'factual':
                    c['_flag_factual'] = True
                    if t.get('needs_review'):
                        c['_needs_review'] = True

        res.append({**r,
                    'rubrics': kept,
                    's_max': sum(c['score'] for c in kept_pos),
                    'core_n': len(kept),
                    'core_n_positive': len(kept_pos),
                    'criteria_before_remedy': len(rubrics),
                    'criteria_after_remedy': len(kept),
                    'criteria_removed': len(removed),
                    'criteria_pending_split': len(split),
                    'removed_criterion_ids': [c.get('_criterion_id') for c in removed],
                    'needs_regen': False,
                    'remedy_skipped': False,
                    'skip_reason': ''})

    stage.write_jsonl('s11Lb_remedied.jsonl', res)
    stage.write_jsonl('_defect_queue.jsonl', queue)

    n_before = sum(r.get('criteria_before_remedy', 0) for r in res)
    n_after = sum(len(r.get('rubrics') or []) for r in res)
    print(f'\n=== 步骤 11Lb 结果 ===')
    print(f'  诊断失效模式  : ' + '  '.join(f'{k}={v}' for k, v in mode_stat.most_common()))
    print(f'  准则数        : {n_before} → {n_after}')
    print(f'  删除(主观/脱靶): {n_drop} 条')
    print(f'  送重写         : {n_split} 条 → data/_defect_queue.jsonl  '
          + '  '.join(f'{k}={v}' for k, v in kind_stat.most_common()))
    n_rev = sum(1 for q in queue if q.get('needs_review'))
    if n_rev:
        print(f'    其中待人工复核: {n_rev} 条（事实错误但诊断器只凭自身知识判定）')
    print(f'  闸门项豁免     : {n_protect} 条（被判 defective 但保留）')
    print(f'  待重生成       : {n_regen} 题（删后正向不足 {MIN_POS} 条，已回滚）')

    sm_a = sum(r.get('s_max', 0) for r in res)
    print(f'  总满分        : {sm_a}')
    half = [r['rid'] for r in res
            if r.get('criteria_before_remedy') and not r.get('needs_regen')
            and len(r.get('rubrics') or []) < r['criteria_before_remedy'] * 0.5]
    print(f'  准则腰斩的题  : {len(half)} {half[:8] or ""}')

    ex = next((r for r in res if r.get('criteria_removed', 0) > 0), None)
    if ex:
        print(f'\n  抽样 {ex["rid"]}: 删 {ex["criteria_removed"]} 条, '
              f'待拆 {ex["criteria_pending_split"]} 条, 满分 {ex["s_max"]}')


if __name__ == '__main__':
    main()
