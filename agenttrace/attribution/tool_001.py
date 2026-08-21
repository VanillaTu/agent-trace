"""TOOL-001 Attribution Engine。

把 TOOL-001 Finding 的成本归因出来,遵循 v0.2 严格范围:

1. Direct:
   - baseline step(第一次调用):必要,不计入候选
   - duplicate steps(第 2..N 次):candidate avoidable
   - direct.tokens = duplicate steps 的 output_tokens 之和
     (保守:output 是这次调用直接产生的,可归因;input 大量是历史 context,不进 direct)

2. Conservative Propagation:
   - duplicate 调用后,若产生了"额外"的 assistant step,其 output_tokens 计入
   - v0.2 只做最保守的一种:把最后一次 duplicate 之后、紧邻的后续 step
     视为"可能因重复调用而多余",但其 output 计入 propagated
   - 不做完整因果推断

3. Unattributed:
   - 各 duplicate step 的 input_tokens(含历史 context)不进 direct/propagated,
     标记为 unattributed_tokens

4. Confidence:
   - 高置信(stateless=False):0.95
   - 无状态工具(stateless=True):0.60(不直接认定浪费)

铁律:不把 input_tokens 全额算成 defect cost。
"""

from __future__ import annotations

from ..core.canonical_trace import Trace
from .base import Attribution, DirectAttribution, PropagatedAttribution

# stateless 工具的置信度衰减
STATELESS_CONFIDENCE = 0.60
DETERMINISTIC_CONFIDENCE = 0.95


class Tool001AttributionEngine:
    """TOOL-001 的成本归因引擎。"""

    def __init__(self) -> None:
        pass

    def attribute(self, trace: Trace, findings) -> list[Attribution]:
        """为 TOOL-001 findings 生成 attribution。

        findings 必须已由 DuplicateToolCallDetector 产生(含 occurrence_indexes)。
        证据链由 detector 负责,attribution 只算钱,不重建 evidence_chain。
        """
        # 预构建 step 索引:(turn_id, step_id) 复合 key —— step_id 每个 turn 重新编号,
        # 用单独 step_id 作 key 会跨 turn 覆盖(真实 bug:会话 "1" E2E 测试发现 3/4 归因错误)
        steps_by_key: dict[tuple, object] = {}
        ordered_steps: list[object] = []
        for turn in trace.turns:
            for st in turn.steps:
                steps_by_key[(turn.turn_id, st.step_id)] = st
                ordered_steps.append(st)

        attributions: list[Attribution] = []
        for idx, f in enumerate(findings):
            if f.rule_id != "TOOL-001":
                continue
            occ_indexes = f.details.get("occurrence_indexes", [])
            stateless = f.details.get("stateless", False)

            # Direct:第一个 occurrence 是 baseline,后续是 duplicate
            baseline_ids: list = []
            candidate_ids: list = []
            direct_tokens = 0
            unattributed = 0

            for i, occ in enumerate(occ_indexes):
                # occurrence_indexes 存 (turn_id, step_id) 复合元组(detector 保证)
                key = occ
                st = steps_by_key.get(key)
                output = st.usage.output_tokens if st else 0
                input_ = st.usage.input_tokens if st else 0
                if i == 0:
                    baseline_ids.append(key)
                else:
                    candidate_ids.append(key)
                    direct_tokens += output
                    # input 是完整上下文,标记为 unattributed(不强行归因)
                    unattributed += input_

            # Conservative Propagation:
            # 最后一次 duplicate 之后紧邻的 assistant step,视为可能因重复而多余
            propagated_ids: list = []
            propagated_tokens = 0
            if candidate_ids:
                last_key = candidate_ids[-1]
                last_step = steps_by_key.get(last_key)
                if last_step is not None and last_step in ordered_steps:
                    last_dup_idx = ordered_steps.index(last_step)
                    # 向后找 1 个 step(最保守)
                    for nxt in ordered_steps[last_dup_idx + 1 : last_dup_idx + 2]:
                        propagated_ids.append((nxt.turn_id, nxt.step_id))
                        propagated_tokens += nxt.usage.output_tokens

            confidence = STATELESS_CONFIDENCE if stateless else DETERMINISTIC_CONFIDENCE

            attributions.append(
                Attribution(
                    finding_id=f"finding-{f.rule_id}-{idx}",
                    rule_id=f.rule_id,
                    finding_idx=idx,
                    kind="cost",  # 候选可避免成本
                    direct=DirectAttribution(
                        baseline_step_ids=baseline_ids,
                        candidate_step_ids=candidate_ids,
                        tokens=direct_tokens,
                    ),
                    propagated=PropagatedAttribution(
                        step_ids=propagated_ids, tokens=propagated_tokens
                    ),
                    unattributed_tokens=unattributed,
                    confidence=confidence,
                )
            )

        return attributions
