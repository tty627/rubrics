#!/usr/bin/env python3
"""定期生成监控快照的 HTML 版本，供 Web 服务器访问"""

import json
import os
import time
from pathlib import Path
from datetime import datetime

def collect_stats():
    """收集监控数据"""
    cache = Path("cache")
    stats = {"stages": {}, "total_calls": 0, "timestamp": datetime.now().isoformat()}

    for stage_dir in sorted(cache.glob("s*")):
        if not stage_dir.is_dir():
            continue
        stage = stage_dir.name
        files = list(stage_dir.glob("*.json"))
        cached = done = 0
        for f in files:
            try:
                d = json.loads(f.read_text())
                if d.get("cached"):
                    cached += 1
                done += 1
            except:
                pass
        stats["stages"][stage] = {"done": done, "cached": cached}
        stats["total_calls"] += done

    # 读取最近的事件
    events_file = cache / "_events.jsonl"
    recent = []
    if events_file.exists():
        lines = events_file.read_text().strip().split("\n")
        for line in lines[-10:]:  # 最近 10 条
            try:
                recent.append(json.loads(line))
            except:
                pass
    stats["recent_events"] = recent
    return stats

def generate_html(stats):
    """生成简洁的 HTML"""
    timestamp = datetime.fromisoformat(stats["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")

    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rubric Pipeline Monitor</title>
<style>
body {{ font-family: monospace; margin: 20px; background: #1a1a1a; color: #e0e0e0; }}
.header {{ font-size: 18px; margin-bottom: 20px; border-bottom: 2px solid #444; padding-bottom: 10px; }}
.stage {{ margin: 10px 0; padding: 8px; background: #252525; border-radius: 4px; }}
.stage-name {{ color: #5af78e; font-weight: bold; }}
.progress {{ color: #9945ff; }}
.cached {{ color: #888; }}
.event {{ margin: 5px 0; padding: 5px; background: #2a2a2a; font-size: 12px; }}
.timestamp {{ color: #666; }}
</style>
</head><body>
<div class="header">
📊 Rubric Pipeline Monitor<br>
<span class="timestamp">更新时间: {timestamp}</span><br>
总调用: <span class="progress">{stats['total_calls']}</span>
</div>"""

    # 各步骤进度
    for stage, data in stats["stages"].items():
        done = data["done"]
        cached = data["cached"]
        fresh = done - cached
        html += f"""
<div class="stage">
<span class="stage-name">{stage}</span>:
<span class="progress">{done} 完成</span>
(<span class="cached">{cached} 缓存, {fresh} 新增</span>)
</div>"""

    # 最近事件
    if stats["recent_events"]:
        html += "<div class='header' style='margin-top: 20px'>最近事件</div>"
        for ev in reversed(stats["recent_events"]):
            stage = ev.get("stage", "?")
            duration = ev.get("duration_s", 0)
            cached = "💾" if ev.get("cached") else "🔥"
            html += f"<div class='event'>{cached} {stage} | {duration:.1f}s</div>"

    html += "</body></html>"
    return html

if __name__ == "__main__":
    stats = collect_stats()
    html = generate_html(stats)
    Path("monitor.html").write_text(html)
    print(f"✓ 已生成 monitor.html ({len(html)} bytes)")
