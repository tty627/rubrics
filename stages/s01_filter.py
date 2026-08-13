"""步骤 1：入口过滤 —— 真人 query 甄别 + 缺陷判定（改写 / 弃用）。

流程位置见 docs/design/rubric_pipeline_feishu_v2.md §1。产出填 xlsx 的 A、B 两列。

种子集上的两点偏离，都是数据本身决定的：
1. **真人甄别跳过**。该判定的强特征是 eval_sample_prob=0.1，种子集没有这一列，
   且这批数据已是人工筛过的。真实线上流必须补回这一步，它是必需前置而非可选清洗。
2. 本地检测器**只做召回不做判定**，逐条送 LLM 裁决。各检测器精度差异极大——
   多子题、MCQ 准；首字截断约 10% 真阳（「是/的/在」开头的正常句子会大量误报），
   所以检测结果只作为提示喂给模型，不直接定性。宁漏不错。

判定取闭集五类，默认直通。改写会打破 query 与既有参考回复的对齐，
因此把参考回复片段一并给模型：改到回复不再切题就该判弃用，而不是强行改。
"""
import os, re, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import stage

VERDICTS = ['直通', '改写-截断补全', '改写-指代消解', '改写-拆分', '弃用']
WORKERS = int(os.environ.get('RP_WORKERS', 8))
THINK = stage.envflag('RP_THINK', False)          # 分类任务，默认关思维链

SYS = '''你是评测数据的入口质检员。判定一条真人 query 是否需要改写或弃用。

判定结果只能取以下五个值之一：
- 直通：query 可直接用于评分，不做任何改动
- 改写-截断补全：query 明显首部或尾部被截断，补全后语义完整
- 改写-指代消解：query 含无先行词的指代（「这个」「上述」「该方法」），须替换为具体所指
- 改写-拆分：query 含多个互不相关的子问题，需要拆开才能分别评分
- 弃用：无法通过最小改动使其可评分，或改写后与既有参考回复不再对齐

判定原则，严格遵守：
1. **默认直通**。只有「缺陷使评分无法进行」才改。表达不优雅、口语化、缺礼貌用语、
   排版混乱、有错别字——全都直通，不算缺陷。
2. **最小改动**。改写只补必要成分，不重组句子、不润色、不扩写。
3. **对齐优先**。给你的参考回复是针对原 query 写的。若你的改写会让这条回复变得
   不切题，说明改动过大，应判弃用或退回直通。
4. 多个子问题若属于同一主题的递进追问（如「什么是 A？A 和 B 的区别？」），
   属于正常提问，判直通；只有互不相关的多个任务才判改写-拆分。
5. 检测器提示只是线索，不是结论。大部分命中都是误报，你要自己看 query 本身。

只输出 JSON：
{"verdict": "五个值之一", "rewritten": "改写后的 query，直通或弃用时为空字符串",
 "reason": "一句话，不超过40字"}'''


def detect(q):
    """本地检测器集合。只召回可疑信号，不做判定。"""
    d = []
    if len(q.strip()) < 15:
        d.append('超短')
    if re.search(r'[（(]?[1１][)）.、].{2,}?[（(]?[2２][)）.、]', q) or re.search(r'①.*②', q):
        d.append('多子题')
    if re.match(r'^[是的在了和与或但而]', q.strip()):
        d.append('疑似首部截断')          # 精度很低，约 10% 真阳
    if q.strip() and q.strip()[-1] not in '。？！?!.）)」”"…:：':
        d.append('疑似尾部截断')
    if re.search(r'(这个|上述|该|此)(方法|问题|题|函数|公式|理论|过程|实验)', q):
        d.append('疑似指代')
    if re.search(r'[（(][ABCD][)）]|^\s*[A-D][.、]', q, re.M):
        d.append('MCQ模板')
    if q.strip().startswith('{') or '"role"' in q or '```json' in q:
        d.append('json包裹')
    return d


def build(r):
    q = r['question']
    ref = next(iter(r['ref_responses'].values()), '')
    hits = detect(q)
    u = [f'【query】\n{q}\n']
    if ref:
        u.append(f'【针对该 query 的参考回复（节选，判断对齐用）】\n{ref[:600]}\n')
    u.append(f'【检测器命中（仅线索，多为误报）】{hits or "无"}')
    return [{'role': 'system', 'content': SYS},
            {'role': 'user', 'content': '\n'.join(u)}], hits


def main():
    m = stage.pick('RP_M_FILTER', 'generator')
    recs = stage.read_jsonl(stage.SEED)
    print(f'步骤 1 入口过滤: {len(recs)} 条, 模型={m.name}, thinking={THINK}')

    def work(r):
        msgs, hits = build(r)
        obj, meta = stage.json_call(m, msgs, stage='s01', thinking=THINK)
        v = obj.get('verdict', '直通')
        if v not in VERDICTS:                     # 模型自造标签一律退回直通，不猜
            v, obj['reason'] = '直通', f'[标签越界:{v}] ' + str(obj.get('reason', ''))[:40]
        rw = (obj.get('rewritten') or '').strip()
        if v.startswith('改写') and not rw:       # 说要改却没给改写文本，同样退回
            v = '直通'
        return {**r, 'verdict': v, 'rewritten': rw if v.startswith('改写') else '',
                'filter_reason': obj.get('reason', ''), 'detectors': hits,
                'query_eff': rw if v.startswith('改写') and rw else r['question'],
                '_meta': meta}

    out, _ = stage.run(work, recs, workers=WORKERS, desc='s01')
    stage.stat_cached([r.pop('_meta') for r in out])
    stage.write_jsonl('s01_filter.jsonl', out)

    c = Counter(r['verdict'] for r in out)
    changed = sum(v for k, v in c.items() if k != '直通')
    print('\n=== 步骤 1 结果 ===')
    for k in VERDICTS:
        print(f'  {k:<14}{c.get(k, 0)}')
    print(f'  改写+弃用率    {changed / max(len(out), 1):.1%}  (PLAN 预期 4%-8%，显著超出说明保守约束没生效)')
    print(f'  检测器命中     {dict(Counter(d for r in out for d in r["detectors"]))}')
    if changed:
        print('\n  被改动的条目：')
        for r in out:
            if r['verdict'] != '直通':
                print(f'    {r["rid"]} [{r["verdict"]}] {r["filter_reason"]}')
                print(f'         原: {r["question"][:60]!r}')
                if r['rewritten']:
                    print(f'         改: {r["rewritten"][:60]!r}')


if __name__ == '__main__':
    main()
