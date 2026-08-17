"""LLM 客户端：纯标准库，带磁盘缓存与并发。OpenAI 兼容协议。"""
import json, os, hashlib, time, urllib.request, urllib.error, threading
from concurrent.futures import ThreadPoolExecutor

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.environ.get('RP_CACHE', os.path.join(_ROOT, 'cache'))
_lock = threading.Lock()


MAX_BUDGET = 32768          # 空 content 逐轮加预算的上限
_EMPTY_RETRY = 'empty-content'

# 多天连跑要能扛住服务端重启、网络抖动、机器迁移。默认 10 次、退避封顶 90s，
# 单次调用最坏约 12 分钟内自愈；配合磁盘缓存，服务端挂十分钟也只是变慢不会丢数据。
RETRIES = int(os.environ.get('RP_RETRIES', 10))
BACKOFF_CAP = float(os.environ.get('RP_BACKOFF_CAP', 90))
# 请求 UA。留空会用 urllib 默认的 "Python-urllib/3.x"，被 Cloudflare 类
# 前置网关判为脚本流量直接 502（api.opentech.top 实测），必须显式设。
UA = os.environ.get('RP_UA', 'curl/8.5.0')

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


# --- token 计量 -----------------------------------------------------------
# 外部 API 按 token 计费，必须能随时回答「到目前为止一共花了多少」。
# 事件流水 _events.jsonl 里虽有 usage，但它按 RP_EV_MAX 滚存，历史会被截掉，
# 累计值算不准。所以另立一本**只增不减、永不滚存**的账：
#   cache/_tokens.json   聚合总账（按 stage × model 分组）
# 用法：python3 scripts/token_report.py
TOKENS = os.environ.get('RP_TOKENS', os.path.join(CACHE_DIR, '_tokens.json'))
_tlock = threading.Lock()


def meter(stage, model_name, model_id, usage):
    """把一次调用的 token 记进总账。缓存命中不计（没有真实消耗）。

    写失败一律吞掉 —— 计量是观测设施，不能反过来弄挂流水线。
    """
    if not usage:
        return
    try:
        pt = int(usage.get('prompt_tokens') or 0)
        ct = int(usage.get('completion_tokens') or 0)
        rt = int((usage.get('completion_tokens_details') or {}).get(
            'reasoning_tokens') or usage.get('reasoning_tokens') or 0)
        cached = int((usage.get('prompt_tokens_details') or {}).get(
            'cached_tokens') or 0)
        if not (pt or ct):
            return
        key = f'{stage}|{model_name}|{model_id}'
        with _tlock:
            try:
                with open(TOKENS, encoding='utf-8') as f:
                    db = json.load(f)
            except Exception:
                db = {}
            e = db.setdefault(key, {'stage': stage, 'model': model_name,
                                    'model_id': model_id, 'calls': 0,
                                    'prompt': 0, 'completion': 0,
                                    'reasoning': 0, 'cached_prompt': 0})
            e['calls'] += 1
            e['prompt'] += pt
            e['completion'] += ct
            e['reasoning'] += rt
            e['cached_prompt'] += cached
            tmp = TOKENS + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(db, f, ensure_ascii=False, indent=1)
            os.replace(tmp, TOKENS)      # 原子替换，并发下不会读到半个文件
    except Exception:
        pass


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
    temperature   : 覆盖默认温度。填 null 表示**整个字段不发**——
                    Claude 系和 kimi 经网关代理时收到 temperature=0.0 会返回
                    HTTP 400，省略该字段即正常。默认 0.0 是为了可复现。
    stream        : 走流式 SSE 取结果。语义与非流式完全一致（同样的缓存键、
                    预算翻倍、思维链跑飞检测），只是分片读回后拼接。
                    留着备用：某些网关只在长响应上超时，流式能保住连接。
                    注意 api.opentech.top 的 502 **不是**超时问题，见 UA 说明。
    """

    def __init__(self, name, model_id, base_url, api_key, family=None, timeout=180,
                 roles=None, reasoning=False, max_tokens=4096, no_think_extra=None,
                 temperature='__default__', stream=False):
        self.name = name
        self.model_id = model_id
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.family = family or name
        self.timeout = timeout
        self.roles = list(roles or [])
        self.reasoning = reasoning
        self.max_tokens = max_tokens
        # '__default__' = 用调用方传的温度；None = 整个字段不发；数值 = 强制覆盖
        self.temperature = temperature
        self.stream = bool(stream)
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


def _read_sse(resp):
    """读 SSE 流，返回与非流式同构的 (choice, usage)。

    只负责拼接，不做任何判定 —— 空正文、思维链跑飞、预算不足的处置逻辑
    在 call() 里共用一份，流式与非流式不允许出现第二套口径。
    reasoning_content 分片单独累计：它不进正文，但要能算出 reasoning_tokens，
    否则「思维链跑飞」检测在流式下永远不触发（usage 里没有该字段时用长度兜底）。
    """
    parts, reason, usage, finish = [], [], {}, None
    for raw in resp:
        line = raw.decode('utf-8', 'replace').strip()
        if not line.startswith('data:'):
            continue
        body = line[5:].strip()
        if body == '[DONE]':
            break
        try:
            obj = json.loads(body)
        except ValueError:
            continue
        if obj.get('usage'):
            usage = obj['usage']
        for ch in obj.get('choices') or []:
            d = ch.get('delta') or {}
            if d.get('content'):
                parts.append(d['content'])
            if d.get('reasoning_content'):
                reason.append(d['reasoning_content'])
            if ch.get('finish_reason'):
                finish = ch['finish_reason']
    text = ''.join(parts)
    if reason and not usage.get('reasoning_tokens'):
        # 流式常不给 usage。按字符数粗估，只用于跑飞判定的量级比较
        usage = {**usage, 'reasoning_tokens': len(''.join(reason)) // 2}
    return {'message': {'content': text}, 'finish_reason': finish}, usage


def call(model, messages, stage='misc', temperature=0.0, max_tokens=None,
         retries=None, extra=None, use_cache=True, thinking=None):
    """返回 (text, meta)。命中缓存不计费。

    max_tokens 缺省用 model.max_tokens。thinking=False 时附加 model.no_think_extra
    关掉思维链（结构化抽取这类任务不需要推理，能省一大截 token）。

    推理模型把思维链算进 completion 预算：预算不够时服务端照样返回 200，但
    content 是空串。这里视为失败、加倍预算重试，且**绝不把空串写进缓存**——
    否则这条记录会永久命中空缓存，事后无从排查。
    """
    if retries is None:
        retries = RETRIES
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
            # 按端点覆盖温度。Claude/kimi 经网关收到 temperature=0.0 会 400，
            # 配置里填 "temperature": null 即整个字段不发。
            ov = getattr(ep, 'temperature', '__default__')
            if ov is None:
                payload.pop('temperature', None)
            elif ov != '__default__':
                payload['temperature'] = ov
            # UA 必须显式设。urllib 默认发 "Python-urllib/3.x"，
            # api.opentech.top 前面的 Cloudflare 对它一律回 502（裸文本
            # "error code: 502"，无 API 层 JSON），换成常规 UA 立刻 200。
            # 排查时容易被响应头 Server-Timing: cfOrigin;dur=3601 误导成
            # 上游超时 —— 实际同一 payload 用 curl 首字节 1.7s 就返回。
            hdr = {'Content-Type': 'application/json',
                   'Authorization': f'Bearer {ep.api_key}',
                   'User-Agent': UA}
            streaming = getattr(ep, 'stream', False)
            if streaming:
                # 流式只为保持连接、绕开网关响应超时，不改任何判定语义。
                # 缓存键不含 stream：同一 (model_id, messages, 预算) 无论走哪种
                # 传输方式都该命中同一条缓存，否则开关一动缓存全废。
                payload['stream'] = True
                payload['stream_options'] = {'include_usage': True}
                hdr['Accept'] = 'text/event-stream'
            req = urllib.request.Request(
                f'{ep.base_url}/chat/completions',
                data=json.dumps(payload, ensure_ascii=False).encode(),
                headers=hdr)
            with urllib.request.urlopen(req, timeout=ep.timeout) as r:
                if streaming:
                    ch, usage = _read_sse(r)
                else:
                    obj = json.loads(r.read().decode())
                    ch, usage = obj['choices'][0], obj.get('usage', {})
            text = (ch['message'].get('content') or '').strip()
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
            meter(stage, model.name, ep.model_id, usage)
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
                time.sleep(min(2 ** att * 1.5, BACKOFF_CAP))
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
