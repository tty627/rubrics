"""LLM 客户端：纯标准库，带磁盘缓存与并发。OpenAI 兼容协议。"""
import json, os, hashlib, time, urllib.request, urllib.error, threading
from concurrent.futures import ThreadPoolExecutor

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.environ.get('RP_CACHE', os.path.join(_ROOT, 'cache'))
_lock = threading.Lock()


MAX_BUDGET = 32768          # 空 content 逐轮加预算的上限
_EMPTY_RETRY = 'empty-content'

# 思维链跑飞的自适应记忆：键为 (模型名, 步骤)。
# 检测一次跑飞要白烧约 200s（推理吃光预算才知道），而跑飞是按 prompt 模板成片出现的
# ——GLM 在 s03 的 R_w 提示上连挂数条。攒够 STRIKES 次就让该步后续调用直接从
# 关思维链起步，把浪费从「每次调用 200s」压到「每个步骤 2 次」。
# 进程级，不落盘：新进程重新学，但配合磁盘缓存，重跑的代价可以忽略。
_runaway = {}
_rlock = threading.Lock()
STRIKES = int(os.environ.get('RP_RUNAWAY_STRIKES', 2))

# --- 调用事件流水 ---------------------------------------------------------
# 缓存文件只有 text 和 usage，没有模型名、没有输入，也看不到「正在飞」的请求，
# 所以旁观者无从知道此刻哪个模型在跑哪道题。这里补一条 append-only 流水：
# 每次调用落 start / end（或 hit / err），tools/watch.py 靠配对 start-end 还原在飞请求。
# 写失败一律吞掉——观测设施不能反过来弄挂流水线。
EVENTS = os.environ.get('RP_EVENTS', os.path.join(CACHE_DIR, '_events.jsonl'))
EV_CHARS = int(os.environ.get('RP_EV_CHARS', 400))       # 输入/输出各留多少字
EV_MAX = int(os.environ.get('RP_EV_MAX', 64)) * (1 << 20)   # 超过就滚存一次
_elock = threading.Lock()
_seq = 0


def _preview(messages):
    """取最后一条 user 消息和 system 开头：前者是题目，后者能区分是 RET 的哪一跳。"""
    usr = next((m.get('content', '') for m in reversed(messages)
                if m.get('role') == 'user'), '')
    sys_ = next((m.get('content', '') for m in messages
                 if m.get('role') == 'system'), '')
    return ' '.join(str(usr).split())[:EV_CHARS], ' '.join(str(sys_).split())[:60]


def _log(**rec):
    global _seq
    if not EVENTS:
        return
    try:
        rec['t'] = time.time()
        rec['pid'] = os.getpid()
        line = json.dumps(rec, ensure_ascii=False) + '\n'
        with _elock:
            os.makedirs(os.path.dirname(EVENTS) or '.', exist_ok=True)
            _seq += 1
            if _seq % 256 == 0 and os.path.exists(EVENTS) \
                    and os.path.getsize(EVENTS) > EV_MAX:
                os.replace(EVENTS, EVENTS + '.1')
            with open(EVENTS, 'a', encoding='utf-8') as f:
                f.write(line)
    except Exception:
        pass


def _eid():
    global _seq
    with _elock:
        _seq += 1
        return f'{os.getpid()}-{_seq}'


class Model:
    """一个模型端点。base_url 为 OpenAI 兼容的 /v1。

    reasoning     : 是否推理模型（思维链走 reasoning_content，且计入 completion 预算）
    max_tokens    : 该模型的默认预算；推理模型要给足，否则正文被思维链挤空
    no_think_extra: 关闭思维链的厂商私有参数，形如
                    {"chat_template_kwargs": {"enable_thinking": false}}
                    各家写法不同，故放配置而不写死在代码里
    """

    def __init__(self, name, model_id, base_url, api_key, family=None, timeout=180,
                 roles=None, reasoning=False, max_tokens=4096, no_think_extra=None):
        self.name = name
        self.model_id = model_id
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.family = family or name
        self.timeout = timeout
        self.roles = list(roles or [])
        self.reasoning = reasoning
        self.max_tokens = max_tokens
        self.no_think_extra = no_think_extra or {}


class Pool:
    """一组可互换的端点，按最少在飞请求数派发。

    用途是把同一个角色的负载摊到多个端点上：实测单个 GLM 端点开思维链
    单次约 32s，20 条题的步骤 3 要跑 55 分钟，而另一个端点整个闲置。

    **成员必须是同一 family**，否则轮转会静默破坏流程的异质性约束——
    步骤 6 要求多个 generator 的 family 互异、步骤 12 要求 judge 与 generator
    不同源，这些判定都是按 Model.family 做的。构造时强制校验。

    派发按「当前在飞最少」而非轮询：端点快慢差 30 倍时（deepseek 15s vs
    glm 0.5s），轮询会让快端点空等慢端点。
    """

    def __init__(self, members, name=None):
        if not members:
            raise ValueError('Pool 不能为空')
        fam = {m.family for m in members}
        if len(fam) > 1:
            raise ValueError(f'Pool 成员 family 必须一致，当前 {sorted(fam)}——'
                             f'混用会破坏步骤 6/12 的异质性约束')
        self.members = list(members)
        self.name = name or '+'.join(m.name for m in members)
        self.family = members[0].family
        self.roles = list(members[0].roles)
        self.model_id = members[0].model_id
        self.reasoning = any(m.reasoning for m in members)
        self.max_tokens = min(m.max_tokens for m in members)
        self.no_think_extra = members[0].no_think_extra
        self._inflight = {m.name: 0 for m in members}
        self._plock = threading.Lock()

    def acquire(self):
        with self._plock:
            m = min(self.members, key=lambda x: self._inflight[x.name])
            self._inflight[m.name] += 1
            return m

    def release(self, m):
        with self._plock:
            self._inflight[m.name] = max(0, self._inflight[m.name] - 1)

    def inflight(self):
        with self._plock:
            return dict(self._inflight)


def _key(model, messages, temperature, max_tokens, extra):
    # Pool 用 model_id 而非 base_url 做身份：同一请求派到哪个副本都该命中同一条缓存，
    # 否则副本数一变缓存全废
    u = '' if isinstance(model, Pool) else model.base_url
    blob = json.dumps({'m': model.model_id, 'u': u, 'msg': messages,
                       't': temperature, 'mt': max_tokens, 'x': extra},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


def _cache_path(stage, k):
    d = os.path.join(CACHE_DIR, stage)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, k + '.json')


def call(model, messages, stage='misc', temperature=0.0, max_tokens=None,
         retries=4, extra=None, use_cache=True, thinking=None):
    """返回 (text, meta)。命中缓存不计费。

    max_tokens 缺省用 model.max_tokens。thinking=False 时附加 model.no_think_extra
    关掉思维链（结构化抽取这类任务不需要推理，能省一大截 token）。

    推理模型把思维链算进 completion 预算：预算不够时服务端照样返回 200，但
    content 是空串。这里视为失败、加倍预算重试，且**绝不把空串写进缓存**——
    否则这条记录会永久命中空缓存，事后无从排查。
    """
    extra = dict(extra or {})
    if thinking is False and model.no_think_extra:
        extra.update(model.no_think_extra)
    budget = max_tokens or model.max_tokens
    k = _key(model, messages, temperature, budget, extra)   # 缓存键锚定请求值，不随重试漂移
    send = dict(extra)          # 实际发出的参数，恢复策略可以改它而不动缓存键
    cp = _cache_path(stage, k)
    usr, sys_ = _preview(messages)
    eid = _eid()
    if use_cache and os.path.exists(cp):
        with open(cp, encoding='utf-8') as f:
            c = json.load(f)
        _log(ev='hit', id=eid, stage=stage, model=model.name,
             prompt=usr, sys=sys_, out=' '.join(c['text'].split())[:EV_CHARS])
        return c['text'], {'cached': True, 'model': model.name}

    last, cur = None, budget
    nothink = bool(thinking is False and model.no_think_extra)
    if not nothink and model.no_think_extra and \
            _runaway.get((model.name, stage), 0) >= STRIKES:
        nothink, send = True, {**send, **model.no_think_extra}   # 该步已知会跑飞
    pool = model if isinstance(model, Pool) else None
    for att in range(retries):
        t0 = time.time()
        ep = pool.acquire() if pool else model      # Pool 时才有端点选取，否则原样
        _log(ev='start', id=eid, stage=stage, model=model.name, model_id=ep.model_id,
             endpoint=ep.name, prompt=usr, sys=sys_, budget=cur, att=att, nothink=nothink)
        try:
            payload = {'model': ep.model_id, 'messages': messages,
                       'temperature': temperature, 'max_tokens': cur, **send}
            req = urllib.request.Request(
                f'{ep.base_url}/chat/completions',
                data=json.dumps(payload, ensure_ascii=False).encode(),
                headers={'Content-Type': 'application/json',
                         'Authorization': f'Bearer {ep.api_key}'})
            with urllib.request.urlopen(req, timeout=ep.timeout) as r:
                obj = json.loads(r.read().decode())
            ch = obj['choices'][0]
            text = (ch['message'].get('content') or '').strip()
            usage = obj.get('usage', {})
            if not text:
                # 两种成因，处置相反，必须分开：
                # a) 思维链跑飞——推理吃光整个预算却一字正文未出。加预算只会让跑飞的
                #    链跑更久，接着撞 timeout，实测 GLM 在 SMILES/化学题上就这样连挂
                #    三条。正确处置是关掉思维链重试，一次就能拿到正文。
                # b) 单纯预算不够——正文写到一半被截。这时才该加预算。
                rt = usage.get('reasoning_tokens') or 0
                if not nothink and model.no_think_extra and rt >= cur * 0.9:
                    send.update(model.no_think_extra)
                    nothink = True
                    with _rlock:
                        kk = (model.name, stage)
                        _runaway[kk] = _runaway.get(kk, 0) + 1
                        n = _runaway[kk]
                    raise RuntimeError(
                        f'{_EMPTY_RETRY}: 思维链跑飞 (reasoning={rt} 吃光预算 {cur})，'
                        f'改为关思维链重试 [{stage} 第 {n} 次'
                        f'{"，后续该步直接关" if n >= STRIKES else ""}]')
                prev, cur = cur, min(cur * 2, MAX_BUDGET)
                raise RuntimeError(
                    f'{_EMPTY_RETRY}: content 为空 (finish={ch.get("finish_reason")}, '
                    f'reasoning_tokens={rt}, 预算 {prev}→{cur})')
            with _lock:
                with open(cp, 'w', encoding='utf-8') as f:
                    json.dump({'text': text, 'usage': usage, 'model': model.name,
                               'nothink_fallback': nothink and thinking is not False},
                              f, ensure_ascii=False)
            _log(ev='end', id=eid, stage=stage, model=model.name, dt=time.time() - t0,
                 usage=usage, nothink=nothink, endpoint=ep.name,
                 out=' '.join(text.split())[:EV_CHARS])
            if pool:
                pool.release(ep)
            return text, {'cached': False, 'model': model.name, 'usage': usage,
                          'budget': cur, 'nothink_fallback': nothink and thinking is not False}
        except Exception as e:
            if pool:
                pool.release(ep)
            last = e
            _log(ev='err', id=eid, stage=stage, model=model.name,
                 dt=time.time() - t0, att=att, endpoint=ep.name, msg=repr(e)[:200])
            # 预算不足是确定性失败，加完预算立刻重试，退避只对网络类错误有意义
            if att < retries - 1 and _EMPTY_RETRY not in str(e):
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
