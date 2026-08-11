"""流水线监视面板：看清楚「现在哪一步在跑、用的哪个模型、跑到哪了、下一步是什么」。

只读。不改流水线代码，不需要在跑的进程配合，随时可开可关。

用法：
    python3 tools/watch.py            # 全屏刷新，Ctrl-C 退出
    python3 tools/watch.py --once     # 打一次快照就退出，便于管道 / 贴日志
    python3 tools/watch.py -n 5       # 刷新间隔 5 秒（默认 2）
    python3 tools/watch.py --no-probe # 不探端点，纯本地信号

四个信息源，都是旁路观测：
  /proc/<pid>           哪个 stage 在跑、跑了多久、RP_* 环境变量、socket 实连到哪个端点
  cache/<stage>/*.json  每完成一次非缓存调用落一个文件 → 进度、速率、token、最新产出预览
  data/*.jsonl          各步产出的行数与时间
  端点 /metrics         sglang 暴露 num_running_reqs/num_queue_reqs，能看出是不是别人在打这台机器

关于模型归属：缓存文件里只有 text 和 usage，没记模型名（见 lib/llm.py 的写盘处），
所以「这一步用的哪个模型」是两条独立证据交叉验证的——进程的 RP_M_* 环境变量按
lib/stage.pick 的规则推导出候选，再用 socket 的远端 IP 反查 models.json 的 base_url
确认。两者不一致时面板会标出来，不会替你圆场。
"""
import json, os, re, shutil, socket, struct, sys, threading, time, unicodedata
import urllib.request, urllib.error
from collections import deque

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

DATA = os.environ.get('RP_OUT', os.path.join(_ROOT, 'data'))
CACHE = os.environ.get('RP_CACHE', os.path.join(_ROOT, 'cache'))
EVENTS = os.environ.get('RP_EVENTS', os.path.join(CACHE, '_events.jsonl'))
TAIL = 512 * 1024   # 首次只读尾部这么多字节，历史再长也不拖慢启动
INFLIGHT_MAX = 1800 # 超过这么久还没配上 end 的 start，当作进程被杀掉的残留
WINDOW = 120        # 速率窗口（秒）：太短会被单次长调用抖成 0，太长则反应迟钝
PROBE_EVERY = 10    # 端点探测间隔（秒）：远大于刷新间隔，免得监视本身变成负载
STALL = 90          # 超过这么久没有新缓存文件就算停滞

# cache 子目录, 脚本, 选模型的环境变量, 输入 jsonl, 输出 jsonl, 每条调用数
# 调用数只用于进度条和 ETA，估偏了不影响其它显示；'ret:X' 表示按 s03 的 RET 策略现算
PIPE = [
    ('s01',          's01_filter.py',       'RP_M_FILTER', None,                's01_filter.jsonl',             1),
    ('s02',          's02_context.py',      'RP_M_GEN',    's01_filter.jsonl',  's02_context.jsonl',            1),
    ('s02_5',        's02_5_route.py',      'RP_M_ROUTE',  's02_context.jsonl', 's02_5_route.jsonl',            2),
    ('s03_batch',    's03_perspective.py',  'RP_M_GEN',    's02_5_route.jsonl', 's03_perspective_batch.jsonl',    'ret:batch'),
    ('s03_hybrid',   's03_perspective.py',  'RP_M_GEN',    's02_5_route.jsonl', 's03_perspective_hybrid.jsonl',   'ret:hybrid'),
    ('s03_faithful', 's03_perspective.py',  'RP_M_GEN',    's02_5_route.jsonl', 's03_perspective_faithful.jsonl', 'ret:faithful'),
]

VIEW = {'tokens': False}    # --tokens：展开 stage 明细与端点侧累计

PIPE_BY = {p[0]: p for p in PIPE}


def all_stages():
    """PIPE 里登记的在前，cache/ 下新冒出来的追加在后。

    别人加了新 stage 不用改这个文件就能看到——只是没登记的那些估不出总数。
    """
    out = [p[0] for p in PIPE]
    try:
        for e in sorted(os.scandir(CACHE), key=lambda e: e.name):
            if e.is_dir() and not e.name.startswith('_') and e.name not in out:
                out.append(e.name)
    except OSError:
        pass
    return out


def guess_output(cdir):
    """没登记的 stage：拿 cache 目录名去 data/ 里前缀匹配产出文件。"""
    try:
        for e in sorted(os.scandir(DATA), key=lambda e: e.name):
            if e.name.startswith(cdir) and e.name.endswith('.jsonl'):
                return e.path
    except OSError:
        pass
    return None


C = {'r': '\x1b[0m', 'd': '\x1b[2m', 'b': '\x1b[1m', 'g': '\x1b[32m', 'y': '\x1b[33m',
     'e': '\x1b[31m', 'c': '\x1b[36m', 'm': '\x1b[35m'}
if os.environ.get('NO_COLOR') or not sys.stdout.isatty():
    C = {k: '' for k in C}


# ---------- 宽度：中文按两列算，否则所有表格都会错位 ----------

def _cw(ch):
    return 2 if unicodedata.east_asian_width(ch) in 'WF' else 1


def w(s):
    return sum(_cw(c) for c in s)


def cut(s, n):
    if w(s) <= n:
        return s
    out, cur = [], 0
    for ch in s:
        if cur + _cw(ch) > n - 1:
            break
        out.append(ch)
        cur += _cw(ch)
    return ''.join(out) + '…'


def pad(s, n):
    s = cut(s, n)
    return s + ' ' * max(0, n - w(s))


def rpad(s, n):
    s = cut(s, n)
    return ' ' * max(0, n - w(s)) + s


def dur(sec):
    sec = int(sec)
    if sec < 60:
        return f'{sec}s'
    if sec < 3600:
        return f'{sec // 60}m{sec % 60:02d}s'
    return f'{sec // 3600}h{sec % 3600 // 60:02d}m'


def num(n):
    for lim, unit in ((1e9, 'G'), (1e6, 'M'), (1e3, 'k')):
        if n >= lim:
            return f'{n / lim:.1f}{unit}'
    return str(int(n))


def acc(d, key, u):
    """把一次调用的 usage 累进 d[key]。字段可能是 None，统一兜成 0。"""
    a = d.setdefault(key, {'n': 0, 'p': 0, 'c': 0, 'r': 0})
    a['n'] += 1
    a['p'] += u.get('prompt_tokens') or 0
    a['c'] += u.get('completion_tokens') or 0
    a['r'] += u.get('reasoning_tokens') or 0
    return a


# ---------- 模型配置 ----------

def load_models():
    """读 models.json。check=False：面板不该因为配置违反硬约束就打不开。"""
    try:
        from lib import config
        return config.load(check=False)
    except Exception as e:
        print(f'{C["e"]}读 models.json 失败: {e}{C["r"]}')
        return {}


def resolve_hosts(models):
    """base_url 的主机名 → IP，用于把 socket 的远端地址反查成模型名。
    多个模型可能共用一个网关（如 glm/qwen-utility/compassjudger 都在 opencompass 那台），
    所以一个 IP 对应的是候选集合，不是单个模型。"""
    ip2names, hostcache = {}, {}
    for m in models.values():
        hp = m.base_url.split('://', 1)[-1].split('/', 1)[0]
        host, _, port = hp.partition(':')
        port = int(port or 80)
        if host not in hostcache:
            try:
                hostcache[host] = {ai[4][0] for ai in socket.getaddrinfo(host, None)}
            except OSError:
                hostcache[host] = set()
        for ip in hostcache[host]:
            ip2names.setdefault((ip, port), []).append(m.name)
    return ip2names


def default_generator(models):
    """复刻 lib/stage.pick 的缺省分支：取该角色在 models.json 里的第一个。"""
    for m in models.values():
        if 'generator' in m.roles:
            return m.name
    return '?'


# ---------- /proc：谁在跑 ----------

def _sock_inodes(pid):
    ino = set()
    try:
        for fd in os.listdir(f'/proc/{pid}/fd'):
            try:
                l = os.readlink(f'/proc/{pid}/fd/{fd}')
            except OSError:
                continue
            if l.startswith('socket:['):
                ino.add(l[8:-1])
    except OSError:
        pass
    return ino


def _tcp_table():
    """全系统 TCP 表：inode → (远端 ip, port, 是否 ESTABLISHED)。"""
    t = {}
    for path, v6 in (('/proc/net/tcp', False), ('/proc/net/tcp6', True)):
        try:
            lines = open(path).read().splitlines()[1:]
        except OSError:
            continue
        for ln in lines:
            p = ln.split()
            if len(p) < 10:
                continue
            h, _, pt = p[2].partition(':')
            try:
                if v6:
                    raw = bytes.fromhex(h)
                    ip = socket.inet_ntop(socket.AF_INET6, b''.join(
                        raw[i:i + 4][::-1] for i in range(0, 16, 4)))
                    if ip.startswith('::ffff:'):
                        ip = ip[7:]
                else:
                    ip = socket.inet_ntoa(struct.pack('<I', int(h, 16)))
                t[p[9]] = (ip, int(pt, 16), p[3] == '01')
            except (ValueError, OSError):
                continue
    return t


def procs(ip2names):
    """扫出所有在跑的 stage 进程。"""
    out, tcp, tick = [], None, os.sysconf('SC_CLK_TCK')
    try:
        uptime = float(open('/proc/uptime').read().split()[0])
    except OSError:
        uptime = 0
    for pid in os.listdir('/proc'):
        if not pid.isdigit():
            continue
        try:
            cmd = open(f'/proc/{pid}/cmdline', 'rb').read().decode(errors='replace')
        except OSError:
            continue
        parts = [a for a in cmd.split('\0') if a]
        # 只认「python 直接跑 stages/xxx.py」，把包着它的 shell -c 排除掉
        if len(parts) < 2 or 'python' not in os.path.basename(parts[0]):
            continue
        script = next((a for a in parts[1:] if a.endswith('.py') and 'stages/' in a), None)
        if not script:
            continue
        env = {}
        try:
            for kv in open(f'/proc/{pid}/environ', 'rb').read().decode(errors='replace').split('\0'):
                k, _, v = kv.partition('=')
                if k.startswith('RP_'):
                    env[k] = v
        except OSError:
            pass
        try:
            st = open(f'/proc/{pid}/stat').read()
            started = float(st[st.rfind(')') + 2:].split()[19]) / tick
            elapsed = max(0.0, uptime - started)
        except (OSError, IndexError, ValueError):
            elapsed = 0.0
        try:
            threads = len(os.listdir(f'/proc/{pid}/task'))
        except OSError:
            threads = 0

        if tcp is None:
            tcp = _tcp_table()
        peers = {}
        for i in _sock_inodes(pid):
            e = tcp.get(i)
            if e and e[2]:
                # 一个 IP 可能挂着多个模型（如 glm/compassjudger/qwen-utility 共用网关），
                # 此时只能确定连的是哪台机器，确定不到是哪个模型，所以并列显示而不是拆成多条
                nm = ip2names.get((e[0], e[1]))
                label = '|'.join(nm) if nm else f'{e[0]}:{e[1]}'
                peers[label] = peers.get(label, 0) + 1
        out.append({'pid': int(pid), 'script': os.path.basename(script), 'env': env,
                    'elapsed': elapsed, 'threads': threads, 'peers': peers})
    return sorted(out, key=lambda p: p['pid'])


# ---------- cache：跑到哪了 ----------

class Scan:
    """增量扫描 cache/<stage>：只 stat 新出现的文件。
    缓存目录会长到上万个文件，每轮全量 stat 会让面板自己变成负载。"""

    def __init__(self):
        self.seen = {}      # stage → {文件名: mtime}
        self.newest = {}    # stage → [(mtime, 路径), ...] 最近若干个
        self.pend = deque()     # 待读 usage 的新文件
        self.tok_stage = {}     # stage → 累计 token
        self.tok_model = {}     # 模型名 → 累计 token
        self.unattr = 0         # 早于埋点、没有 model 字段的文件数

    def scan(self, stage):
        d = os.path.join(CACHE, stage)
        known = self.seen.setdefault(stage, {})
        fresh = []
        try:
            with os.scandir(d) as it:
                for e in it:
                    if not e.name.endswith('.json'):
                        continue
                    if e.name in known:
                        continue
                    try:
                        known[e.name] = e.stat().st_mtime
                    except OSError:
                        continue
                    fresh.append((known[e.name], e.path))
                    self.pend.append((stage, e.path))
        except FileNotFoundError:
            return {'n': 0, 'rate': 0.0, 'last': 0.0}
        if fresh:
            nw = (self.newest.get(stage, []) + fresh)
            self.newest[stage] = sorted(nw, reverse=True)[:5]
        now = time.time()
        recent = sum(1 for t in known.values() if now - t <= WINDOW)
        return {'n': len(known),
                'rate': recent / (WINDOW / 60.0),
                'last': max(known.values()) if known else 0.0}

    def drain(self, budget=1500):
        """读新缓存文件里的 usage，累计 token。缓存文件写完就不再变，所以每个只读一次。

        限量是为了 Phase 2 那种上万文件的场景：全量读一次要几秒，
        摊到几帧里读完，首帧就不会卡住。
        """
        for _ in range(min(budget, len(self.pend))):
            stage, path = self.pend.popleft()
            try:
                with open(path, encoding='utf-8') as f:
                    r = json.load(f)
            except (OSError, ValueError):
                continue
            u = r.get('usage') or {}
            acc(self.tok_stage, stage, u)
            m = r.get('model')
            if m:
                acc(self.tok_model, m, u)
            else:
                self.unattr += 1        # 早于埋点的调用，归不到具体模型
        return len(self.pend)

    def totals(self):
        t = {'n': 0, 'p': 0, 'c': 0, 'r': 0}
        for a in self.tok_stage.values():
            for k in t:
                t[k] += a[k]
        return t

    def preview(self, stage, k=3):
        out = []
        for mt, path in self.newest.get(stage, [])[:k]:
            try:
                d = json.load(open(path, encoding='utf-8'))
            except (OSError, ValueError):
                continue
            u = d.get('usage') or {}
            txt = ' '.join((d.get('text') or '').split())
            out.append((mt, u.get('completion_tokens', 0), u.get('reasoning_tokens') or 0, txt))
        return out


# ---------- 事件流水：谁在跑、跑的什么内容 ----------

class Events:
    """增量 tail lib/llm.py 落的 cache/_events.jsonl，配对 start/end 还原在飞请求。

    只认自己读到的 start：面板启动前就发出、启动后才回来的请求会配不上对，
    这类 end 直接丢弃，不会污染统计。
    """

    def __init__(self, path=EVENTS):
        self.path = path
        self.off = None
        self.rest = b''
        self.open = {}                  # id → start 事件
        self.done = deque(maxlen=60)    # 最近完成
        self.win = deque(maxlen=4000)   # 窗口内事件，用于按模型统计
        self.pids = set()
        self.exists = False

    def poll(self):
        try:
            sz = os.path.getsize(self.path)
        except OSError:
            return
        self.exists = True
        if self.off is None:                    # 首读：只从尾部 TAIL 字节开始
            self.off = max(0, sz - TAIL)
            self.rest = b'' if self.off == 0 else b'\0'   # 丢掉开头的半行
        if sz < self.off:                       # 被滚存或清空了
            self.off, self.rest, self.open = 0, b'', {}
        try:
            with open(self.path, 'rb') as f:
                f.seek(self.off)
                buf = f.read()
        except OSError:
            return
        self.off += len(buf)
        data = self.rest + buf
        *lines, self.rest = data.split(b'\n')
        for ln in lines:
            if ln.startswith(b'\0') or not ln.strip():
                continue
            try:
                e = json.loads(ln.decode('utf-8', 'replace'))
            except ValueError:
                continue
            ev, eid = e.get('ev'), e.get('id')
            self.pids.add(e.get('pid'))
            self.win.append(e)
            if ev == 'start':
                self.open[eid] = e
            elif ev in ('end', 'err'):
                s = self.open.pop(eid, None)
                if s:
                    e['prompt'], e['sys'] = s.get('prompt', ''), s.get('sys', '')
                    e['wait'] = e['t'] - s['t']
                    self.done.append(e)

        now = time.time()
        for eid, s in list(self.open.items()):   # 清掉被杀进程留下的孤儿 start
            if now - s['t'] > INFLIGHT_MAX or not os.path.exists(f'/proc/{s.get("pid")}'):
                self.open.pop(eid, None)
        while self.win and now - self.win[0]['t'] > WINDOW:
            self.win.popleft()

    def inflight(self):
        return sorted(self.open.values(), key=lambda e: e['t'])

    def by_model(self):
        """窗口内按模型汇总：在飞 / 完成 / 缓存命中 / 平均耗时 / 输出 token。"""
        agg = {}
        for e in self.win:
            a = agg.setdefault(e.get('model', '?'),
                               {'end': 0, 'hit': 0, 'err': 0, 'dt': 0.0, 'tok': 0})
            ev = e.get('ev')
            if ev == 'end':
                a['end'] += 1
                a['dt'] += e.get('dt', 0)
                a['tok'] += (e.get('usage') or {}).get('completion_tokens', 0)
            elif ev in ('hit', 'err'):
                a[ev] += 1
        for e in self.open.values():
            agg.setdefault(e.get('model', '?'),
                           {'end': 0, 'hit': 0, 'err': 0, 'dt': 0.0, 'tok': 0})
        for m, a in agg.items():
            a['fly'] = sum(1 for e in self.open.values() if e.get('model') == m)
        return agg


# ---------- 预期调用数 ----------

_jl = {}


def lines(name):
    """读 data/<name> 的记录数与内容，按 (mtime,size) 缓存。"""
    p = name if os.path.isabs(name) else os.path.join(DATA, name)
    try:
        st = os.stat(p)
    except OSError:
        return 0, []
    key = (p, st.st_mtime, st.st_size)
    if _jl.get('k') != key:
        try:
            recs = [json.loads(l) for l in open(p, encoding='utf-8') if l.strip()]
        except (OSError, ValueError):
            return 0, []
        _jl.clear()
        _jl['k'], _jl['v'] = key, recs
    return len(_jl['v']), _jl['v']


def seed_name(ps):
    """s01 的输入：优先取在跑进程的 RP_SEED，否则 phase1 子集优先。"""
    for p in ps:
        if p['script'].startswith('s01') and p['env'].get('RP_SEED'):
            return p['env']['RP_SEED']
    env = os.environ.get('RP_SEED')
    if env:
        return env
    return 'seed_phase1.jsonl' if os.path.exists(
        os.path.join(DATA, 'seed_phase1.jsonl')) else 'seed.jsonl'


def expected(spec, inp, ps):
    """预期调用总数。返回 0 表示估不出来（进度条留空，不瞎猜）。"""
    n, recs = lines(inp) if inp else (0, [])
    if isinstance(spec, int):
        return n * spec
    if not spec.startswith('ret:'):
        return 0
    strategy = spec.split(':', 1)[1]
    w1 = 1
    for p in ps:
        if p['script'].startswith('s03'):
            w1 = int(p['env'].get('RP_W1', 1) or 1)
    total = 0
    for r in recs:
        form = r.get('rubric_form')
        if form == 'gated_answer':
            continue        # 固定 3 视角，不调模型
        if form == 'analytic':
            u = len(r.get('scenarios') or [])
        else:               # multi_part：只有 open block 走 RET
            u = len([b for b in (r.get('blocks') or []) if b.get('block_type') == 'open'])
        if not u:
            continue
        if strategy == 'batch':
            total += 1                          # 一次批量出全部场景的视角
        else:
            # R_h 批量 1 + R_w(ℓ=1) w1 次 + 新场景批量 1 + 每个场景 R_w(ℓ=2) 各 1
            total += 1 + w1 + 1 + (u + 1)
    return total


# ---------- 端点探测（后台线程，UI 永不阻塞） ----------

class Probe(threading.Thread):
    daemon = True

    def __init__(self, models):
        super().__init__()
        self.models = models
        self.state = {}
        self.has_metrics = {}
        self.reps = {}      # 模型 → [副本状态]，见 _merge
        self.stop = threading.Event()

    def _merge(self, name, s):
        """同一个 URL 后面可能挂多个副本（deepseek 实测 2 个），/metrics 每次随机命中一个，
        所以累计计数器会在几个数值之间来回跳。计数器只增不减，据此认副本身份：
        新采样若不小于某个已知副本、且量级相近，就认为是同一个，否则算新发现的副本。
        """
        p = s.get('ptok')
        if p is None:
            return
        reps = self.reps.setdefault(name, [])
        best, bd = None, None
        for r in reps:
            if p + 1 >= r['ptok']:                  # 计数器不会倒退
                d = abs(p - r['ptok']) / max(r['ptok'], 1)
                if d < 0.5 and (bd is None or d < bd):
                    best, bd = r, d
        if best is None:
            reps.append({**s, 'p0': p, 'g0': s.get('gtok', 0)})
        else:
            best.update(s)

    def totals(self, name):
        """把各副本的累计量加起来，同时给出「面板开启以来」的增量。"""
        reps = self.reps.get(name) or []
        if not reps:
            return None
        return {'n': len(reps),
                'ptok': sum(r.get('ptok', 0) for r in reps),
                'gtok': sum(r.get('gtok', 0) for r in reps),
                'dp': sum(r.get('ptok', 0) - r['p0'] for r in reps),
                'dg': sum(r.get('gtok', 0) - r['g0'] for r in reps)}

    def _get(self, url, timeout=6):
        req = urllib.request.Request(url, headers={'Accept': '*/*'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode(errors='replace')

    # 面板关心的服务端指标 → 内部字段名。sglang 和 vLLM 命名不同，各列一份别名。
    _WANT = {
        'run': ('num_running_reqs', 'num_requests_running'),
        'que': ('num_queue_reqs', 'num_waiting_reqs', 'num_requests_waiting'),
        'ptok': ('prompt_tokens_total',),
        'gtok': ('generation_tokens_total',),
        'tps': ('gen_throughput', 'avg_generation_throughput_toks_per_s'),
        'kv': ('token_usage', 'gpu_cache_usage_perc'),
        'hit': ('cache_hit_rate', 'gpu_prefix_cache_hit_rate'),
    }
    _SUM = {'run', 'que', 'ptok', 'gtok'}       # 计数类可跨分片相加，比率类不能

    def _metrics(self, base):
        """sglang / vLLM 都在服务根暴露 Prometheus。抓请求数、累计 token、吞吐、KV 占用。"""
        root = base.rsplit('/v1', 1)[0]
        txt = self._get(root + '/metrics', timeout=6)
        out = {}
        for ln in txt.splitlines():
            if ln.startswith('#') or ' ' not in ln:
                continue
            name, _, val = ln.rpartition(' ')
            name = name.split('{', 1)[0].split(':', 1)[-1].strip()
            try:
                v = float(val)
            except ValueError:
                continue
            for key, alias in self._WANT.items():
                if name in alias:
                    out[key] = out.get(key, 0) + v if key in self._SUM else v
        return out

    def run(self):
        while not self.stop.is_set():
            for m in self.models.values():
                t0 = time.time()
                try:
                    txt = self._get(m.base_url + '/models', timeout=8)
                    ids = {d.get('id') for d in (json.loads(txt).get('data') or [])}
                    st = {'up': True, 'lat': time.time() - t0,
                          'known': m.model_id in ids, 'err': ''}
                except Exception as e:
                    st = {'up': False, 'lat': time.time() - t0, 'known': False,
                          'err': repr(e)[:60]}
                if st['up'] and self.has_metrics.get(m.name, True):
                    try:
                        # 多抓几次：负载均衡是随机的，一次只能命中一个副本。
                        # 中间要停一下——连着抓会被连接级粘连打到同一个后端。
                        for i in range(3):
                            if i:
                                self.stop.wait(0.4)
                            s = self._metrics(m.base_url)
                            self._merge(m.name, s)
                            st.update(s)
                        self.has_metrics[m.name] = 'run' in st
                    except Exception:
                        self.has_metrics[m.name] = False   # 405/404：网关不暴露，别再试
                self.state[m.name] = st
            self.stop.wait(PROBE_EVERY)


# ---------- 渲染 ----------

BAR = '█'
DOT = '░'


class Out:
    """按「段」收集输出，好让 fit() 能按终端高度裁剪。

    段内分两部分：head（标题和表头，不裁）和 items（条目，从尾部按 unit 行成组裁掉）。
    prio 越大越先被裁。
    """

    def __init__(self):
        self.secs = []
        self.cur = None

    def sec(self, title, prio=5, unit=1, keep=None):
        # 缺省保住一个完整条目：留个只剩标题的空壳没有信息量，
        # 那种情况该由 fit() 把整段丢掉，而不是留一行「省略 N 项」占位
        self.cur = {'head': [title], 'items': [], 'unit': unit, 'prio': prio,
                    'keep': unit if keep is None else keep, 'cut': 0}
        self.secs.append(self.cur)

    def head(self, line):
        self.cur['head'].append(line)

    def append(self, line):
        if self.cur is None:
            self.sec(line, prio=0)
        else:
            self.cur['items'].append(line)


_ANSI = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]')


def _secname(s):
    return _ANSI.sub('', s['head'][0]).strip().lstrip('▶').split('（')[0].strip() or '?'


def fit(secs, rows):
    """把各段压进 rows 行以内。rows=0 表示不限（--once）。

    两个阶段：先从最不重要的段成组砍尾部条目，还超就**整段丢掉**。
    留一堆只剩标题的空壳段没有意义——九个标题就吃掉小终端的一大半。
    """
    live = list(secs)
    if not rows:
        return _flat(live, [])

    def size(s):
        return len(s['head']) + len(s['items']) + (1 if s['cut'] else 0)

    def total(extra=0):
        return sum(size(s) for s in live) + max(0, len(live) - 1) + extra

    order = lambda: sorted(live, key=lambda s: -s['prio'])
    while total() > rows:                       # 阶段一：砍条目
        for s in order():
            if len(s['items']) > s['keep']:
                del s['items'][-min(s['unit'], len(s['items']) - s['keep']):]
                s['cut'] += 1
                break
        else:
            break

    hidden = []
    while total(1 if hidden else 0) > rows:     # 阶段二：整段丢弃
        drop = next((s for s in order() if s['prio'] > 1), None)
        if not drop:
            break
        live.remove(drop)
        hidden.append(_secname(drop))
    return _flat(live, hidden)[:rows]


def _flat(live, hidden):
    out = []
    for i, s in enumerate(live):
        if i:
            out.append('')
        out += s['head'] + s['items']
        if s['cut']:
            out.append(f'  {C["d"]}… 省略 {s["cut"]} 项{C["r"]}')
    if hidden:
        out.append(f'{C["d"]}窗口放不下，已隐藏：{" ".join(hidden)}'
                   f'（拉高窗口，或 --once 看全）{C["r"]}')
    return out


def bar(done, total, n=14):
    """估算总数只是参考，实跑超出很正常（JSON 重试、R_w 导出新场景都会加调用），
    所以超了就如实显示 >100% 并标黄，不假装刚好跑满。"""
    if not total:
        return C['d'] + '─' * n + C['r'] + '    ?  '
    f = done / total
    col = C['y'] if f > 1.02 else (C['g'] if f >= 0.999 else (C['c'] if f > 0 else C['d']))
    k = min(n, int(f * n))
    return f'{col}{BAR * k}{C["d"]}{DOT * (n - k)}{C["r"]} {col}{f * 100:4.0f}%{C["r"]}'


def render(scan, ev, probe, models, ip2names, interval):
    ps = procs(ip2names)
    running = {p['script'] for p in ps}
    gen0 = default_generator(models)
    ev.poll()
    TW = max(80, shutil.get_terminal_size((130, 40)).columns)
    L = Out()
    ttl = time.strftime('%H:%M:%S')
    L.sec(f'{C["b"]}rubrics 流水线监视{C["r"]}  {C["d"]}{ttl}  刷新 {interval}s  '
          f'速率窗口 {WINDOW}s  Ctrl-C 退出{C["r"]}', prio=0)

    # --- 在跑的进程 ---（每个进程 3-4 行，整块裁）
    L.sec(f'{C["c"]}▶ 在跑{C["r"]}', prio=2, unit=4)
    if not ps:
        L.append(f'  {C["d"]}没有 stages/*.py 在跑{C["r"]}')
    for p in ps:
        envs = '  '.join(f'{k}={v}' for k, v in sorted(p['env'].items())) or '（全默认）'
        # 该脚本按 lib/stage.pick 的规则会选哪个模型
        var = next((v[2] for v in PIPE if v[1] == p['script']), None)
        want = p['env'].get(var) if var else None
        guess = want or gen0
        seen = ', '.join(f'{k}×{v}' for k, v in sorted(p['peers'].items())) or '此刻无连接'
        cand = {n for k in p['peers'] for n in k.split('|')}
        if not p['peers']:
            mark = f'{C["d"]}（urllib 每次调用现连现断，采样到空属正常）{C["r"]}'
        elif guess in cand:
            mark = f'{C["g"]}✓ 相符{C["r"]}'
        else:
            mark = f'{C["y"]}⚠ 与推断的模型不符{C["r"]}'
        L.append(f'  {C["b"]}pid {p["pid"]}{C["r"]}  {pad(p["script"], 22)}'
                 f'已跑 {pad(dur(p["elapsed"]), 9)}线程 {p["threads"]}')
        L.append(f'    {C["d"]}环境{C["r"]} {envs}')
        L.append(f'    {C["d"]}模型{C["r"]} {guess}'
                 f'{C["d"]}（{"环境变量指定" if want else "默认取 " + ("generator 第一个" if not var else var + " 未设")}）{C["r"]}'
                 f'   {C["d"]}socket 实连{C["r"]} {seen} {mark}')
        if ev.exists and p['pid'] not in ev.pids:
            L.append(f'    {C["y"]}⚠ 这个进程早于事件流水启动，看不到它的调用内容'
                     f'{C["r"]}{C["d"]}（Python 不热重载 lib/llm.py，重跑后才有）{C["r"]}')

    # --- 各步进度 ---
    L.sec(f'{C["c"]}▶ 进度{C["r"]} {C["d"]}（调用数 = cache/<stage> 的文件数；'
          f'总数为按输入条数估算，仅供 ETA 参考）{C["r"]}', prio=1)
    L.head(f'  {C["d"]}{pad("", 3)}{pad("stage", 14)}{rpad("调用", 6)} /'
           f'{rpad("估总", 6)}  {pad("进度", 21)}{pad("速率", 11)}{pad("ETA", 8)}状态{C["r"]}')
    seed = seed_name(ps)
    now = time.time()
    for cdir in all_stages():
        s = scan.scan(cdir)
        age = now - s['last'] if s['last'] else 0
        meta = PIPE_BY.get(cdir)
        if meta:
            _, script, var, inp, outp, spec = meta
            exp = expected(spec, inp or seed, ps)
            out_p = os.path.join(DATA, outp)
            # s03 三种策略共用一个脚本，用 RP_RET 区分现在跑的是哪一个
            if cdir.startswith('s03_'):
                live = any(p['script'] == script and
                           p['env'].get('RP_RET', 'hybrid') == cdir[4:] for p in ps)
            else:
                live = script in running
        else:                       # 没登记的 stage：估不出总数，只能看有没有新调用
            exp, out_p = 0, guess_output(cdir)
            live = bool(ps) and 0 < age < STALL
        done_file = bool(out_p) and os.path.exists(out_p)
        if s['n'] == 0 and not done_file:
            icon, stat = f'{C["d"]}○{C["r"]}', f'{C["d"]}未开始{C["r"]}'
        elif live:
            if age > STALL:
                icon, stat = f'{C["y"]}▶{C["r"]}', f'{C["y"]}在跑·{dur(age)} 无新调用{C["r"]}'
            else:
                icon, stat = f'{C["g"]}▶{C["r"]}', f'{C["g"]}在跑{C["r"]}'
        elif done_file and os.path.getmtime(out_p) >= s['last']:
            n_out, _ = lines(out_p)
            icon, stat = f'{C["g"]}✓{C["r"]}', f'{C["d"]}完成 {n_out} 行{C["r"]}'
        else:
            icon, stat = f'{C["y"]}⏸{C["r"]}', f'{C["y"]}中断 {dur(age)} 前{C["r"]}'
        # 先把纯文本排好版再上色，否则转义序列会被算进宽度、整张表错位
        rate = f'{s["rate"]:.1f} 次/分' if s['rate'] else '-'
        eta = dur((exp - s['n']) / s['rate'] * 60) \
            if (s['rate'] and exp and exp > s['n']) else '-'
        cells = pad(rate, 11) + pad(eta, 8)
        if not s['rate']:
            cells = C['d'] + cells + C['r']
        L.append(f'  {icon}  {pad(cdir, 14)}{rpad(str(s["n"]), 6)} /'
                 f'{rpad(str(exp or "?"), 6)}  {bar(s["n"], exp)}  {cells}{stat}')

    # --- 端点 ---
    L.sec(f'{C["c"]}▶ 端点{C["r"]} {C["d"]}（run/que 是整台服务的负载，包含别人的请求）{C["r"]}',
          prio=6)
    for m in models.values():
        st = probe.state.get(m.name) if probe else None
        if st is None:
            body = f'{C["d"]}探测中…{C["r"]}'
        elif not st['up']:
            body = f'{C["e"]}✗ 不可达{C["r"]} {C["d"]}{st["err"]}{C["r"]}'
        else:
            if st.get('run') is not None:
                hot = C['y'] if (st.get('que') or 0) > 0 else C['g']
                load = f'   {hot}运行 {st["run"]:.0f}  排队 {st.get("que") or 0:.0f}{C["r"]}'
                if st.get('tps'):
                    load += f'  {C["d"]}出字{C["r"]} {st["tps"]:.0f} tok/s'
                nrep = (probe.totals(m.name) or {}).get('n', 1)
                if nrep > 1:      # 多副本：这些数只是本次命中的那个副本，不是全集群
                    load += f'  {C["y"]}(×{nrep} 副本，此为其一){C["r"]}'
            else:
                load = f'   {C["d"]}（不暴露 /metrics）{C["r"]}'
            kn = '' if st['known'] else f'  {C["e"]}model_id 不在该端点{C["r"]}'
            body = f'{C["g"]}✓{C["r"]} {st["lat"]:.2f}s{load}{kn}'
        L.append(f'  {pad(m.name, 14)}{pad(m.model_id, 34)}'
                 f'{C["d"]}{pad("/".join(m.roles), 20)}{C["r"]}{body}')

    # --- Token 用量：本流水线花掉的（全量缓存，不只是窗口内） ---
    left = scan.drain()
    tt = scan.totals()
    head = f'{C["c"]}▶ Token 用量{C["r"]} {C["d"]}（本流水线，按全量缓存统计'
    head += f'，还有 {left} 个文件待读' if left else ''
    L.sec(head + f'）{C["r"]}', prio=4)
    L.head(f'  {C["d"]}{pad("", 2)}{pad("", 16)}{rpad("调用", 6)}{rpad("输入", 9)}'
           f'{rpad("输出", 9)}{rpad("思维链", 10)}{rpad("占比", 6)}{rpad("输出/次", 9)}{C["r"]}')

    def tok_row(label, a, col=''):
        rr = a['r'] / a['c'] if a['c'] else 0
        rc = C['y'] if rr > 0.85 else ''
        return (f'  {col}{pad(label, 18)}{C["r"]}{rpad(str(a["n"]), 6)}'
                f'{rpad(num(a["p"]), 9)}{rpad(num(a["c"]), 9)}{rpad(num(a["r"]), 10)}'
                f'{rc}{rpad(f"{rr * 100:.0f}%" if a["c"] else "-", 6)}{C["r"]}'
                f'{rpad(num(a["c"] // a["n"]) if a["n"] else "-", 9)}')

    for m, a in sorted(scan.tok_model.items(), key=lambda kv: -kv[1]['c']):
        L.append(tok_row(m, a, C['b']))
    if scan.unattr:
        L.append(f'  {C["d"]}{pad("未标注(早于埋点)", 18)}{rpad(str(scan.unattr), 6)}'
                 f'   缓存文件里没有 model 字段，归不到具体模型；量在下面的 stage 明细里{C["r"]}')
    if VIEW['tokens']:
        L.append(f'  {C["d"]}── 按 stage ──{C["r"]}')
        for s, a in sorted(scan.tok_stage.items()):
            L.append(tok_row('  ' + s, a))
    if tt['n']:
        L.append(tok_row('合计', tt, C['b']) +
                 f'   {C["d"]}总 token{C["r"]} {num(tt["p"] + tt["c"])}')

    # --- 端点侧累计：整机口径，含他人流量 ---
    if VIEW['tokens'] and probe:
        L.sec(f'{C["c"]}▶ 端点侧 token{C["r"]} {C["d"]}（服务进程启动以来的整机累计，'
              f'包含别人的流量）{C["r"]}', prio=7, unit=2)
        for m in models.values():
            st = probe.state.get(m.name) or {}
            tt2 = probe.totals(m.name)
            if not tt2:
                L.append(f'  {pad(m.name, 14)}{C["d"]}不暴露 /metrics，只能看上面的流水线侧{C["r"]}')
                continue
            # 说「已采到」而不是「共有」：负载均衡是随机的，没采到不等于没有
            rep = (f'{C["y"]}已采到 {tt2["n"]} 个副本·合计{C["r"]}' if tt2['n'] > 1
                   else f'{C["d"]}已采到 1 个副本{C["r"]}')
            L.append(f'  {pad(m.name, 14)}{rep}  {C["d"]}累计{C["r"]} 输入 '
                     f'{rpad(num(tt2["ptok"]), 8)} 输出 {rpad(num(tt2["gtok"]), 8)}'
                     f'   {C["d"]}面板开启以来{C["r"]} +{num(tt2["dp"])} / +{num(tt2["dg"])}')
            extra = []
            if st.get('kv') is not None:
                kc = C['e'] if st['kv'] > 0.9 else C['g']
                extra.append(f'{C["d"]}KV 占用{C["r"]} {kc}{st["kv"] * 100:.0f}%{C["r"]}')
            if st.get('hit') is not None:
                extra.append(f'{C["d"]}前缀缓存命中{C["r"]} {st["hit"] * 100:.0f}%')
            if st.get('tps'):
                extra.append(f'{C["d"]}出字{C["r"]} {st["tps"]:.0f} tok/s')
            if extra:
                L.append(f'  {" " * 14}{C["d"]}本次采样命中的那个副本：{C["r"]}' + '  '.join(extra))

    # --- 按模型的负载：glm 和 deepseek 各自在干多少活 ---
    fly = ev.inflight()
    agg = ev.by_model()
    L.sec(f'{C["c"]}▶ 模型负载{C["r"]} {C["d"]}（本流水线自己的调用，最近 {WINDOW}s）{C["r"]}',
          prio=3)
    if not agg:
        L.append(f'  {C["d"]}窗口内没有本流水线的调用'
                 f'{"" if ev.exists else "（还没有 cache/_events.jsonl，跑一次任意 stage 就会有）"}{C["r"]}')
    for m in sorted(agg, key=lambda x: -agg[x]['fly'] - agg[x]['end']):
        a = agg[m]
        avg = f'{a["dt"] / a["end"]:.1f}s' if a['end'] else '-'
        tpm = num(int(a['tok'] / (WINDOW / 60))) + '/分' if a['tok'] else '-'
        fc = C['g'] if a['fly'] else C['d']
        L.append(f'  {pad(m, 14)}{fc}在飞 {a["fly"]:<3}{C["r"]}'
                 f'{C["d"]}完成{C["r"]} {a["end"]:<4}{C["d"]}缓存命中{C["r"]} {a["hit"]:<5}'
                 f'{C["d"]}失败{C["r"]} {a["err"]:<4}'
                 f'{C["d"]}平均{C["r"]} {pad(avg, 9)}{C["d"]}输出{C["r"]} {tpm}')

    # --- 在飞的请求：此刻模型手上正拿着的题 ---
    # 在飞和最近完成是「在跑什么」的正文，最后才裁；每条 2-3 行，整条裁
    L.sec(f'{C["c"]}▶ 在飞的请求{C["r"]} {C["d"]}（{len(fly)} 条，还没返回的）{C["r"]}',
          prio=2, unit=2)
    if not fly:
        L.append(f'  {C["d"]}此刻没有在飞的请求{C["r"]}')
    for e in fly[:6]:
        L.append(f'  {C["g"]}◆{C["r"]} {pad(e.get("model", "?"), 13)}'
                 f'{pad(e.get("stage", "?"), 13)}{C["y"]}已等 {rpad(dur(now - e["t"]), 6)}{C["r"]}'
                 f'  {C["d"]}{cut(e.get("sys", ""), 44)}{C["r"]}')
        L.append(f'      {C["d"]}问{C["r"]} {cut(e.get("prompt", ""), TW - 12)}')

    # --- 最近完成：问什么、答什么，一眼可见 ---
    L.sec(f'{C["c"]}▶ 最近完成{C["r"]} {C["d"]}（时间倒序）{C["r"]}', prio=5, unit=3)
    recent = list(ev.done)[::-1][:5]
    if recent:
        for e in recent:
            u = e.get('usage') or {}
            rt = u.get('reasoning_tokens') or 0
            bad = e.get('ev') == 'err'
            head = f'{C["e"]}✗{C["r"]}' if bad else f'{C["g"]}✓{C["r"]}'
            dt_s = f'{e.get("dt", 0):.1f}s'
            tail = (f'{C["e"]}{cut(e.get("msg", ""), 60)}{C["r"]}' if bad else
                    f'{rpad(num(u.get("completion_tokens", 0)) + " tok", 9)}'
                    + (f'  {C["m"]}think {num(int(rt))}{C["r"]}' if rt else ''))
            L.append(f'  {head} {C["d"]}{time.strftime("%H:%M:%S", time.localtime(e["t"]))}'
                     f' {rpad(dur(now - e["t"]) + " 前", 9)}{C["r"]} '
                     f'{pad(e.get("model", "?"), 13)}{pad(e.get("stage", "?"), 12)}'
                     f'{rpad(dt_s, 7)}  {tail}')
            L.append(f'      {C["d"]}问{C["r"]} {cut(e.get("prompt", ""), TW - 12)}')
            if not bad:
                L.append(f'      {C["d"]}答{C["r"]} {cut(e.get("out", ""), TW - 12)}')
    else:
        # 没有事件流水时退回缓存文件预览：看得到输出，看不到输入和模型归属
        L.append(f'  {C["d"]}事件流水里还没有完成记录，下面退回 cache 文件（无输入、无模型名）{C["r"]}')
        rows = [(mt, cdir, ct, rt, txt) for cdir, *_ in PIPE
                for mt, ct, rt, txt in scan.preview(cdir, 5)]
        rows.sort(reverse=True)
        for mt, cdir, ct, rt, txt in rows[:6]:
            r_s = f'{C["m"]}{pad("think " + num(int(rt)), 12)}{C["r"]}' if rt else ' ' * 12
            L.append(f'  {C["d"]}{time.strftime("%H:%M:%S", time.localtime(mt))} '
                     f'{rpad(dur(now - mt) + " 前", 10)}{C["r"]}  '
                     f'{pad(cdir, 13)}{rpad(num(int(ct)) + " tok", 9)}   {r_s}'
                     f'{cut(txt, max(20, TW - 60))}')

    # --- 数据文件 ---
    L.sec(f'{C["c"]}▶ 产出文件{C["r"]}', prio=8)     # 最不紧要，先裁它
    try:
        fs = sorted((e for e in os.scandir(DATA) if e.name.endswith(('.jsonl', '.json'))),
                    key=lambda e: e.stat().st_mtime, reverse=True)
    except OSError:
        fs = []
    for e in fs[:8]:
        st = e.stat()
        n = sum(1 for _ in open(e.path, encoding='utf-8', errors='replace')) \
            if e.name.endswith('.jsonl') else 0
        L.append(f'  {pad(e.name, 34)}{rpad(str(n) + " 行" if n else "-", 9)}'
                 f'   {C["d"]}{dur(now - st.st_mtime)} 前   {st.st_size // 1024}K{C["r"]}')
    return L.secs


def main():
    a = sys.argv[1:]
    once = '--once' in a
    VIEW['tokens'] = '--tokens' in a
    interval = 2.0
    if '-n' in a:
        interval = float(a[a.index('-n') + 1])
    models = load_models()
    ip2names = resolve_hosts(models)
    scan, ev = Scan(), Events()
    probe = None
    if '--no-probe' not in a and models:
        probe = Probe(models)
        probe.start()
        if once:
            time.sleep(4)       # 一次性快照：给探测线程时间跑完一轮（含多副本发现）
    alt = False
    try:
        if once:
            print('\n'.join(fit(render(scan, ev, probe, models, ip2names, interval), 0)))
            return 0
        # 备用屏 + 藏光标：像 vim/htop 那样独占一屏，退出后原来的终端内容原样回来。
        # 输出被重定向时不发这组序列，免得往管道里灌控制字符
        if sys.stdout.isatty():
            sys.stdout.write('\x1b[?1049h\x1b[?25l')
            alt = True
        while True:
            rows = shutil.get_terminal_size((130, 40)).lines
            secs = render(scan, ev, probe, models, ip2names, interval)
            # 裁到只剩 rows-1 行：写满最后一行会把整屏顶上去，就又滚动了
            L = fit(secs, max(8, rows - 1))
            # 回原点逐行重写、每行清到行尾，最后清掉屏幕剩余部分——全程不产生滚动
            sys.stdout.write('\x1b[H' + '\n'.join(l + '\x1b[K' for l in L) + '\x1b[J')
            sys.stdout.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0
    finally:
        if probe:
            probe.stop.set()
        if alt:
            sys.stdout.write('\x1b[?25h\x1b[?1049l')
            sys.stdout.flush()


if __name__ == '__main__':
    sys.exit(main())
