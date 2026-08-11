"""流水线监视面板 v2：更直观的三层结构。

核心改进：
  1. 【状态栏】- 一行说清：端点是否正常、当前在跑什么、预计何时完成
  2. 【进度表】- 只显示活跃步骤（未开始/已完成可折叠）+ 速率趋势
  3. 【成本栏】- Token 用量 + 预估总成本

用法：
    python3 tools/watch_v2.py          # 全屏刷新
    python3 tools/watch_v2.py --once   # 快照
    python3 tools/watch_v2.py --all    # 展开所有步骤
"""
import json, os, sys, time, shutil
from collections import deque

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 复用原面板的基础组件
from tools.watch import (
    CACHE, DATA, EVENTS, WINDOW, PROBE_EVERY, STALL, PIPE, PIPE_BY,
    C, w, cut, pad, rpad, dur, num, all_stages, guess_output, lines,
    seed_name, expected, load_models, resolve_hosts, default_generator,
    procs, Scan, Events, Probe
)

VIEW = {'all': False}

# 模型价格表（美元/M tokens）: (输入价, 输出价)
PRICING = {
    'deepseek': (0.27, 1.10),       # DeepSeek-V3
    'qwen': (0.30, 0.60),           # Qwen系列平均
    'glm': (0.50, 0.50),            # GLM-4
    'gpt': (2.50, 10.0),            # GPT-4 级别
}

def guess_price(model_name):
    """根据模型名猜测价格"""
    ml = model_name.lower()
    for key, price in PRICING.items():
        if key in ml:
            return price
    return (1.0, 2.0)  # 默认价格


class ScanV2(Scan):
    """扩展Scan：记录历史速率用于趋势判断"""
    def __init__(self):
        super().__init__()
        self.rate_history = {}  # stage → deque([时间戳, 速率])

    def scan(self, stage):
        result = super().scan(stage)
        # 记录速率历史（最近5个点）
        hist = self.rate_history.setdefault(stage, deque(maxlen=5))
        hist.append((time.time(), result['rate']))
        return result

    def trend(self, stage):
        """返回速率趋势：'up'/'down'/'flat'"""
        hist = self.rate_history.get(stage, [])
        if len(hist) < 3:
            return 'flat'
        rates = [r for t, r in hist if r > 0]
        if len(rates) < 2:
            return 'flat'
        # 简单线性趋势：比较前半段和后半段平均值
        mid = len(rates) // 2
        avg1 = sum(rates[:mid]) / mid
        avg2 = sum(rates[mid:]) / (len(rates) - mid)
        if avg2 > avg1 * 1.15:
            return 'up'
        elif avg2 < avg1 * 0.85:
            return 'down'
        return 'flat'


# ========== 状态栏 ==========

def status_line(ps, models, probe):
    """一行总结：端点健康 | 当前任务 | 关键指标"""
    parts = []

    # 端点健康度
    if probe:
        down = [m for m, st in probe.state.items() if st and not st.get('up')]
        if down:
            parts.append(f'{C["e"]}✗ 端点异常: {",".join(down[:2])}{C["r"]}')
        else:
            up_count = sum(1 for st in probe.state.values() if st and st.get('up'))
            if up_count:
                parts.append(f'{C["g"]}✓ {up_count}个端点正常{C["r"]}')

    # 当前任务
    if ps:
        scripts = list({p['script'] for p in ps})
        if len(scripts) == 1:
            parts.append(f'{C["c"]}{scripts[0]}{C["r"]} 运行中')
        else:
            parts.append(f'{C["c"]}{len(scripts)}个任务{C["r"]} 并行')
    else:
        parts.append(f'{C["d"]}空闲{C["r"]}')

    return '  |  '.join(parts) if parts else f'{C["d"]}等待启动{C["r"]}'


# ========== 进度表 ==========

def progress_table(ps, scan, seed):
    """进度表：只显示活跃步骤，折叠其他"""
    now = time.time()
    lines_out = []
    lines_out.append(f'{C["b"]}进度{C["r"]}')

    active, pending, done = [], [], []

    for cdir in all_stages():
        s = scan.scan(cdir)
        age = now - s['last'] if s['last'] else 0
        meta = PIPE_BY.get(cdir)

        # 判断状态
        if meta:
            _, script, var, inp, outp, spec = meta
            exp = expected(spec, inp or seed, ps)
            out_p = os.path.join(DATA, outp) if outp else None
            if cdir.startswith('s03_'):
                live = any(p['script'] == script and
                          p['env'].get('RP_RET', 'hybrid') == cdir[4:] for p in ps)
            else:
                live = script in {p['script'] for p in ps}
        else:
            exp, out_p = 0, guess_output(cdir)
            live = bool(ps) and 0 < age < STALL

        done_file = bool(out_p) and os.path.exists(out_p)

        # 分类
        if s['n'] == 0 and not done_file:
            pending.append((cdir, s, exp, 'pending'))
        elif live or (0 < age < STALL):
            active.append((cdir, s, exp, 'active' if live else 'stalled'))
        elif done_file:
            done.append((cdir, s, exp, 'done'))

    # 显示活跃步骤
    if not active and not pending and not done:
        lines_out.append(f'  {C["d"]}没有步骤数据{C["r"]}')
    else:
        for cdir, s, exp, status in active:
            trend = scan.trend(cdir)
            trend_mark = {'up': f'{C["g"]}↑{C["r"]}', 'down': f'{C["e"]}↓{C["r"]}', 'flat': '→'}[trend]

            # 进度条（14字符）
            if exp:
                frac = min(1.0, s['n'] / exp)
                bar_len = int(frac * 14)
                col = C['g'] if frac >= 0.999 else (C['c'] if frac > 0 else C['d'])
                bar = f'{col}{"█" * bar_len}{C["d"]}{"░" * (14 - bar_len)}{C["r"]}'
                pct = f'{col}{frac * 100:3.0f}%{C["r"]}'
            else:
                bar = f'{C["d"]}{"─" * 14}{C["r"]}'
                pct = f'{C["d"]}  ? {C["r"]}'

            rate_str = f'{s["rate"]:.1f}次/分' if s['rate'] else '-'
            eta_str = dur(int((exp - s['n']) / s['rate'] * 60)) if (s['rate'] and exp and exp > s['n']) else '-'

            stat_mark = f'{C["g"]}▶{C["r"]}' if status == 'active' else f'{C["y"]}⏸{C["r"]}'
            lines_out.append(f'{stat_mark} {pad(cdir, 13)} {bar} {pct}  '
                           f'{pad(rate_str, 10)} {trend_mark}  {pad("ETA " + eta_str, 12)}')

        # 折叠信息
        if not VIEW['all']:
            if done:
                lines_out.append(f'  {C["d"]}✓ 已完成 {len(done)} 步{C["r"]}')
            if pending:
                lines_out.append(f'  {C["d"]}○ 未开始 {len(pending)} 步  (展开用 --all){C["r"]}')
        else:
            for cdir, s, exp, _ in done:
                lines_out.append(f'{C["d"]}✓ {pad(cdir, 13)} 已完成{C["r"]}')
            for cdir, s, exp, _ in pending:
                lines_out.append(f'{C["d"]}○ {pad(cdir, 13)} 未开始{C["r"]}')

    return lines_out


# ========== 资源统计 ==========

def resource_stats(scan, ev, probe, models):
    """合并的资源统计：Token + 成本 + 端点负载"""
    lines_out = []
    lines_out.append(f'{C["b"]}资源{C["r"]}')

    # Token汇总（流水线侧）
    scan.drain()
    tt = scan.totals()
    if tt['n'] > 0:
        think_pct = (tt['r'] / tt['c'] * 100) if tt['c'] else 0
        think_str = f'{C["m"]}思维链 {think_pct:.0f}%{C["r"]}' if tt['r'] else ''
        lines_out.append(f'  输入 {C["c"]}{num(tt["p"])}{C["r"]}  '
                        f'输出 {C["c"]}{num(tt["c"])}{C["r"]}  '
                        f'{think_str}')

        # 成本估算
        total_cost = 0
        for m, a in scan.tok_model.items():
            inp_p, out_p = guess_price(m)
            cost = (a['p'] / 1e6 * inp_p) + (a['c'] / 1e6 * out_p)
            total_cost += cost
        if total_cost > 0:
            lines_out.append(f'  估算成本: {C["y"]}${total_cost:.2f}{C["r"]}')
    else:
        lines_out.append(f'  {C["d"]}暂无Token消耗{C["r"]}')

    # 端点负载
    if probe:
        lines_out.append('')
        for m in models.values():
            st = probe.state.get(m.name)
            if not st:
                continue

            if not st.get('up'):
                lines_out.append(f'  {C["e"]}✗{C["r"]} {pad(m.name, 12)} {C["e"]}离线{C["r"]}')
            else:
                run = st.get('run', 0)
                que = st.get('que', 0)
                tps = st.get('tps', 0)

                load_col = C['y'] if que > 0 else (C['g'] if run > 0 else C['d'])
                load_str = f'{load_col}运行{int(run)} 排队{int(que)}{C["r"]}'
                tps_str = f'{int(tps)}tok/s' if tps else '-'

                lines_out.append(f'  {C["g"]}✓{C["r"]} {pad(m.name, 12)} {load_str}  {C["d"]}{tps_str}{C["r"]}')

    # 调用统计（事件流水）
    ev.poll()
    agg = ev.by_model()
    if agg:
        lines_out.append('')
        total_end = sum(a['end'] for a in agg.values())
        total_err = sum(a['err'] for a in agg.values())
        if total_end + total_err > 0:
            success_rate = total_end / (total_end + total_err) * 100
            avg_time = sum(a['dt'] for a in agg.values()) / total_end if total_end else 0

            sr_col = C['g'] if success_rate >= 95 else (C['y'] if success_rate >= 90 else C['e'])
            lines_out.append(f'  最近{WINDOW}s: '
                           f'{sr_col}成功率 {success_rate:.0f}%{C["r"]}  '
                           f'成功 {total_end}  失败 {total_err}  '
                           f'{C["d"]}平均 {avg_time:.1f}s{C["r"]}')

    return lines_out


# ========== 主渲染 ==========

def render(scan, ev, probe, models, ip2names):
    """三层结构输出"""
    ps = procs(ip2names)
    seed = seed_name(ps)

    lines_out = []

    # 标题 + 状态栏
    lines_out.append(f'{C["b"]}═══ Rubrics 流水线监视 ═══{C["r"]}  '
                    f'{C["d"]}{time.strftime("%Y-%m-%d %H:%M:%S")}{C["r"]}')
    lines_out.append(status_line(ps, models, probe))
    lines_out.append('')

    # 进度表
    lines_out.extend(progress_table(ps, scan, seed))
    lines_out.append('')

    # 资源统计
    lines_out.extend(resource_stats(scan, ev, probe, models))

    return lines_out


# ========== 主循环 ==========

def main():
    a = sys.argv[1:]
    once = '--once' in a
    VIEW['all'] = '--all' in a
    interval = 2.0
    if '-n' in a:
        interval = float(a[a.index('-n') + 1])

    models = load_models()
    ip2names = resolve_hosts(models)
    scan = ScanV2()
    ev = Events()
    probe = None

    if '--no-probe' not in a and models:
        probe = Probe(models)
        probe.start()
        if once:
            time.sleep(4)

    alt = False
    try:
        if once:
            print('\n'.join(render(scan, ev, probe, models, ip2names)))
            return 0

        # 备用屏 + 藏光标
        if sys.stdout.isatty():
            sys.stdout.write('\x1b[?1049h\x1b[?25l')
            alt = True

        while True:
            lines_out = render(scan, ev, probe, models, ip2names)
            rows = shutil.get_terminal_size((130, 40)).lines

            # 如果输出超过终端高度，裁剪到合适大小
            if len(lines_out) > rows - 2:
                lines_out = lines_out[:rows - 3] + [f'{C["d"]}... (窗口太小，拉高或用 --once 看全){C["r"]}']

            # 回原点重写
            sys.stdout.write('\x1b[H' + '\n'.join(l + '\x1b[K' for l in lines_out) + '\x1b[J')
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
