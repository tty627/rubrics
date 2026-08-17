"""流水线监视面板：分清「我们打了什么」和「端点上正在发生什么」。

    python3 tools/panel.py            # 全屏刷新，Ctrl-C 退出
    python3 tools/panel.py --once     # 快照，便于贴日志
    python3 tools/panel.py -n 5       # 刷新间隔 5 秒（默认 2）
    python3 tools/panel.py --all      # 展开已完成/未开始的步骤
    python3 tools/panel.py --no-probe # 不探端点，只看本地信号

重写动因（旧面板 watch_v2 的 bug）：它把端点 /metrics 的读数直接印在模型名后面，
读起来像是「这个模型正在跑这么多请求」。实际 /metrics 是**整机口径，含其他租户**，
且多个模型可能共用一个网关。于是出现了 deepseek 显示「运行5 排队9」、glm-ac 显示
「运行0 排队0」，而真相是 5762 次调用全打在 glm-ac 上——deepseek 一次没用过，
那些数字是同机别人的流量；glm-ac 那台网关根本不暴露 /metrics（405），
被当成 0 印了出来。两个数字都不假，但摆的位置让人得出相反的结论。

四个信息源，按可信度排列，面板里也按这个顺序呈现：
  cache/<stage>/*.json  含 model 字段 → 本流水线的调用归属，**唯一权威**
  cache/_events.jsonl   start/end 配对 → 此刻在飞的请求、失败率
  data/*.jsonl          各步产出行数 → 数据口径（在跑 20 条还是 453 条）
  端点 /metrics         整机负载，含他人；拿不到就显式写「不暴露」，绝不填 0
"""
import json, os, shutil, sys, threading, time, urllib.request
from collections import deque

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 复用 watch.py 里与本次 bug 无关、已验证的部分：宽度计算、/proc 扫描、事件配对
from tools.watch import (
    CACHE, DATA, EVENTS, WINDOW, PROBE_EVERY, STALL,
    C, pad, rpad, dur, num, acc, lines, load_models, resolve_hosts, procs, Events,
)

VIEW = {'all': False}

# cache 子目录, 脚本, 输入 jsonl, 输出 jsonl, 每条输入的调用数
#   数字        : 每条输入固定 N 次
#   'perspectives' 等字段名: 累加输入记录该字段的长度
#   'ret'       : s03 按 RET 策略现算
#   'analytic'  : 只有 rubric_form=analytic 的记录才调
#   'criteria'  : 累加 criteria 长度
#   None        : 纯计算步，不调模型
# {S} 会被替换成当前 RET 策略（hybrid/batch/faithful）
PIPE = [
    ('s01',        's01_filter.py',       None,                     's01_filter.jsonl',        1),
    ('s02',        's02_context.py',      's01_filter.jsonl',       's02_context.jsonl',       1),
    # s02_5 是每条 1 次 + hybrid 缺 block 时追一轮修复，所以按 1 估、超出打 +。
    # 旧面板按 2 估，于是跑完的步骤永远停在 52%，看着像卡住了。
    ('s02_5',      's02_5_route.py',      's02_context.jsonl',      's02_5_route.jsonl',       1),
    ('s03_batch',    's03_perspective.py', 's02_5_route.jsonl', 's03_perspective_batch.jsonl',    'ret'),
    ('s03_hybrid',   's03_perspective.py', 's02_5_route.jsonl', 's03_perspective_hybrid.jsonl',   'ret'),
    ('s03_faithful', 's03_perspective.py', 's02_5_route.jsonl', 's03_perspective_faithful.jsonl', 'ret'),
    ('s03b',       's03b_merge.py',       's03_perspective_{S}.jsonl', 's03b_merged_{S}.jsonl', 1),
    ('s04',        's04_criteria.py',     's03b_merged_{S}.jsonl',  's04_criteria.jsonl',      'perspectives'),
    ('s07',        's07_difficulty.py',   's04_criteria.jsonl',     's07_evolved.jsonl',       'analytic'),
    ('s08',        's08_penalties.py',    's07_evolved.jsonl',      's08_penalties.jsonl',     1),
    ('s09',        's09_normalize.py',    's08_penalties.jsonl',    's09_normalized.jsonl',    None),
    ('s11_subj',   's11_diagnose.py',     's09_normalized.jsonl',   's11_diagnosed.jsonl',     'criteria'),
    ('s11_non-',   's11_diagnose.py',     's09_normalized.jsonl',   's11_diagnosed.jsonl',     'criteria'),
    ('s11_ungr',   's11_diagnose.py',     's09_normalized.jsonl',   's11_diagnosed.jsonl',     'criteria'),
]
PIPE_BY = {p[0]: p for p in PIPE}


def strategy(ps):
    """当前 RET 策略：在跑的进程说了算，否则看哪个 s03 产出存在。"""
    for p in ps:
        if p['script'].startswith('s03') and p['env'].get('RP_RET'):
            return p['env']['RP_RET']
    for s in ('hybrid', 'batch', 'faithful'):
        if os.path.exists(os.path.join(DATA, f's03_perspective_{s}.jsonl')):
            return s
    return 'hybrid'


def all_stages(ps):
    """PIPE 登记的在前，cache/ 下新冒出来的追加在后（新 stage 不用改这个文件）。"""
    s = strategy(ps)
    out = [p[0] for p in PIPE]
    try:
        for e in sorted(os.scandir(CACHE), key=lambda e: e.name):
            if e.is_dir() and not e.name.startswith('_') and e.name not in out:
                out.append(e.name)
    except OSError:
        pass
    # 未选中的 s03 策略不显示，免得三行里两行永远是空的
    return [c for c in out if not c.startswith('s03_') or c == f's03_{s}']


def expected(cdir, seed, ps):
    """预期调用总数。返回 0 表示估不出来——进度条留空，不瞎猜。"""
    meta = PIPE_BY.get(cdir)
    if not meta:
        return 0
    _, _, inp, _, spec = meta
    if spec is None:
        return 0
    s = strategy(ps)
    inp = (inp or seed).replace('{S}', s)
    n, recs = lines(inp)
    if isinstance(spec, int):
        return n * spec
    if spec == 'perspectives':
        return sum(len(r.get('perspectives') or []) for r in recs)
    if spec == 'criteria':
        return sum(len(r.get('criteria') or []) for r in recs)
    if spec == 'analytic':
        return sum(1 for r in recs if r.get('rubric_form') == 'analytic')
    if spec == 'ret':
        return _ret_calls(recs, s, ps)
    return 0


def _ret_calls(recs, strat, ps):
    """s03 的调用数按 RET 策略算，公式对齐 stages/s03_perspective.py。"""
    w1 = 1
    for p in ps:
        if p['script'].startswith('s03'):
            w1 = int(p['env'].get('RP_W1', 1) or 1)
    total = 0
    for r in recs:
        form = r.get('rubric_form')
        if form == 'gated_answer':
            continue                    # 固定 3 视角，不调模型
        if form == 'analytic':
            u = len(r.get('scenarios') or [])
        else:                           # multi_part：只有 open block 走 RET
            u = len([b for b in (r.get('blocks') or []) if b.get('block_type') == 'open'])
        if not u:
            continue
        # R_h 批量 1 + R_w(ℓ=1) w1 次 + 新场景批量 1 + 每场景 R_w(ℓ=2) 各 1
        total += 1 if strat == 'batch' else 1 + w1 + 1 + (u + 1)
    return total


# ---------- cache 扫描：进度、速率、模型归属 ----------

class Scan:
    """增量扫描 cache/<stage>：只 stat 新出现的文件。

    缓存目录会长到上万个文件，每轮全量 stat 会让面板自己变成负载。
    比 watch.py 的版本多一件事：把 model 字段按 stage 分别累计，
    这样「s03 用的哪个模型」是从落盘结果读出来的，不靠环境变量推。
    """

    def __init__(self):
        self.first = True       # 首帧多读一些，让 --once 快照的模型归属就是全的
        self.seen = {}          # stage → {文件名: mtime}
        self.pend = deque()     # 待读 usage 的新文件
        self.tok_stage = {}     # stage → 累计 token
        self.tok_model = {}     # 模型名 → 累计 token
        self.stage_model = {}   # stage → {模型名: 次数}
        self.unattr = 0         # 早于埋点、没有 model 字段的文件数
        self.rate_hist = {}     # stage → 最近几轮速率，用于趋势箭头

    def scan(self, stage):
        d = os.path.join(CACHE, stage)
        known = self.seen.setdefault(stage, {})
        try:
            with os.scandir(d) as it:
                for e in it:
                    if not e.name.endswith('.json') or e.name in known:
                        continue
                    try:
                        known[e.name] = e.stat().st_mtime
                    except OSError:
                        continue
                    self.pend.append((stage, e.path))
        except FileNotFoundError:
            return {'n': 0, 'rate': 0.0, 'last': 0.0}
        now = time.time()
        recent = sum(1 for t in known.values() if now - t <= WINDOW)
        r = {'n': len(known), 'rate': recent / (WINDOW / 60.0),
             'last': max(known.values()) if known else 0.0}
        h = self.rate_hist.setdefault(stage, deque(maxlen=6))
        if not h or now - h[-1][0] >= 5:        # 每 5s 记一个点，免得刷新率影响趋势
            h.append((now, r['rate']))
        return r

    def trend(self, stage):
        """速率趋势箭头。点太少或全零时返回平，不硬给方向。"""
        rates = [r for _, r in self.rate_hist.get(stage, []) if r > 0]
        if len(rates) < 4:
            return '→'
        mid = len(rates) // 2
        a = sum(rates[:mid]) / mid
        b = sum(rates[mid:]) / (len(rates) - mid)
        if b > a * 1.15:
            return f'{C["g"]}↑{C["r"]}'
        if b < a * 0.85:
            return f'{C["y"]}↓{C["r"]}'
        return '→'

    def drain(self, budget=1500):
        """读新缓存文件的 usage 与 model。缓存写完不再变，每个只读一次。

        限量摊到多帧：Phase 2 上万文件全量读一次要几秒，首帧会卡住。
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
                d = self.stage_model.setdefault(stage, {})
                d[m] = d.get(m, 0) + 1
            else:
                self.unattr += 1        # 早于埋点的调用，归不到具体模型
        return len(self.pend)

    def totals(self):
        t = {'n': 0, 'p': 0, 'c': 0, 'r': 0}
        for a in self.tok_stage.values():
            for k in t:
                t[k] += a[k]
        return t

    def who(self, stage):
        """这一步的模型归属，按次数降序：[(模型名, 次数), ...]。"""
        return sorted(self.stage_model.get(stage, {}).items(),
                      key=lambda kv: -kv[1])


# ---------- 端点探测：按主机分组，读数一律标注「整机」 ----------

class HostProbe(threading.Thread):
    """后台探端点，UI 永不阻塞。

    按 host:port 分组而不是按模型名：models.json 里 glm-ac / compassjudger /
    qwen-utility 都在 opencompass 那台网关后面，按模型名列会把同一台机器的读数
    抄三遍，看着像三个端点各自在忙。

    /metrics 是整机口径（含其他租户），拿不到就记 exposed=False 让上层写「不暴露」，
    绝不回填 0 —— 旧面板正是把 405 当成 0 印出来，才让闲着的端点看起来比在用的还忙。
    """
    daemon = True

    # 关心的服务端指标 → 内部字段。sglang 与 vLLM 命名不同，各列一份别名。
    _WANT = {
        'run':  ('num_running_reqs', 'num_requests_running'),
        'que':  ('num_queue_reqs', 'num_waiting_reqs', 'num_requests_waiting'),
        'ptok': ('prompt_tokens_total',),
        'gtok': ('generation_tokens_total',),
        'tps':  ('gen_throughput', 'avg_generation_throughput_toks_per_s'),
    }
    _SUM = {'run', 'que', 'ptok', 'gtok'}   # 计数类可跨分片相加，比率类不能

    def __init__(self, models):
        super().__init__()
        self.hosts = {}         # host:port → [模型名]
        self.url = {}           # host:port → 探测用的服务根 URL
        for m in models.values():
            for ep in (getattr(m, 'members', None) or [m]):
                hp = ep.base_url.split('://', 1)[-1].split('/', 1)[0]
                self.hosts.setdefault(hp, [])
                if m.name not in self.hosts[hp]:
                    self.hosts[hp].append(m.name)
                self.url[hp] = ep.base_url
        self.state = {}         # host:port → 探测结果
        self.reps = {}          # host:port → 见过的累计 ptok 值，用于估副本数
        self.stop = threading.Event()

    def _get(self, url, timeout=6):
        req = urllib.request.Request(url, headers={'Accept': '*/*'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode(errors='replace')

    def _metrics(self, base):
        root = base.rsplit('/v1', 1)[0]
        out = {}
        for ln in self._get(root + '/metrics', timeout=6).splitlines():
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
            for hp, base in self.url.items():
                st = {'models': self.hosts[hp]}
                t0 = time.time()
                try:
                    ids = {d.get('id') for d in
                           (json.loads(self._get(base + '/models', timeout=8)).get('data') or [])}
                    st.update(up=True, lat=time.time() - t0, ids=ids)
                except Exception as e:
                    st.update(up=False, lat=time.time() - t0, err=repr(e)[:60])
                if st['up']:
                    st.update(self._probe_metrics(hp, base))
                self.state[hp] = st
            self.stop.wait(PROBE_EVERY)

    def _probe_metrics(self, hp, base):
        """探 /metrics。同一 URL 后面可能挂多个副本，负载均衡每次随机命中一个，
        累计计数器因此在几个值之间跳。这里只做一件事：数出见过几个不同量级的
        计数器，报「≥N 副本」。不试图合成一个总数——那个数没有可解释的口径。"""
        try:
            s = self._metrics(base)
        except Exception:
            return {'exposed': False}
        if 'run' not in s and 'ptok' not in s:
            return {'exposed': False}      # 200 但没有我们认识的指标
        seen = self.reps.setdefault(hp, [])
        p = s.get('ptok')
        if p is not None:
            # 计数器只增不减：新采样若与某个已知副本量级相近就算同一个
            if not any(abs(p - q) / max(q, 1) < 0.5 for q in seen):
                seen.append(p)
            else:
                for i, q in enumerate(seen):
                    if abs(p - q) / max(q, 1) < 0.5:
                        seen[i] = max(q, p)
                        break
        return {'exposed': True, 'reps': len(seen), **s}


# ---------- 数据口径 ----------

def scope(ps):
    """在跑几条？返回 (声明的种子, 实际条数, 来源, 警告)。

    这一行是有来由的：run_phase1.py 改了但进程半小时前就起了，跑的是旧版，
    于是 Phase 1 拿全量 453 条在跑（本该 20 条）。旧面板没有任何地方能看出这点。

    「声明」取自 RP_SEED，「实际」取自 s01 产出的行数——s01 每条输入一次调用，
    行数就是它真正吃进去的条数。两者不一致时报警：环境变量可以骗人，产出不会。
    """
    seed, src = None, ''
    for p in ps:
        if p['env'].get('RP_SEED'):
            seed, src = p['env']['RP_SEED'], f"pid {p['pid']}"
            break
    if not seed:
        seed, src = os.environ.get('RP_SEED'), 'env'
    if not seed:
        seed, src = ('seed_phase1.jsonl' if os.path.exists(
            os.path.join(DATA, 'seed_phase1.jsonl')) else 'seed.jsonl'), '推定'

    actual, _ = lines('s01_filter.jsonl')       # s01 实际吃进去多少条
    declared, _ = lines(seed)
    warn, real = '', seed
    if actual and declared and actual != declared:
        # 认一下实际那个数对上了哪个候选，报出来比只说「不一致」有用
        hit = next((c for c in ('seed.jsonl', 'seed_phase1.jsonl')
                    if lines(c)[0] == actual), None)
        warn = (f's01 实际跑了 {actual} 条'
                f'{f"（= {hit}）" if hit else ""}，与声明的 {seed} {declared} 条不一致'
                f' —— 进程可能起在改动之前')
        if hit:
            real = hit      # 进度分母用实际那个，否则 s01 会显示 453/20
    return seed, declared, actual, src, warn, real


def header(ps, ev):
    out = [f'{C["b"]}Rubrics 流水线{C["r"]}  '
           f'{C["d"]}{time.strftime("%H:%M:%S")}{C["r"]}']
    seed, declared, actual, src, warn, _ = scope(ps)
    if ps:
        who = ', '.join(sorted({p['script'].replace('.py', '') for p in ps}))
        el = max(p['elapsed'] for p in ps)
        out.append(f'  {C["g"]}▶{C["r"]} {who}  {C["d"]}已跑 {dur(el)}{C["r"]}')
    else:
        out.append(f'  {C["d"]}○ 无 stage 进程在跑{C["r"]}')
    line = (f'  数据口径 声明 {C["c"]}{seed}{C["r"]} {declared} 条 '
            f'{C["d"]}({src}){C["r"]}')
    if actual:
        col = C['y'] if actual != declared else C['c']
        line += f'   实际 {col}{actual}{C["r"]} 条 {C["d"]}(s01 产出行数){C["r"]}'
    out.append(line)
    if warn:
        out.append(f'  {C["y"]}⚠ {warn}{C["r"]}')
    if ev.exists and ev.open:
        out.append(f'  在飞请求 {C["c"]}{len(ev.open)}{C["r"]} 个')
    return out


# ---------- 进度表 ----------

def freshness(cdir, ps):
    """产出相对输入是否新鲜。返回 'done' / 'stale' / 'missing' / 'none'。

    只看「产出文件存在」会把上一轮的残留当成本轮已完成——实测就踩了：
    s04/s07/s08/s11 的产出在本轮开跑前被删掉，cache 目录还在，面板照样报「已完成」；
    s03b 的产出是 20 条时代的，输入已经变成 452 条，也得算过期。
    """
    meta = PIPE_BY.get(cdir)
    if not meta:
        return 'none'
    _, _, inp, outp, _ = meta
    s = strategy(ps)
    op = os.path.join(DATA, outp.replace('{S}', s)) if outp else None
    if not op or not os.path.exists(op):
        return 'missing'
    if inp:
        ip = os.path.join(DATA, inp.replace('{S}', s))
        try:
            if os.path.getmtime(op) < os.path.getmtime(ip):
                return 'stale'      # 输入比产出新，这份产出是旧输入算出来的
        except OSError:
            pass
    return 'done'


def progress(ps, scan, ev, seed, scanned):
    now = time.time()
    out = [f'{C["b"]}进度{C["r"]}  {C["d"]}步骤        完成/预期        速率      ETA    '
           f'在飞  模型{C["r"]}']
    live_scripts = {p['script'] for p in ps}
    active, done, stale, pending = [], [], [], []

    for cdir in all_stages(ps):
        s = scanned[cdir]
        exp = expected(cdir, seed, ps)
        meta = PIPE_BY.get(cdir)
        fresh = freshness(cdir, ps)
        age = now - s['last'] if s['last'] else 1e9

        if meta and meta[1] in live_scripts:
            # s03 三策略共用一个脚本，靠 RP_RET 区分是哪一个在跑
            if cdir.startswith('s03_'):
                live = any(p['env'].get('RP_RET', 'hybrid') == cdir[4:]
                           for p in ps if p['script'] == meta[1])
            else:
                live = True
        else:
            live = False

        row = (cdir, s, exp, live, age, fresh)
        if live or age < STALL:
            active.append(row)
        elif fresh == 'done':
            done.append(row)
        elif s['n']:
            stale.append(row)       # 有缓存但产出缺失/过期：上一轮的残留
        else:
            pending.append(row)

    fly = {}
    for e in ev.open.values():
        fly[e.get('stage', '?')] = fly.get(e.get('stage', '?'), 0) + 1

    for cdir, s, exp, live, age, _ in active:
        out.append(_row(cdir, s, exp, live, age, scan, fly))
    # 过期的单独列出来，不与已完成混在一起：这些步骤本轮还得重跑
    for cdir, s, exp, _, _, fresh in stale:
        why = '产出不存在' if fresh == 'missing' else '产出早于输入'
        out.append(f'  {C["y"]}⚠{C["r"]} {pad(cdir, 11)} {C["y"]}{pad(why, 12)}{C["r"]}'
                   f'{C["d"]}缓存 {s["n"]} 次可复用，本轮仍需重跑{C["r"]}')
    if VIEW['all']:
        for cdir, s, exp, live, age, _ in done:
            out.append(_row(cdir, s, exp, live, age, scan, fly, dim=True))
        for cdir, s, exp, *_ in pending:
            out.append(f'  {C["d"]}○ {pad(cdir, 11)} 未开始'
                       f'{f"  预期 {exp}" if exp else ""}{C["r"]}')
    else:
        if done:
            out.append(f'  {C["d"]}✓ 已完成 {len(done)} 步：'
                       f'{", ".join(c for c, *_ in done)}{C["r"]}')
        if pending:
            out.append(f'  {C["d"]}○ 未开始 {len(pending)} 步  (--all 展开){C["r"]}')
    if not active and not done and not stale and not pending:
        out.append(f'  {C["d"]}没有步骤数据{C["r"]}')
    return out


def _row(cdir, s, exp, live, age, scan, fly, dim=False):
    """一行步骤。预期估不出来时进度条留空，不用假分母凑百分比。"""
    if exp:
        frac = min(1.0, s['n'] / exp)
        col = C['g'] if frac >= 0.999 else C['c']
        bar = f'{col}{"█" * int(frac * 12)}{C["d"]}{"░" * (12 - int(frac * 12))}{C["r"]}'
        # 预期是按公式估的，真实调用数可能超出（重试、JSON 重出都会多打）。
        # 超了就标 >，别把分母当成真值，也别把进度条截成看不出来。
        cnt = f'{s["n"]}/{exp}' + ('+' if s['n'] > exp else '')
        pct = f'{col}{frac * 100:3.0f}%{C["r"]}'
    else:
        bar = f'{C["d"]}{"─" * 12}{C["r"]}'
        cnt = str(s['n'])
        pct = f'{C["d"]}   ?{C["r"]}'

    rate = f'{s["rate"]:.1f}/分' if s['rate'] else '-'
    eta = dur((exp - s['n']) / s['rate'] * 60) if (s['rate'] and exp > s['n']) else '-'
    n_fly = fly.get(cdir, 0)
    who = scan.who(cdir)
    if not who:
        mstr = f'{C["d"]}-{C["r"]}'
    elif len(who) == 1:
        mstr = who[0][0]
    else:                   # 多个模型跑过同一步：并列显示，别只报第一个
        tot = sum(n for _, n in who)
        mstr = ' + '.join(f'{m} {C["d"]}{n / tot * 100:.0f}%{C["r"]}' for m, n in who)

    mark = f'{C["g"]}▶{C["r"]}' if live else (
        f'{C["y"]}⏸{C["r"]}' if age < STALL else f'{C["d"]}✓{C["r"]}')
    body = (f'{mark} {pad(cdir, 11)} {bar} {pct} {rpad(cnt, 12)} '
            f'{pad(rate, 9)}{scan.trend(cdir)} {pad(eta, 7)}'
            f'{rpad(str(n_fly) if n_fly else "-", 4)}  {mstr}')
    return f'{C["d"]}{body}{C["r"]}' if dim else body


# ---------- 本流水线的调用（权威口径：cache 里的 model 字段） ----------

def ours(scan, ev, models):
    out = [f'{C["b"]}本流水线的调用{C["r"]}  {C["d"]}来自 cache/*/[hash].json 的 model 字段{C["r"]}']
    if not scan.tok_model and not scan.unattr:
        out.append(f'  {C["d"]}还没有非缓存调用{C["r"]}')
        return out

    agg = ev.by_model()
    total = sum(a['n'] for a in scan.tok_model.values())
    for m, a in sorted(scan.tok_model.items(), key=lambda kv: -kv[1]['n']):
        share = a['n'] / max(total, 1) * 100
        think = f'  {C["m"]}思维链 {a["r"] / a["c"] * 100:.0f}%{C["r"]}' if a.get('r') and a['c'] else ''
        e = agg.get(m) or {}
        fly = f'  在飞 {C["c"]}{e["fly"]}{C["r"]}' if e.get('fly') else ''
        out.append(f'  {C["c"]}{pad(m, 14)}{C["r"]} {rpad(num(a["n"]), 6)} 次 '
                   f'{C["d"]}({share:4.1f}%){C["r"]}  '
                   f'入 {rpad(num(a["p"]), 6)}  出 {rpad(num(a["c"]), 6)}{think}{fly}')

    # 配了但一次没用过的模型：显式列出来，免得「为什么没用 X」只能靠猜
    idle = [n for n in models if n not in scan.tok_model]
    if idle:
        out.append(f'  {C["d"]}未被调用: {", ".join(idle)}{C["r"]}')
    if scan.unattr:
        out.append(f'  {C["d"]}{scan.unattr} 次归属未知（早于埋点的缓存）{C["r"]}')

    # 窗口内成功率：区分真失败和缓存命中，两者混在一起会看不出端点在抖
    end = sum(a['end'] for a in agg.values())
    err = sum(a['err'] for a in agg.values())
    hit = sum(a['hit'] for a in agg.values())
    if end + err:
        sr = end / (end + err) * 100
        col = C['g'] if sr >= 95 else (C['y'] if sr >= 90 else C['e'])
        avg = sum(a['dt'] for a in agg.values()) / end if end else 0
        out.append(f'  {C["d"]}最近 {WINDOW}s{C["r"]} {col}成功率 {sr:.0f}%{C["r"]}  '
                   f'成功 {end}  失败 {err}  缓存命中 {hit}  '
                   f'{C["d"]}平均 {avg:.1f}s{C["r"]}')
    return out


# ---------- 端点状态（整机口径，含他人） ----------

def endpoints(probe, scan):
    if not probe:
        return []
    out = [f'{C["b"]}端点{C["r"]}  {C["y"]}整机口径，含其他租户的流量{C["r"]}'
           f'{C["d"]} — 不是本流水线的负载{C["r"]}']
    for hp, st in sorted(probe.state.items()):
        host = hp.split('.', 1)[0]      # 长域名只留首段，够区分了
        # 标出这台机器上哪些模型我们真的调过——「为什么在用 X 不用 Y」看这一列
        used = [n for n in st['models'] if n in scan.tok_model]
        names = ', '.join(f'{C["c"]}{n}{C["r"]}' if n in used else f'{C["d"]}{n}{C["r"]}'
                          for n in st['models'])
        if not st.get('up'):
            out.append(f'  {C["e"]}✗{C["r"]} {pad(host, 22)} {C["e"]}离线{C["r"]} '
                       f'{C["d"]}{st.get("err", "")}{C["r"]}')
            out.append(f'      {names}')
            continue
        lat = f'{st["lat"] * 1000:.0f}ms'
        if not st.get('exposed'):
            load = f'{C["d"]}/metrics 不暴露，看不到整机负载{C["r"]}'
        else:
            run, que = int(st.get('run', 0)), int(st.get('que', 0))
            col = C['y'] if que else (C['g'] if run else C['d'])
            reps = f'  {C["d"]}≥{st["reps"]} 副本{C["r"]}' if st.get('reps', 1) > 1 else ''
            tps = f'  {C["d"]}{int(st["tps"])}tok/s{C["r"]}' if st.get('tps') else ''
            load = f'{col}整机 运行{run} 排队{que}{C["r"]}{tps}{reps}'
        note = '' if used else f'   {C["d"]}本流水线未调用{C["r"]}'
        out.append(f'  {C["g"]}✓{C["r"]} {pad(host, 22)} {rpad(lat, 7)}  {load}')
        out.append(f'      {names}{note}')
    return out


# ---------- 在飞请求 ----------

def inflight(ev, width, k=6):
    fl = ev.inflight()
    if not fl:
        return []
    now = time.time()
    out = [f'{C["b"]}在飞{C["r"]}  {C["d"]}{len(fl)} 个请求等在路上{C["r"]}']
    for e in fl[:k]:
        wait = now - e['t']
        col = C['e'] if wait > 120 else (C['y'] if wait > 40 else C['d'])
        flags = []
        if e.get('nothink'):
            flags.append('关思维链')
        if e.get('att'):
            flags.append(f'第{e["att"] + 1}次')
        fs = f' {C["y"]}[{",".join(flags)}]{C["r"]}' if flags else ''
        ep = e.get('endpoint') or e.get('model', '?')
        head = (f'  {col}{rpad(dur(wait), 6)}{C["r"]} {pad(e.get("stage", "?"), 11)} '
                f'{pad(ep, 14)}{fs}')
        out.append(head)
        p = e.get('prompt') or ''
        if p:
            out.append(f'        {C["d"]}{pad(p, max(20, width - 10))}{C["r"]}')
    if len(fl) > k:
        out.append(f'  {C["d"]}… 另外 {len(fl) - k} 个{C["r"]}')
    return out


def render(scan, ev, probe, models, ip2names, width):
    ps = procs(ip2names)
    ev.poll()
    seed = scope(ps)[5]         # 用实际口径算分母，不用声明的
    # 先把所有步骤扫一遍再 drain，最后才渲染：progress 里的「模型」列要读 drain
    # 的结果，顺序颠倒的话首帧那一列全是 '-'（--once 快照尤其明显）。
    scanned = {c: scan.scan(c) for c in all_stages(ps)}
    scan.drain(budget=20000 if scan.first else 1500)
    scan.first = False
    out = header(ps, ev)
    out.append('')
    out += progress(ps, scan, ev, seed, scanned)
    out.append('')
    out += ours(scan, ev, models)
    ep = endpoints(probe, scan)
    if ep:
        out.append('')
        out += ep
    fl = inflight(ev, width)
    if fl:
        out.append('')
        out += fl
    return out


def main():
    a = sys.argv[1:]
    once = '--once' in a
    VIEW['all'] = '--all' in a
    interval = float(a[a.index('-n') + 1]) if '-n' in a else 2.0

    models = load_models()
    ip2names = resolve_hosts(models)
    scan, ev, probe = Scan(), Events(), None
    if '--no-probe' not in a and models:
        probe = HostProbe(models)
        probe.start()
        if once:
            time.sleep(3)       # 给探测线程一轮的时间，否则快照里端点全是空的

    alt = False
    try:
        if once:
            w_ = shutil.get_terminal_size((130, 40)).columns
            print('\n'.join(render(scan, ev, probe, models, ip2names, w_)))
            return 0
        if sys.stdout.isatty():
            sys.stdout.write('\x1b[?1049h\x1b[?25l')    # 备用屏 + 藏光标
            alt = True
        while True:
            cols, rows = shutil.get_terminal_size((130, 40))
            out = render(scan, ev, probe, models, ip2names, cols)
            if len(out) > rows - 2:
                out = out[:rows - 3] + [f'{C["d"]}… 窗口太小，拉高或用 --once{C["r"]}']
            sys.stdout.write('\x1b[H' + '\n'.join(l + '\x1b[K' for l in out) + '\x1b[J')
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
