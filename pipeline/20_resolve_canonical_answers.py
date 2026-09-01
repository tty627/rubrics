"""阶段 20：从题面独立求解可核验答案，绝不读候选回答。

rubric 只从题目生成，可核验题的正确性判据因此必须自己解出来，而不能从待评回答里抄。
单模型解题不可靠，所以采用「权威模型 + 异源交叉复核」的共识准入：

  1. grounder（强制绑定各厂商闭源最强模型）出权威答案；
  2. 另外两个异源模型独立解同一题；
  3. 三方 answer_canonical 归一后完全一致 → 准入，写进 rubric 判据；
  4. 有分歧 → 该题答案判据挂起，写 data/_answer_dispute.jsonl，不进判分。

准入门槛故意设成「全一致」而非「多数票」：多数票在两个模型犯同一个错时会把错答案
锁成真值，而这条链的下游（22 判分的程序化核验）无条件相信 answer_canonical。
宁可挂起也不给错判据。
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import answer_check, stage, task_input

THINK = stage.envflag("RP_THINK", True)
PROGRAM_KINDS = {"numeric", "option", "token", "exact_text"}
CROSS_CHECKS = int(os.environ.get("RP_ANSWER_CROSS_CHECKS", 2))
DISPUTE_PATH = os.environ.get("RP_ANSWER_DISPUTE", "data/_answer_dispute.jsonl")
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


def solve(record, model):
    """单模型解一题，返回规范化后的答案字段。"""
    obj, meta = stage.json_call(model, build(record), stage="s20_answer", thinking=THINK)
    kind = str(obj.get("answer_kind", "none"))
    if kind not in PROGRAM_KINDS | {"formula", "none"}:
        kind = "none"
    confidence = str(obj.get("confidence", "low"))
    if confidence not in ("high", "medium", "low"):
        confidence = "low"
    canonical = str(obj.get("answer_canonical", "") or "").strip()[:300]
    if kind not in PROGRAM_KINDS or confidence != "high":
        canonical = ""
    return {"model": model.name, "family": model.family,
            "answer": str(obj.get("answer", "") or "")[:500],
            "answer_kind": kind, "answer_canonical": canonical,
            "answer_confidence": confidence,
            "answer_evidence": str(obj.get("evidence", "") or "")[:500],
            "_meta": meta}


def resolve_record(record, ground, checkers):
    clean = {k: v for k, v in record.items()
             if k not in ("answer", "answer_kind", "answer_canonical", "answer_sound",
                          "answer_source", "anchors", "gaps", "anchor_key", "anchor_shared")}
    if clean.get("question_type") not in ("verifiable", "hybrid"):
        return {**clean, "answer": "", "answer_kind": "none", "answer_canonical": "",
                "answer_confidence": "not_applicable", "answer_evidence": "",
                "answer_admitted": False, "answer_source": "independent_solver"}

    # 权威答案来自 grounder；它决定 answer/kind/evidence，交叉复核只决定是否准入。
    authority = solve(clean, ground)
    votes = [authority] + [solve(clean, m) for m in checkers]
    canonical = authority["answer_canonical"]
    agree = [v for v in votes
             if answer_check.norm_txt(v["answer_canonical"]) == answer_check.norm_txt(canonical)]
    # 权威侧没给出可程序核验答案时无判据可准入，不算争议，按开放题处理。
    admitted = bool(canonical) and len(agree) == len(votes)
    result = {**clean, "answer": authority["answer"],
              "answer_kind": authority["answer_kind"],
              "answer_canonical": canonical if admitted else "",
              "answer_sound": admitted,
              "answer_confidence": authority["answer_confidence"],
              "answer_evidence": authority["answer_evidence"],
              "answer_admitted": admitted,
              "answer_source": "independent_solver",
              "answer_authority": ground.name,
              "_answer_votes": [{k: v for k, v in vote.items() if k != "_meta"}
                                for vote in votes],
              "_answer_meta": authority["_meta"]}
    if canonical and not admitted:
        result["answer_dispute"] = True
        result["answer_dispute_reason"] = (
            f"交叉复核不一致：{len(agree)}/{len(votes)} 与权威答案一致")
    return result


def pick_checkers(ground):
    """挑异源复核模型：family 必须不同于权威模型，同源模型共享盲区，复核等于自证。"""
    override = os.environ.get("RP_M_ANSWER_CHECKS")
    if override:
        return [stage.config.get(name) for name in override.split(",") if name.strip()]
    seen = {ground.family}
    picked = []
    for role in ("judge", "generator", "diagnoser"):
        for model in stage.config.by_role(role):
            if model.family in seen:
                continue
            seen.add(model.family)
            picked.append(model)
            if len(picked) == CROSS_CHECKS:
                return picked
    if len(picked) < CROSS_CHECKS:
        raise ValueError(
            f"交叉复核需要 {CROSS_CHECKS} 个异于 {ground.family} 的 family，"
            f"当前只有 {[m.name for m in picked]}；请在 config/models.json 补端点")
    return picked


def write_disputes(records):
    """把挂起的题单独落盘，便于人工裁决；无争议时也写空文件，避免读到上一轮的残留。"""
    path = ROOT / DISPUTE_PATH if not os.path.isabs(DISPUTE_PATH) else Path(DISPUTE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [r for r in records if r.get("answer_dispute")]
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({
                "rid": r.get("rid"), "question": r.get("question"),
                "reason": r.get("answer_dispute_reason"),
                "votes": r.get("_answer_votes"),
            }, ensure_ascii=False) + "\n")
    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    # grounder 是权威答案源，强制走配置里的闭源最强模型；缺角色就停，不回退。
    ground = stage.pick("RP_M_GROUND", "grounder")
    checkers = pick_checkers(ground)
    print(f"  权威答案模型 {ground.name}({ground.family})，"
          f"交叉复核 {', '.join(f'{m.name}({m.family})' for m in checkers)}")
    records = stage.read_jsonl(args.src)
    out, errors = stage.run(lambda r: resolve_record(r, ground, checkers), records,
                            workers=int(os.environ.get("RP_WORKERS", 8)), desc="s20_answer")
    if errors:
        raise SystemExit(f"independent answer resolution failed for {len(errors)} records")
    disputes = write_disputes(out)
    admitted = sum(1 for r in out if r.get("answer_admitted"))
    verifiable = sum(1 for r in out
                     if r.get("question_type") in ("verifiable", "hybrid"))
    for record in out:
        record.pop("_answer_meta", None)
    print(f"  可核验题 {verifiable}，共识准入 {admitted}，分歧挂起 {disputes}")
    stage.write_jsonl(args.out, out)


if __name__ == "__main__":
    main()
