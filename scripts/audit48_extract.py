"""48 试点审计抽取：把 s11Lc_cons48 的判分与诊断材料按题导出成可读档案。

用法:
  RP_AUDIT_SRC=data/s11Lc_cons48.jsonl RP_AUDIT_OUT=data/_audit48 python3 scripts/audit48_extract.py

产出:
  data/_audit48/index.md         全 48 题一览（档次得分率 + 诊断标记）
  data/_audit48/{rid}.md         单题完整档案：题目、rubric、六档回复、逐条判分、诊断
"""
import json, os, sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SRC = os.environ.get('RP_AUDIT_SRC', 'data/s11Lc_cons48.jsonl')
OUT = Path(os.environ.get('RP_AUDIT_OUT', 'data/_audit48'))

TIER_CN = {'strong': '强档', 'mid': '中档', 'trunc': '截断档', 'cut': '删点档',
           'weak': '弱档', 'adv': '对抗档'}


def tier_line(p):
    mark = ' ⚠️失效' if p.get('degraded') else ''
    return (f"| {TIER_CN.get(p['tier'], p['tier'])} | {p['n_chars']} 字 "
            f"| {p.get('how', '')}{mark} |")


def one_case(r):
    c = r.get('consequential') or {}
    lo, ha, cal = c.get('low_signal') or {}, c.get('hackable') or {}, c.get('calibration') or {}
    j = r.get('judged') or {}
    rubs = r.get('rubrics') or []
    cid2c = {x.get('_criterion_id'): x for x in rubs}

    out = []
    out.append(f"# {r['rid']}  {r.get('subject', '')}\n")
    out.append(f"- rubric_form: {r.get('rubric_form')}  question_type: {r.get('question_type')}"
               f"  s_max: {r.get('s_max')}")
    if c.get('skip_reason'):
        out.append(f"- ⏭️ 跳过: {c['skip_reason']}")
    if cal.get('issue'):
        out.append(f"- 🎯 标定: {cal['issue']} — {cal.get('reason', '')}")
    if lo.get('is_defective'):
        out.append(f"- 🔇 LowSignal: {lo.get('reasons')}")
    if ha.get('is_defective'):
        out.append(f"- 🕳️ Hackable: {ha.get('reasons')}")
    out.append(f"\n## 题目\n\n{r.get('question', '')}\n")

    out.append("\n## Rubric 准则\n")
    for x in rubs:
        mark = '扣分' if not x.get('is_positive') else ''
        out.append(f"- [{x.get('_criterion_id')}] ({x.get('dimension')}) "
                   f"{x.get('criteria')}  score={x.get('score')} {mark}")

    out.append("\n## 回复池\n")
    for p in r.get('pool') or []:
        out.append(tier_line(p))
    out.append("\n## 判分（每档得分率）\n")
    for t, v in j.items():
        if isinstance(v, dict):
            out.append(f"- {TIER_CN.get(t, t)}: {v.get('score')}/{v.get('s_max')} "
                       f"= {v.get('rate', 0):.1%}  n_met={v.get('n_met')} "
                       f"n_missing={v.get('n_missing')}")

    # 逐档逐条判定
    out.append("\n## 逐条判定矩阵（met ✓ / 未 met ✗ / 漏判 ?）\n")
    cids = [x.get('_criterion_id') for x in rubs]
    out.append("| 准则 | " + " | ".join(TIER_CN.get(t, t) for t in j) + " |")
    out.append("|---|" + "---|" * len(j))
    for cid in cids:
        row = [f"[{cid}] {cid2c.get(cid, {}).get('criteria', '')[:24]}"]
        for t in j:
            item = next((x for x in j[t].get('items', []) if x.get('_criterion_id') == cid), None)
            if item is None:
                row.append('—')
            elif item.get('judge_missing'):
                row.append('?')
            elif item.get('by_program'):
                row.append('P✓' if item.get('met') else 'P✗')
            else:
                row.append('✓' if item.get('met') else '✗')
        out.append("| " + " | ".join(row) + " |")

    # 翻转/不一致明细
    if ha.get('surface_criteria'):
        out.append("\n## 表面特征（弱/对抗档 met、强档未 met）\n")
        for s in ha['surface_criteria']:
            cid = s['_criterion_id']
            x = cid2c.get(cid, {})
            out.append(f"- [{cid}] {x.get('criteria', '')}  ← 在 {s['tier']} 档 met")
    if ha.get('inconsistent_across_weak'):
        out.append("\n## 弱档造法不一致（trunc/cut/weak 结论打架）\n")
        for s in ha['inconsistent_across_weak']:
            out.append(f"- [{s['_criterion_id']}] trunc={s.get('trunc')} "
                       f"cut={s.get('cut')} weak={s.get('weak')}")

    # 全文
    out.append("\n## 回复全文\n")
    for p in r.get('pool') or []:
        out.append(f"\n### {TIER_CN.get(p['tier'], p['tier'])} ({p['n_chars']} 字) "
                   f"{p.get('how', '')}\n")
        out.append(p.get('text', ''))
        out.append("\n")
    return "\n".join(out)


def main():
    recs = [json.loads(l) for l in open(SRC)]
    OUT.mkdir(parents=True, exist_ok=True)
    idx = []
    for r in sorted(recs, key=lambda x: x['rid']):
        c = r.get('consequential') or {}
        lo, ha, cal = c.get('low_signal') or {}, c.get('hackable') or {}, c.get('calibration') or {}
        j = r.get('judged') or {}
        marks = []
        if c.get('skip_reason'):
            marks.append('⏭️跳过')
        if cal.get('issue'):
            marks.append(cal['issue'])
        if lo.get('is_defective'):
            marks.append('LowSignal')
        if ha.get('is_defective'):
            marks.append('Hackable')
        rates = " ".join(f"{t}={v.get('rate', 0):.0%}" for t, v in j.items())
        idx.append(f"| {r['rid']} | {r.get('rubric_form', '')} | {rates} | "
                   f"{' + '.join(marks) if marks else '✓'} | {r.get('subject', '')[:24]} |")
        (OUT / f"{r['rid']}.md").write_text(one_case(r))
    header = ("| rid | form | " + " | ".join(TIER_CN.get(t, t) for t in
              ['strong', 'mid', 'trunc', 'cut', 'weak', 'adv']) + " | 标记 | 学科 |\n"
              "|---:|---|---|---|---|---|---|---:|---|---|")
    (OUT / 'index.md').write_text("# 48 试点审计索引\n\n" + header + "\n" + "\n".join(idx))
    print(f"抽取完成: {len(recs)} 题 → {OUT}")

if __name__ == '__main__':
    main()
