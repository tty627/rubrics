#!/usr/bin/env python3
"""生成紧凑的纯文本监控快照，适合通过企微/飞书发送"""

import json
from pathlib import Path
from datetime import datetime

def main():
    cache = Path("cache")
    lines = [f"📊 Rubric Pipeline 状态 ({datetime.now().strftime('%H:%M:%S')})"]
    lines.append("=" * 50)

    total = 0
    for stage_dir in sorted(cache.glob("s*")):
        if not stage_dir.is_dir():
            continue
        files = list(stage_dir.glob("*.json"))
        done = len(files)
        if done == 0:
            continue

        cached = sum(1 for f in files
                    if json.loads(f.read_text()).get("cached"))
        fresh = done - cached
        total += done

        lines.append(f"{stage_dir.name}: {done} 完成 ({cached} 缓存 + {fresh} 新)")

    lines.append("=" * 50)
    lines.append(f"总计: {total} 次调用")

    # 最后一条事件
    events = cache / "_events.jsonl"
    if events.exists():
        last = events.read_text().strip().split("\n")[-1]
        ev = json.loads(last)
        t = datetime.fromisoformat(ev["timestamp"]).strftime("%H:%M:%S")
        lines.append(f"最新: {ev['stage']} @ {t}")

    print("\n".join(lines))

if __name__ == "__main__":
    main()
