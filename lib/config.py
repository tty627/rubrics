"""模型端点配置：加载 config/models.json、按角色取模型、校验异质性硬约束。

约束出处见 CLAUDE.md「Critical Constraints」与 docs/design/PLAN.md §1：
  步骤 6  多模型聚合：需 ≥2 个 generator 且 family 互异（同系列共享盲区）
  步骤 11 RIFT 诊断  ：diagnoser 需异质组合
  步骤 12 判分       ：judge 的 family 必须不同于所有 generator（避免自偏好偏差）

前两条与第三条的区别：步骤 6/12 缺了会让结果失效，属硬失败；步骤 11 单一
diagnoser 仍能跑免池诊断（Phase 2 就是这么用的），只降覆盖，故只告警。

直接运行会校验配置并对每个端点做一次真实探活：
    python3 lib/config.py
"""
import json, os, sys, time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from lib.llm import Model, Pool

CONFIG = os.environ.get('RP_MODELS', os.path.join(_ROOT, 'config', 'models.json'))
_cache = None


def load(path=None, check=True):
    """读 models.json → {name: Model|Pool}，按文件顺序。违反硬约束直接抛。

    models.json 里可以定义 Pool：把一组端点标记同一个 pool_group（任意字符串），
    load() 会把同组端点合并成一个 Pool 对象，name 是这组端点名的「+」拼接。
    Pool 成员必须是同一 family，否则构造抛异常——混用会破坏步骤 6/12 的异质性约束。

    Pool 的用途是把同一角色的负载摊到多个端点上：实测单个 GLM 端点开思维链
    单次约 32s，20 条题的步骤 3 要跑 55 分钟，而另一个端点整个闲置。
    """
    global _cache
    if _cache is not None and path is None:
        return _cache
    p = path or CONFIG
    if not os.path.exists(p):
        raise FileNotFoundError(
            f'缺少 {p}。复制 config/models.json.example 为 config/models.json 并填端点。')
    with open(p, encoding='utf-8') as f:
        raw = json.load(f)

    models = {}
    pools = {}
    for d in raw:
        m = Model(name=d['name'], model_id=d['model_id'], base_url=d['base_url'],
                  api_key=d.get('api_key', 'EMPTY'), family=d.get('family'),
                  timeout=d.get('timeout', 180), roles=d.get('roles', []),
                  reasoning=d.get('reasoning', False),
                  max_tokens=d.get('max_tokens', 4096),
                  no_think_extra=d.get('no_think_extra'),
                  # 键不存在 → 用默认温度；键存在但为 null → 不发 temperature 字段
                  temperature=(d['temperature'] if 'temperature' in d
                               else '__default__'),
                  # 走流式 SSE。用于绕开网关响应超时（见 Model.stream 文档）
                  stream=d.get('stream', False))
        pg = d.get('pool_group')
        if pg:
            pools.setdefault(pg, []).append(m)
        else:
            if m.name in models:
                raise ValueError(f'models.json 中 name 重复: {m.name}')
            models[m.name] = m

    # 合并每个 pool_group
    for pg, members in pools.items():
        pool = Pool(members)
        if pool.name in models:
            raise ValueError(f'pool_group={pg} 生成的 name="{pool.name}" 与已有 Model 冲突')
        models[pool.name] = pool

    if check:
        hard, soft = inspect(models)
        for s in soft:
            print(f'  [配置告警] {s}')
        if hard:
            raise ValueError('模型配置违反硬约束：\n  - ' + '\n  - '.join(hard))
    if path is None:
        _cache = models
    return models


def inspect(ms):
    """返回 (硬失败, 软告警) 两个文字列表，都为空表示配置健康。"""
    gen = [m for m in ms.values() if 'generator' in m.roles]
    gf = {m.family for m in gen}
    hard, soft = [], []

    if len(gen) < 2:
        soft.append(f'步骤 6 聚合线需 ≥2 个 generator，当前 {len(gen)} 个（lean 主线未做聚合，仅告警）')
    elif len(gf) < 2:
        soft.append(f'步骤 6 聚合线需 generator 的 family 互异，当前只有 {sorted(gf)}（lean 主线未做聚合，仅告警）')

    for j in [m for m in ms.values() if 'judge' in m.roles]:
        if j.family in gf:
            hard.append(f'步骤 12 judge "{j.name}" 的 family={j.family} 与 generator '
                        f'同系列（generator families={sorted(gf)}），判分会虚高')
    if not any('judge' in m.roles for m in ms.values()):
        soft.append('未配置 judge，步骤 12 判分不可用（Phase 1-3 不需要）')

    dg = [m for m in ms.values() if 'diagnoser' in m.roles]
    df = {m.family for m in dg}
    if not dg:
        soft.append('未配置 diagnoser，步骤 11 RIFT 诊断不可用')
    elif len(df) < 2:
        soft.append(f'步骤 11 diagnoser 只有 family={sorted(df)}，'
                    f'免池诊断可跑但失效模式覆盖会偏窄')
    return hard, soft


def by_role(role, path=None):
    """取带某角色的全部模型，保持 models.json 顺序。"""
    return [m for m in load(path).values() if role in m.roles]


def one(role, path=None):
    """取该角色的第一个模型。没有则抛。"""
    ms = by_role(role, path)
    if not ms:
        raise ValueError(f'models.json 里没有 roles 含 "{role}" 的模型')
    return ms[0]


def get(name, path=None):
    """按 name 取单个模型。"""
    ms = load(path)
    if name not in ms:
        raise ValueError(f'未知模型 "{name}"，可用: {list(ms)}')
    return ms[name]


def _probe():
    """真实打一次每个端点，确认 model_id、鉴权、非空返回都对。"""
    from lib import llm
    ms = load()
    print(f'配置: {CONFIG}\n')
    print(f'{"name":<15}{"family":<10}{"roles":<26}{"延迟":<9}结果')
    print('-' * 78)
    ok = 0
    for m in ms.values():
        t0 = time.time()
        try:
            txt, meta = llm.call(
                m, [{'role': 'user', 'content': '只回复两个字：可用'}],
                stage='_probe', max_tokens=m.max_tokens, use_cache=False,
                retries=2, thinking=False)
            dt = f'{time.time() - t0:.1f}s'
            u = meta.get('usage', {})
            print(f'{m.name:<15}{m.family:<10}{",".join(m.roles):<26}{dt:<9}'
                  f'OK {txt[:14]!r} (reasoning={u.get("reasoning_tokens", 0)})')
            ok += 1
        except Exception as e:
            print(f'{m.name:<15}{m.family:<10}{",".join(m.roles):<26}'
                  f'{time.time() - t0:.1f}s'.ljust(60) + f'失败 {repr(e)[:90]}')
    print('-' * 78)
    print(f'{ok}/{len(ms)} 个端点可用')

    gf = sorted({m.family for m in ms.values() if 'generator' in m.roles})
    jf = sorted({m.family for m in ms.values() if 'judge' in m.roles})
    df = sorted({m.family for m in ms.values() if 'diagnoser' in m.roles})
    print(f'\n步骤 6  generator families : {gf}')
    print(f'步骤 11 diagnoser families : {df}')
    print(f'步骤 12 judge families     : {jf}  (须与 generator 无交集)')
    return 0 if ok == len(ms) else 1


if __name__ == '__main__':
    sys.exit(_probe())
