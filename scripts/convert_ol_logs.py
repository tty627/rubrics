#!/usr/bin/env python3
"""OpenCompass 线上日志(generation_ol) → 流水线输入（input.xlsx 七列 + seed.jsonl）。

输入格式（generation_ol_*.jsonl，每行一个 session）：
  {"uid", "model_id", "session_id", ..., "messages": [{"role","content"}...],
   "label": {"domain","answer_quality","score","capability","difficulty",
             "query_type","verify_type",...}}

映射规则：
  question      = 第一条 user 消息（任务要求段；后续 user 消息是研究资料注入，
                  过长不进题目，避免把 rubric 生成锚在资料上）
  subject       = [label.domain]
  ref_responses = assistant 消息文本（去空；最多取 2 条最长的 → 多回复题才能进
                  Phase 4 实测，单回复题只走结构线，硬约束 1）
  draft_rubric  = 留空（原数据无草稿；检查点 2 的草稿对比需另行补，见报告）
  人工标注      = 存为 _ol_* 内部字段（score / answer_quality / capability 等），
                  后续可当锚点集或验证集用

产出（同一目录）：
  input.xlsx  七列（A-G 与 data/input.xlsx 原格式一致，s00_seed 标准入口）
  seed.jsonl  直接可喂 s01_filter 的种子（与 s00_seed(input.xlsx) 逐字节一致）
  report.json 统计报告

用法:
  python3 scripts/convert_ol_logs.py --src <ol.jsonl> --out-dir <目录>
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import xlsx

LABEL_KEYS = ('answer_quality', 'score', 'capability', 'difficulty',
              'query_type', 'verify_type', 'topic', 'is_chitchat', 'is_complete')

# 与 lib/xlsx._XML_BAD 同一份：提取端先清一遍，保证 seed.jsonl 与
# input.xlsx 往返逐字节一致（xlsx 写库也会再兜底清一次）
_XML_BAD = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]')


def sanitize(s):
    s = _XML_BAD.sub('', s)
    # XML 解析会把 \r / \r\n 归一成 \n：提取端先归一，保证 seed 与 xlsx 往返逐字节一致
    return s.replace('\r\n', '\n').replace('\r', '\n')


def content_of(m):
    c = m.get('content')
    return c if isinstance(c, str) and c.strip() else ''


def convert(recs):
    out, n_multi, skipped, labels = [], 0, [], []
    n_out = 0                                    # 按产出顺序编号（跳过的行不占号）
    for line_no, r in enumerate(recs, 1):
        users = [content_of(m) for m in r.get('messages') or []
                 if m.get('role') == 'user']
        users = [u for u in users if u]
        answers = [content_of(m) for m in r.get('messages') or []
                   if m.get('role') == 'assistant']
        answers = [a for a in answers if a]
        if not users:
            skipped.append({'line': line_no, 'reason': '缺 user 消息'})
            continue
        if not answers:
            skipped.append({'line': line_no, 'reason': 'assistant 回复为空'})
            continue
        question = sanitize(users[0][:8000])  # 任务要求段，截断防超预算
        answers = sorted({sanitize(a) for a in answers}, key=len, reverse=True)[:2]
        refs = {f'response_{i+1}': a for i, a in enumerate(answers) if a}
        if len(refs) >= 2:
            n_multi += 1
        label = r.get('label') or {}
        n_out += 1
        out.append({
            'rid': f'q{n_out + 1:04d}',     # 与 xlsx 行号对齐（表头占第 1 行）
            'xlsx_row': n_out + 1,
            'question': question,
            'subject': [str(label.get('domain', ''))],
            'draft_rubric': None,
            'ref_responses': refs,
            'ref_errors': [],
        })
        labels.append({
            'rid': out[-1]['rid'], 'xlsx_row': out[-1]['xlsx_row'],
            'ol_line': line_no,              # 原始 jsonl 行号，溯源用
            'uid': r.get('uid'), 'session_id': r.get('session_id'),
            'label': {k: label.get(k) for k in LABEL_KEYS},
        })
    return out, n_multi, skipped, labels


def report(recs, n_multi, skipped, labels):
    from collections import Counter
    n = len(recs)
    refs = Counter(len(r['ref_responses']) for r in recs)
    score = Counter(str((x['label'].get('score') or {}).get('value'))
                    for x in labels)
    quality = Counter(x['label'].get('answer_quality') for x in labels)
    return {
        'n_records': n,
        'ref_response_count_dist': dict(refs),
        'double_response': n_multi,
        'answer_quality_dist': dict(quality),
        'score_value_dist': dict(score),
        'notes': [
            'question=第一条 user 消息；draft_rubric 留空（检查点 2 无草稿对照）',
            f'仅 {n_multi}/{n} 题为双回复，可进 Phase 4；其余单回复题只走结构线',
            '人工标注存于 _ol_label 内部字段',
        ],
        'skipped': skipped,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--out-dir', default=None,
                    help='默认与 --src 同目录')
    a = ap.parse_args()
    out_dir = a.out_dir or os.path.dirname(os.path.abspath(a.src))
    os.makedirs(out_dir, exist_ok=True)

    with open(a.src, encoding='utf-8') as f:
        recs = [json.loads(l) for l in f if l.strip()]
    print(f'读入 {len(recs)} 条 session')

    seed, n_multi, skipped, labels = convert(recs)
    rep = report(seed, n_multi, skipped, labels)

    # seed.jsonl
    seed_path = os.path.join(out_dir, 'seed.jsonl')
    with open(seed_path, 'w', encoding='utf-8') as f:
        for r in seed:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

    # labels.jsonl：人工标注 sidecar（xlsx 七列装不下；锚点集/验证/草稿生成素材用）
    with open(os.path.join(out_dir, 'labels.jsonl'), 'w', encoding='utf-8') as f:
        for x in labels:
            f.write(json.dumps(x, ensure_ascii=False) + '\n')

    # input.xlsx 七列（与 data/input.xlsx 原格式一致）
    header = ['need_rewrite', 'rewritten', 'gen_rubric', 'question',
              'dimension', 'draft_rubric', 'ref_response']
    rows = [header]
    for r in seed:
        rows.append([
            '', '', '', r['question'],
            json.dumps(r['subject'], ensure_ascii=False),
            '',                                   # draft_rubric 留空
            json.dumps(r['ref_responses'], ensure_ascii=False),
        ])
    xlsx_path = os.path.join(out_dir, 'input.xlsx')
    xlsx.write(xlsx_path, rows)

    with open(os.path.join(out_dir, 'report.json'), 'w', encoding='utf-8') as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)

    print(f'写出 {seed_path}  ({len(seed)} 题, 双回复 {n_multi})')
    print(f'写出 {xlsx_path}')
    print(f'回复数分布: {rep["ref_response_count_dist"]}')
    print(f'answer_quality: {rep["answer_quality_dist"]}')
    print(f'score.value: {rep["score_value_dist"]}')


if __name__ == '__main__':
    main()
