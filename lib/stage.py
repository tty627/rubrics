"""stage 公共设施：jsonl 读写、强制 JSON 输出的 LLM 调用、按角色选模型。

每个 stage 从 data/ 读前序 jsonl、写自己的 jsonl，彼此只靠文件耦合，
因此任一步都能单独重跑。模型选取一律走这里，便于 Phase 1 换模型对比。
"""
import json, os, sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from lib import llm, config

DATA = os.environ.get('RP_OUT', os.path.join(_ROOT, 'data'))
# Phase 1 用 20 条子集，Phase 2 换回全量 seed.jsonl，靠这个变量切
SEED = os.environ.get('RP_SEED', 'seed.jsonl')


def _path(name):
    return name if os.path.isabs(name) else os.path.join(DATA, name)


def read_jsonl(name):
    p = _path(name)
    if not os.path.exists(p):
        raise FileNotFoundError(f'缺少 {p}，先跑前序步骤')
    with open(p, encoding='utf-8') as f:
        return [json.loads(l) for l in f if l.strip()]


def write_jsonl(name, recs):
    p = _path(name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    print(f'写出 {p}  ({len(recs)} 条)')
    return p


def pick(env, role):
    """按环境变量指定模型名，缺省取该角色的第一个。"""
    name = os.environ.get(env)
    return config.get(name) if name else config.one(role)


def envflag(name, default):
    """读 0/1 环境变量。"""
    v = os.environ.get(name)
    return default if v is None else v not in ('0', 'false', 'False', '')


def json_call(model, messages, stage, json_retries=2, **kw):
    """调 LLM 并解析 JSON，解析失败时把坏输出回灌给模型要求重出。

    与 llm.call 内部 retries 的分工：那层管网络错误和空 content，
    这层只管格式。重试时 messages 变了，缓存键随之改变，不会命中旧的坏结果。
    """
    msgs, last = list(messages), None
    for _ in range(json_retries + 1):
        txt, meta = llm.call(model, msgs, stage=stage, **kw)
        try:
            return llm.extract_json(txt), meta
        except ValueError as e:
            last = e
            msgs = msgs + [
                {'role': 'assistant', 'content': txt[:2000]},
                {'role': 'user', 'content':
                    '上一轮输出不是合法 JSON。只输出 JSON 本体，不要解释文字，'
                    '不要 markdown 代码块包裹。'}]
    raise ValueError(f'{model.name} JSON 解析失败 {json_retries + 1} 次: {last}')


def run(fn, items, workers=8, desc=''):
    """并发跑 fn(item)，丢弃失败项并报告。返回 (成功列表, 失败列表)。"""
    out, errs = llm.parallel_map(fn, items, workers=workers, desc=desc)
    ok = [r for r in out if r is not None]
    if len(ok) < len(items):
        print(f'  [{desc}] 成功 {len(ok)}/{len(items)}')
    return ok, errs


def stat_cached(metas):
    """统计缓存命中，用于判断这轮实际花了多少调用。"""
    n = sum(1 for m in metas if m and m.get('cached'))
    print(f'  缓存命中 {n}/{len(metas)}，实际调用 {len(metas) - n}')
