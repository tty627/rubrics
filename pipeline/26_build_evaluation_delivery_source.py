"""Merge measured and non-measured rubric records into one delivery source."""
import argparse
import json
from pathlib import Path


def read(path):
    with open(path, encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--measured", required=True)
    parser.add_argument("--fallback", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    measured = {record["rid"]: record for record in read(args.measured)}
    fallback = {record["rid"]: record for record in read(args.fallback)}
    merged = []
    for rid, base in fallback.items():
        if rid in measured:
            record = dict(measured[rid])
            for key in ("judged", "pool", "consequential"):
                record.pop(key, None)
        else:
            record = {**base, "_s11Le": {
                "chosen_round": "未参与 Phase 4（单回复题）",
                "residual": [], "skipped": False, "rounds_seen": 0,
            }}
        merged.append(record)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as stream:
        for record in merged:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"merged {len(merged)} tasks -> {target}")


if __name__ == "__main__":
    main()
