"""阶段 26：把实测终态并回冻结版，产出唯一交付源。

每题都应当有实测证据 —— 回复池六档全部现场生成，不存在「无法参与实测」的题
（旧线按 ref_responses 条数把数据集切成 388 + 64，那个切分已删除）。
所以缺实测只能是上游失败，这里如实标 `measured=False` 并在末尾报数，
让阶段 04 的结构闸门去拦，而不是给它编一个「未参与」的正常理由。
"""
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
    merged, unmeasured = [], []
    for rid, base in fallback.items():
        if rid in measured:
            record = dict(measured[rid])
            # judged/pool/consequential 是实测中间态，体积大且不进交付；
            # 终态选择结论已经留在 _s11Le 里。
            for key in ("judged", "pool", "consequential"):
                record.pop(key, None)
            record["measured"] = True
        else:
            unmeasured.append(rid)
            record = {**base, "measured": False,
                      "measured_missing_reason": "实测链未产出该题，见 _stage_errors"}
        merged.append(record)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as stream:
        for record in merged:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"merged {len(merged)} tasks -> {target}")
    if unmeasured:
        print(f"  ⚠️  {len(unmeasured)} 题缺实测证据（上游失败）：{unmeasured[:8]}")


if __name__ == "__main__":
    main()
