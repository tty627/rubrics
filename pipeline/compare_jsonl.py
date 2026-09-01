"""Compare JSONL records semantically, independent of line order and serialization."""
import argparse
import json


def load(path):
    records = {}
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            record = json.loads(line)
            key = record.get("rid")
            if not isinstance(key, str) or not key:
                raise ValueError(f"{path}: record missing rid")
            if key in records:
                raise ValueError(f"{path}: duplicate rid {key}")
            records[key] = record
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("left")
    parser.add_argument("right")
    args = parser.parse_args()
    left, right = load(args.left), load(args.right)
    if left.keys() != right.keys():
        missing_left = sorted(right.keys() - left.keys())[:10]
        missing_right = sorted(left.keys() - right.keys())[:10]
        raise SystemExit(f"rid mismatch: missing_left={missing_left} missing_right={missing_right}")
    different = [rid for rid in left if left[rid] != right[rid]]
    if different:
        raise SystemExit(f"content mismatch for {len(different)} records: {different[:10]}")
    print(f"semantic match: {len(left)} records")


if __name__ == "__main__":
    main()
