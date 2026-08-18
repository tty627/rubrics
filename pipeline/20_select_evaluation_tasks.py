"""Select tasks with at least two candidate responses for measured evaluation."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    selected = []
    with open(args.src, encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            refs = record.get("ref_responses") or {}
            count = sum(1 for value in refs.values() if isinstance(value, str) and value.strip())
            if count >= 2:
                selected.append(record)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as stream:
        for record in selected:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"selected {len(selected)} tasks -> {target}")


if __name__ == "__main__":
    main()
