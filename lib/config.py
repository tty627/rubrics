"""模型端点配置、精确角色选择和异源判分约束。"""
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from lib.llm import Model, Pool

CONFIG = os.environ.get('RP_MODELS', os.path.join(_ROOT, 'config', 'models.json'))
# 各大厂商闭源系列。grounder（权威答案源）只能取自这里。
CLOSED_SOURCE_FAMILIES = {'anthropic', 'openai', 'google'}
_cache = None


def load(path=None, check=True):
    """读取 models.json；必需角色缺失或判分模型同源时直接失败。"""
    global _cache
    if _cache is not None and path is None:
        return _cache
    target = path or CONFIG
    if not os.path.exists(target):
        raise FileNotFoundError(
            f'缺少 {target}。复制 config/models.json.example 为 config/models.json 并填端点。')
    with open(target, encoding='utf-8') as stream:
        raw = json.load(stream)

    models = {}
    pools = {}
    for item in raw:
        model = Model(
            name=item['name'], model_id=item['model_id'], base_url=item['base_url'],
            api_key=item.get('api_key', 'EMPTY'), family=item.get('family'),
            timeout=item.get('timeout', 180), roles=item.get('roles', []),
            reasoning=item.get('reasoning', False), max_tokens=item.get('max_tokens', 4096),
            no_think_extra=item.get('no_think_extra'),
            temperature=(item['temperature'] if 'temperature' in item else '__default__'),
            stream=item.get('stream', False))
        group = item.get('pool_group')
        if group:
            pools.setdefault(group, []).append(model)
        elif model.name in models:
            raise ValueError(f'models.json 中 name 重复: {model.name}')
        else:
            models[model.name] = model

    for group, members in pools.items():
        pool = Pool(members)
        if pool.name in models:
            raise ValueError(f'pool_group={group} 生成的 name="{pool.name}" 与已有模型冲突')
        models[pool.name] = pool

    if check:
        errors = inspect(models)
        if os.environ.get('RP_ALLOW_OPEN_GROUNDER') == '1':
            print('⚠️  RP_ALLOW_OPEN_GROUNDER=1：grounder 用开源模型，'
                  'canonical 答案正确性不保证（临时放行）')
        if errors:
            raise ValueError('模型配置违反硬约束：\n  - ' + '\n  - '.join(errors))
    if path is None:
        _cache = models
    return models


def inspect(models, allow_open_grounder=None):
    """返回全部硬错误；唯一主线不提供缺角色或同源模型的降级路径。

    `allow_open_grounder` 显式传 True/False 时覆盖环境开关；缺省读
    `RP_ALLOW_OPEN_GROUNDER`（临时放行，见下）。测试用它强制走闭源判定，
    不受环境变量干扰。
    """
    if allow_open_grounder is None:
        allow_open_grounder = os.environ.get('RP_ALLOW_OPEN_GROUNDER') == '1'
    # 必需角色 = 流水线真正会 pick 的角色。grounder 取代了原 solver：
    # 可核验答案的权威解由闭源最强模型出（阶段 20），不再有独立 solver 角色。
    by_role_map = {
        role: [model for model in models.values() if role in model.roles]
        for role in ('generator', 'diagnoser', 'grounder', 'judge', 'veto',
                     'pool_mid', 'pool_weak')
    }
    errors = []
    for role, members in by_role_map.items():
        if not members:
            errors.append(f'未配置必需角色 {role}')

    generator_families = {model.family for model in by_role_map['generator']}
    judge_families = {model.family for model in by_role_map['judge']}
    for judge in by_role_map['judge']:
        if judge.family in generator_families:
            errors.append(
                f'judge "{judge.name}" family={judge.family} 与 generator 同源')
    for reviewer in by_role_map['veto']:
        if reviewer.family in generator_families | judge_families:
            errors.append(
                f'veto "{reviewer.name}" family={reviewer.family} 必须异于 generator 和 judge')

    # grounder 是可核验题正确答案的唯一权威来源，必须是各厂商闭源最强模型：
    # 自建开源端点解错了没人拦，而下游 22 的程序化核验无条件相信 answer_canonical。
    # RP_ALLOW_OPEN_GROUNDER=1 是临时放行：闭源凭据失效时先用开源占位把机制跑通，
    # 但 canonical 答案正确性不保证。默认（不设）仍然硬拦 —— 这是设计红线，别默认放开。
    if not allow_open_grounder:
        for ground in by_role_map['grounder']:
            if ground.family not in CLOSED_SOURCE_FAMILIES:
                errors.append(
                    f'grounder "{ground.name}" family={ground.family} 不在闭源厂商列表 '
                    f'{sorted(CLOSED_SOURCE_FAMILIES)}；权威答案不得由自建开源端点提供')
    return errors


def by_role(role, path=None):
    return [model for model in load(path).values() if role in model.roles]


def one(role, path=None):
    models = by_role(role, path)
    if not models:
        raise ValueError(f'models.json 里没有 roles 含 "{role}" 的模型')
    return models[0]


def get(name, path=None):
    models = load(path)
    if name not in models:
        raise ValueError(f'未知模型 "{name}"，可用: {list(models)}')
    return models[name]


def _probe():
    from lib import llm
    models = load()
    print(f'配置: {CONFIG}\n')
    print(f'{"name":<15}{"family":<12}{"roles":<36}{"延迟":<9}结果')
    print('-' * 90)
    ok = 0
    for model in models.values():
        started = time.time()
        try:
            text, meta = llm.call(
                model, [{'role': 'user', 'content': '只回复两个字：可用'}],
                stage='_probe', max_tokens=model.max_tokens, use_cache=False,
                retries=2, thinking=False)
            elapsed = f'{time.time() - started:.1f}s'
            usage = meta.get('usage', {})
            print(f'{model.name:<15}{model.family:<12}{",".join(model.roles):<36}{elapsed:<9}'
                  f'OK {text[:14]!r} (reasoning={usage.get("reasoning_tokens", 0)})')
            ok += 1
        except Exception as error:
            print(f'{model.name:<15}{model.family:<12}{",".join(model.roles):<36}'
                  f'{time.time() - started:.1f}s 失败 {repr(error)[:90]}')
    print('-' * 90)
    print(f'{ok}/{len(models)} 个端点可用')
    return 0 if ok == len(models) else 1


if __name__ == '__main__':
    sys.exit(_probe())
