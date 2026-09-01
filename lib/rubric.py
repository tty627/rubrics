"""lib/rubric.py —— rubric schema 语义的唯一实现。

流程位置：所有 stage / 脚本共享的基础库。方向、闸门、满分、得分率、veto 的
判定口径只允许在这里定义，业务代码一律调用，不许再内联公式
（2026-08-14 收敛：闸门判定曾在 s11Lb/s12L/export 手写三遍，s_max 在 8+ 处内联，
改一处口径要翻三处代码，极易漂移）。

口径（导师 2026-08-14 复核，见 CLAUDE.md 硬约束第 5 条）：
- is_positive = 方向（true=该做到 / false=不该出现），score 符号与之同向；
- is_gate     = 0/1 阀门，标 gated_answer 题的答案判据，由闸门规则推导，
                不是独立字段语义（is_gate 与 is_positive 方向相同）；
- is_veto     = 负向原则性错误的一票否决标记。与 is_gate 方向相反、独立成字段，
                判分侧必须能区分「必须做到」与「触犯即整题不合格」；
- s_max       = sum(正向 score)，分母只算正向（闸门项计入分母，
                归一化延后到判分阶段）；
- 得分率 rate = 判分得分 / s_max；veto 命中时整题 rate 归 0（不进补偿式求和）。

veto 设计约束（Qwen《Reinforcement Learning with Rubric Anchors》veto 机制 +
教育测量的 conjunctive/compensatory 混合模型）：
- veto 项必须**原子**：单一错误，不含「且/或」串接（阶段 10 用 SUBJ_DEG 同款正则兜底）；
- 判据是「触犯即整题不合格」，不是「扣分较多」；
- 规则必须**显式声明**（VETO_RULE），不能藏在权重里；
- 聚合走 apply_veto：raw_rate 保留不含 veto 的补偿式得分率，供区分度诊断
  （阶段 23）与审计使用，避免 veto 归零污染 gap/std/floor 判据。
"""
import re

# ---- 负项严重性分级（阶段 10 给负项打标，分值只作权重、分级独立成字段）----
SEVERITY_LEVELS = ('principle', 'major', 'minor')

# ---- 判定线词表（阶段 06 打质量标记、阶段 10 卡 veto 门槛，两处必须同口径）----
# 「可核验字面量」：数字、拉丁串、公式符号、引号包裹内容。有它才算判定线写实了。
ANCHOR = re.compile(r'[0-9A-Za-z=＝≈±<>≤≥$]|["“”]')
# 主观程度词：拿它当判定线的准则，判分器各判各的，不能当 veto 或扣分依据。
SUBJ_DEG = re.compile(r'严重|显著|根本性|明显|大幅|过度')

# ---- 闸门规则 ----
GATE_MIN_SCORE = 4      # 闸门项分值下限：低于此是支撑项，不冒充闸门
GATE_MIN_SHARE = 0.3    # 闸门项占满分比例下限（拆成两条的答案判据各自达标）

# ---- veto 聚合规则（交付说明必须引用同一句，口径不允许出现第二个版本）----
VETO_RULE = '任一 is_veto 项被判定成立 → 整题得分率为 0，不进补偿式求和'


def is_positive(c):
    """方向判定：is_positive 字段即方向（导师口径）。"""
    return bool(c.get('is_positive'))


def positives(criteria):
    """正向准则（该做到的）。"""
    return [c for c in criteria if is_positive(c)]


def negatives(criteria):
    """负向准则（不该出现的）。"""
    return [c for c in criteria if not is_positive(c)]


def s_max(criteria):
    """满分 = sum(正向 score)。分母只算正向，闸门项计入（硬约束第 5 条口径）。"""
    return sum(c['score'] for c in criteria if is_positive(c))


def is_gate(c, s_max_=None):
    """闸门判定：正向、score >= GATE_MIN_SCORE、且占满分 >= GATE_MIN_SHARE。

    s_max_ 缺省时不给闸门资格（False）——调用方要么已算好分母，要么用
    gate_indices() 现算，避免每个循环里重算一遍满分。
    """
    if not is_positive(c):
        return False
    if c.get('score', 0) < GATE_MIN_SCORE:
        return False
    s = s_max_ if s_max_ is not None else 0
    return bool(s) and c['score'] / s >= GATE_MIN_SHARE


def gate_indices(criteria, s_max_=None):
    """闸门准则在 criteria 中的下标（0 起）。s_max_ 缺省时按 criteria 现算。"""
    s = s_max_ if s_max_ is not None else s_max(criteria)
    return [i for i, c in enumerate(criteria) if is_gate(c, s)]


def rate(score, s_max_):
    """判分得分率 = 判分得分 / s_max（跨题可比性由得分率保证）。"""
    return round(score / s_max_, 4) if s_max_ else 0.0


# ---- veto 语义 ----
def is_veto(c):
    """veto 标记：只允许标在负向准则上（与 is_gate 方向相反，判分侧必须能区分）。"""
    return bool(c.get('is_veto')) and not is_positive(c)


def veto_items(criteria):
    """返回带 is_veto 标记的负向准则。"""
    return [c for c in criteria if is_veto(c)]


def apply_veto(raw_rate, vetoed):
    """聚合规则（VETO_RULE）：veto 命中 → 整题 0 分；否则维持补偿式得分率。"""
    return 0.0 if vetoed else raw_rate


def aggregate(items, s_max_):
    """对一份判分结果汇总，返回 {'raw_rate', 'veto_by', 'rate'}。

    items：判分条目（含 score / is_veto / met / _criterion_id 字段）。
    raw_rate = 所有 met 项得分之和 / s_max（补偿式，负项照常扣分）；
    veto_by  = 命中（met=true）的 veto 项 _criterion_id 列表；
    rate     = apply_veto(raw_rate, veto 是否命中)。
    s11Lc 等区分度诊断必须用 raw_rate，veto 归零单独统计（见 s11Lc 改动）。
    """
    raw = sum(c.get('score', 0) for c in items if c.get('met'))
    by = [c.get('_criterion_id') for c in items if c.get('met') and is_veto(c)]
    raw_rate = rate(raw, s_max_)
    return {'raw_rate': raw_rate, 'veto_by': by,
            'rate': apply_veto(raw_rate, bool(by))}
