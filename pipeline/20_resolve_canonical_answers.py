"""Resolve program-checkable answers from task instructions, never candidate responses."""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import stage, task_input

THINK = stage.envflag("RP_THINK", True)
PROGRAM_KINDS = {"numeric", "option", "token", "exact_text"}
SYS = '''你是独立答案求解器。只根据原始 system/developer/user 指令求解可机械核验的答案。
不得读取、猜测或复述任何候选回答，也不得把候选回答当作真值。

仅当题目有唯一答案，且另一份正确回答必然原样包含一个最小字符串时，才提供可程序核验答案。
answer_kind 可选 numeric、option、token、exact_text、formula、none。
formula 或自然语言答案一律使用 none，answer_canonical 留空。无法确定时宁可留空。

只输出 JSON：
{"answer": "答案或空串", "answer_kind": "numeric|option|token|exact_text|formula|none",
 "answer_canonical": "最小可核验字符串或空串", "confidence": "high|medium|low",
 "evidence": "仅说明由哪些题面条件和推导得到，不引用候选回答"}'''


def build(record):
    return [{"role": "system", "content": SYS},
            {"role": "user", "content": task_input.prompt_context(record, max_chars=14000)}]


def resolve_record(record, model):
    clean = {k: v for k, v in record.items()
             if k not in ("answer", "answer_kind", "answer_canonical", "answer_sound",
                          "answer_source", "anchors", "gaps", "anchor_key", "anchor_shared")}
    if clean.get("question_type") not in ("verifiable", "hybrid"):
        return {**clean, "answer": "", "answer_kind": "none", "answer_canonical": "",
                "answer_confidence": "not_applicable", "answer_evidence": "",
                "answer_source": "independent_solver"}
    obj, meta = stage.json_call(model, build(clean), stage="s20_answer", thinking=THINK)
    kind = str(obj.get("answer_kind", "none"))
    if kind not in PROGRAM_KINDS | {"formula", "none"}:
        kind = "none"
    confidence = str(obj.get("confidence", "low"))
    if confidence not in ("high", "medium", "low"):
        confidence = "low"
    canonical = str(obj.get("answer_canonical", "") or "").strip()[:300]
    if kind not in PROGRAM_KINDS or confidence != "high":
        canonical = ""
    return {**clean, "answer": str(obj.get("answer", "") or "")[:500],
            "answer_kind": kind, "answer_canonical": canonical,
            "answer_sound": confidence == "high", "answer_confidence": confidence,
            "answer_evidence": str(obj.get("evidence", "") or "")[:500],
            "answer_source": "independent_solver", "_answer_meta": meta}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        model = stage.pick("RP_M_ANSWER", "solver")
    except ValueError:
        model = stage.pick("RP_M_ANSWER", "judge")
        print(f"  [配置回退] 未配置 solver，使用 judge={model.name}")
    records = stage.read_jsonl(args.src)
    out, errors = stage.run(lambda r: resolve_record(r, model), records,
                            workers=int(os.environ.get("RP_WORKERS", 8)), desc="s20_answer")
    for record in out:
        record.pop("_answer_meta", None)
    if errors:
        raise SystemExit(f"independent answer resolution failed for {len(errors)} records")
    stage.write_jsonl(args.out, out)


if __name__ == "__main__":
    main()
