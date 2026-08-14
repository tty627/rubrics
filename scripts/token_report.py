#!/usr/bin/env python3
"""token 消耗报表 —— 回答「这个项目到目前为止一共烧了多少 token」。

用法：
  python3 scripts/token_report.py            # 总账
  python3 scripts/token_report.py --by stage # 按流水线步骤
  python3 scripts/token_report.py --by model # 按模型
  python3 scripts/token_report.py --watch    # 每 10 秒刷新，跑批时挂着看

数据来自 cache/_tokens.json，由 lib/llm.py 的 meter() 在每次**真实**调用后累加
（缓存命中不计，因为没有实际消耗）。这本账只增不减、不滚存，
和 cache/_events.jsonl 不同 —— 后者按 RP_EV_MAX 滚存，历史会被截掉，算不准累计值。

⚠️ 这里只统计本项目自己发出的调用。外部接口的账单以对方后台为准。
"""
import argparse
import json
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKENS = os.environ.get('RP_TOKENS',
                        os.path.join(REPO, 'cache', '_tokens.json'))


def load():
    try:
        with open(TOKENS, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        sys.exit(f'读取 {TOKENS} 失败: {e}')


def human(n):
    for unit, div in (('B', 1e9), ('M', 1e6), ('K', 1e3)):
        if n >= div:
            return f'{n / div:.2f}{unit}'
    return str(int(n))


def render(db, by):
    if not db:
        print(f'账本为空：{TOKENS}\n（还没有真实调用，或全部命中缓存）')
        return

    rows = list(db.values())
    tot = {k: sum(r.get(k, 0) for r in rows)
           for k in ('calls', 'prompt', 'completion', 'reasoning', 'cached_prompt')}
    total_tokens = tot['prompt'] + tot['completion']

    print('=' * 78)
    print(f'Token 总消耗    {human(total_tokens):>12}   '
          f'(输入 {human(tot["prompt"])} + 输出 {human(tot["completion"])})')
    print(f'总调用次数      {tot["calls"]:>12,}')
    if tot['reasoning']:
        print(f'其中思维链      {human(tot["reasoning"]):>12}   '
              f'占输出 {tot["reasoning"] / max(tot["completion"], 1):.0%}')
    if tot['cached_prompt']:
        print(f'命中提示缓存    {human(tot["cached_prompt"]):>12}   '
              f'占输入 {tot["cached_prompt"] / max(tot["prompt"], 1):.0%}'
              f'   ← 这部分通常计费更低')
    if tot['calls']:
        print(f'平均每次调用    {total_tokens / tot["calls"]:>12,.0f} tokens')
    print('=' * 78)

    # 分组聚合
    agg = {}
    for r in rows:
        k = r.get(by, '?') if by != 'both' else f'{r["stage"]} / {r["model"]}'
        e = agg.setdefault(k, {'calls': 0, 'prompt': 0, 'completion': 0})
        for f in ('calls', 'prompt', 'completion'):
            e[f] += r.get(f, 0)

    label = {'stage': '流水线步骤', 'model': '模型', 'both': '步骤 / 模型'}[by]
    w = max([len(str(k)) for k in agg] + [len(label)])
    print(f'\n{label:<{w}}  {"调用":>8} {"输入":>10} {"输出":>10} '
          f'{"合计":>10} {"占比":>7}')
    print('-' * (w + 50))
    for k, e in sorted(agg.items(), key=lambda x: -(x[1]['prompt'] + x[1]['completion'])):
        s = e['prompt'] + e['completion']
        print(f'{k:<{w}}  {e["calls"]:>8,} {human(e["prompt"]):>10} '
              f'{human(e["completion"]):>10} {human(s):>10} '
              f'{s / max(total_tokens, 1):>6.1%}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--by', choices=('stage', 'model', 'both'), default='both')
    ap.add_argument('--watch', action='store_true', help='每 10 秒刷新')
    ap.add_argument('--json', action='store_true', help='输出原始 JSON')
    a = ap.parse_args()

    if a.json:
        print(json.dumps(load(), ensure_ascii=False, indent=2))
        return
    if not a.watch:
        render(load(), a.by)
        return
    try:
        while True:
            os.system('clear')
            print(f'token 报表  {time.strftime("%H:%M:%S")}   Ctrl-C 退出\n')
            render(load(), a.by)
            time.sleep(10)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
