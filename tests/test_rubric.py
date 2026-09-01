#!/usr/bin/env python3
"""lib/rubric.py 的纯逻辑单测 —— 语义唯一实现的兜底（硬约束 5/6 的口径全在这）。

零 LLM、零网络、零第三方依赖，直接跑：
    python3 tests/test_rubric.py
改 lib/rubric.py 任何口径前先跑这个；make check 也会跑。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import rubric as R

pos = {'is_positive': True, 'score': 5}
neg = {'is_positive': False, 'score': -3}

# ---- 方向与分类 ----
assert R.is_positive(pos) and not R.is_positive(neg)
assert R.positives([pos, neg]) == [pos]
assert R.negatives([pos, neg]) == [neg]

# ---- s_max：分母只算正向（硬约束 5）----
assert R.s_max([pos, neg]) == 5
assert R.s_max([]) == 0

# ---- 闸门：正向 + 分值下限 + 占比下限，缺 s_max 不给资格 ----
gate = {'is_positive': True, 'score': 6}
assert R.is_gate(gate, s_max_=10)                        # 6>=4 且 6/10>=0.3
assert not R.is_gate({'is_positive': True, 'score': 2}, s_max_=10)   # 分值不够
assert not R.is_gate({'is_positive': True, 'score': 6}, s_max_=30)   # 占比不够
assert not R.is_gate(neg, s_max_=10)                     # 负向不给闸门
assert not R.is_gate(gate, None)                         # 缺分母不给资格
assert R.gate_indices([{'is_positive': True, 'score': 2}, gate], s_max_=11) == [1]

# ---- 得分率 ----
assert R.rate(6, 10) == 0.6
assert R.rate(0, 0) == 0.0

# ---- veto：负项专属（硬约束 6）----
veto = {'is_positive': False, 'is_veto': True, 'score': -8}
assert R.is_veto(veto)
assert not R.is_veto({'is_positive': True, 'is_veto': True, 'score': 8})
assert R.veto_items([pos, neg, veto]) == [veto]

# ---- 聚合：raw 补偿式 / veto 归零（VETO_RULE）----
items = [
    {'_criterion_id': 'L1', 'is_positive': True, 'score': 5, 'met': True,
     'is_veto': False},
    {'_criterion_id': 'L2', 'is_positive': False, 'score': -3, 'met': True,
     'is_veto': False},
    {'_criterion_id': 'L3', 'is_positive': False, 'score': -8, 'met': False,
     'is_veto': True},
]
agg = R.aggregate(items, 5)
assert agg['raw_rate'] == 0.4 and agg['rate'] == 0.4 and agg['veto_by'] == []
items[2]['met'] = True
agg = R.aggregate(items, 5)
assert agg['raw_rate'] == -1.2          # (5-3-8)/5，补偿式允许为负
assert agg['rate'] == 0.0 and agg['veto_by'] == ['L3']   # veto 命中 → 整题 0

print('test_rubric: 全部断言通过 ✓')
