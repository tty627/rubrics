"""LLM 客户端：纯标准库，带磁盘缓存与并发。OpenAI 兼容协议。"""
import json, os, hashlib, time, urllib.request, urllib.error, threading
from concurrent.futures import ThreadPoolExecutor

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.environ.get('RP_CACHE', os.path.join(_ROOT, 'cache'))
_lock = threading.Lock()


class Model:
    """一个模型端点。base_url 为 OpenAI 兼容的 /v1。"""

    def __init__(self, name, model_id, base_url, api_key, family=None, timeout=180):
        self.name = name
        self.model_id = model_id
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.family = family or name
        self.timeout = timeout


def _key(model, messages, temperature, max_tokens, extra):
    blob = json.dumps({'m': model.model_id, 'u': model.base_url, 'msg': messages,
                       't': temperature, 'mt': max_tokens, 'x': extra},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


def _cache_path(stage, k):
    d = os.path.join(CACHE_DIR, stage)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, k + '.json')


def call(model, messages, stage='misc', temperature=0.0, max_tokens=4096,
         retries=4, extra=None, use_cache=True):
    """返回 (text, meta)。命中缓存不计费。"""
    extra = extra or {}
    k = _key(model, messages, temperature, max_tokens, extra)
    cp = _cache_path(stage, k)
    if use_cache and os.path.exists(cp):
        with open(cp, encoding='utf-8') as f:
            c = json.load(f)
        return c['text'], {'cached': True, 'model': model.name}

    payload = {'model': model.model_id, 'messages': messages,
               'temperature': temperature, 'max_tokens': max_tokens, **extra}
    body = json.dumps(payload, ensure_ascii=False).encode()
    last = None
    for att in range(retries):
        try:
            req = urllib.request.Request(
                f'{model.base_url}/chat/completions', data=body,
                headers={'Content-Type': 'application/json',
                         'Authorization': f'Bearer {model.api_key}'})
            with urllib.request.urlopen(req, timeout=model.timeout) as r:
                obj = json.loads(r.read().decode())
            text = obj['choices'][0]['message']['content']
            usage = obj.get('usage', {})
            with _lock:
                with open(cp, 'w', encoding='utf-8') as f:
                    json.dump({'text': text, 'usage': usage}, f, ensure_ascii=False)
            return text, {'cached': False, 'model': model.name, 'usage': usage}
        except Exception as e:
            last = e
            if att < retries - 1:
                time.sleep(2 ** att * 1.5)
    raise RuntimeError(f'{model.name} failed after {retries}: {last}')


def parallel_map(fn, items, workers=8, desc=''):
    """并发跑 fn(item)，返回与 items 同序的结果；异常存为 None 并记录。"""
    out = [None] * len(items)
    errs = []

    def run(i_it):
        i, it = i_it
        try:
            out[i] = fn(it)
        except Exception as e:
            errs.append((i, repr(e)[:200]))

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(run, enumerate(items)))
    if errs and desc:
        print(f'  [{desc}] {len(errs)}/{len(items)} 失败，前3: {errs[:3]}')
    return out, errs


def extract_json(text):
    """从模型输出里抠 JSON。容忍 ```json 包裹和前后废话。"""
    t = text.strip()
    if '```' in t:
        import re
        m = re.search(r'```(?:json)?\s*(.*?)```', t, re.S)
        if m:
            t = m.group(1).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    for a, b in (('{', '}'), ('[', ']')):
        i, j = t.find(a), t.rfind(b)
        if i >= 0 and j > i:
            try:
                return json.loads(t[i:j + 1])
            except Exception:
                continue
    raise ValueError(f'no JSON in output: {t[:200]}')
