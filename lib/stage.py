"""编号流水线公共设施：JSONL I/O、LLM 调用和显式模型选择。"""
import importlib.util, json, os, sys, time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from lib import llm, config

DATA = os.environ.get('RP_DATA_ROOT', os.path.join(_ROOT, 'data'))


def _path(name):
    if os.path.isabs(name):
        return name
    # Accept explicit repository-relative paths such as data/foo.jsonl while
    # preserving the historical RP_OUT-relative behavior for bare filenames.
    if name == 'data' or name.startswith(('data/', 'outputs/', 'cache/', 'logs/')):
        return os.path.join(_ROOT, name)
    return os.path.join(DATA, name)


def read_jsonl(name):
    p = _path(name)
    if not os.path.exists(p):
        raise FileNotFoundError(f'缺少 {p}，先跑前序步骤')
    with open(p, encoding='utf-8') as f:
        return [json.loads(l) for l in f if l.strip()]


def write_jsonl(name, recs):
    p = _path(name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    temp = p + '.tmp'
    with open(temp, 'w', encoding='utf-8') as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    os.replace(temp, p)
    print(f'写出 {p}  ({len(recs)} 条)')
    return p


def pick(env, role):
    """按显式环境变量或精确角色选模型；缺失配置立即失败。"""
    name = os.environ.get(env)
    if name:
        return config.get(name)
    return config.one(role)


def envflag(name, default):
    """读 0/1 环境变量。"""
    v = os.environ.get(name)
    return default if v is None else v not in ('0', 'false', 'False', '')


_LOADED = {}


def load(name):
    """按文件名加载同级编号阶段模块并缓存。

    编号文件名以数字开头，不是合法标识符，`import` 用不了。跨阶段复用口径
    （如 24 的可达性探针必须复用 22 的判分 build）只能走这条路。
    """
    if name not in _LOADED:
        path = os.path.join(_ROOT, 'pipeline', name)
        if not os.path.exists(path):
            raise FileNotFoundError(f'缺少阶段文件 {path}')
        spec = importlib.util.spec_from_file_location(
            'pipeline_' + name.replace('.py', ''), path)
        module = importlib.util.module_from_spec(spec)
        # 先登记再执行：阶段模块间若有交叉引用，避免重复加载。
        _LOADED[name] = module
        spec.loader.exec_module(module)
    return _LOADED[name]


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
    """并发跑 fn(item)，返回成功列表和带输入下标的失败列表。"""
    out, errs = llm.parallel_map(fn, items, workers=workers, desc=desc)
    ok = [r for r in out if r is not None]
    if len(ok) < len(items):
        print(f'  [{desc}] 成功 {len(ok)}/{len(items)}，失败下标: {[i for i, _ in errs[:20]]}')
        write_failure_manifest(desc, items, errs)
    return ok, errs


def require_success(errors, stage_name):
    """任何子任务失败都阻断当前运行，禁止部分结果进入后续发布链。"""
    if errors:
        sample = '; '.join(f'#{i}: {message}' for i, message in errors[:5])
        raise RuntimeError(f'{stage_name} 有 {len(errors)} 个子任务失败：{sample}')


def _failure_key(item, index):
    if isinstance(item, dict):
        return str(item.get('rid', item.get('id', index)))
    if isinstance(item, (tuple, list)):
        return '/'.join(str(x) for x in item[:3])
    return str(index)


def write_failure_manifest(desc, items, errs):
    """把所有 stage 的失败追加到统一清单，防止未改造的调用方静默丢题。"""
    path = os.environ.get('RP_FAILURE_MANIFEST',
                          os.path.join(DATA, '_stage_errors.jsonl'))
    if not path or not errs:
        return
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        now = time.strftime('%Y-%m-%dT%H:%M:%S%z')
        with open(path, 'a', encoding='utf-8') as f:
            for index, message in errs:
                f.write(json.dumps({
                    'time': now, 'stage': str(desc), 'index': int(index),
                    'key': _failure_key(items[index], index),
                    'error': str(message)[:500]}, ensure_ascii=False) + '\n')
    except Exception as e:
        # 观测清单不能反过来阻断主流程；终端仍会保留失败下标。
        print(f'  ⚠️ 无法写入失败清单 {path}: {e}')


def errors_by_index(errs):
    """把 parallel_map 的 [(index, error), ...] 转成可持久化映射。"""
    return {int(i): str(message)[:500] for i, message in (errs or [])}


def error_entry(stage_name, key, message):
    """构造统一的内部失败记录，供 jsonl 产物和检查点复用。"""
    return {'stage': str(stage_name), 'key': str(key),
            'error': str(message)[:500]}


def add_stage_errors(record, entries):
    """把失败追加到记录的 _stage_errors，不覆盖上游已有失败。"""
    out = dict(record)
    old = list(out.get('_stage_errors') or [])
    for entry in entries or []:
        if entry and entry not in old:
            old.append(dict(entry))
    if old:
        out['_stage_errors'] = old
    return out


def add_stage_error(record, stage_name, key, message):
    """add_stage_errors 的单条便捷形式。"""
    return add_stage_errors(record, [error_entry(stage_name, key, message)])


def stat_cached(metas):
    """统计缓存命中，用于判断这轮实际花了多少调用。"""
    n = sum(1 for m in metas if m and m.get('cached'))
    print(f'  缓存命中 {n}/{len(metas)}，实际调用 {len(metas) - n}')
