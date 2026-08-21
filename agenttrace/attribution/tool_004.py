"""TOOL-004 Attribution Engine。

定位(design detector-tool-004 D3):flag 标记归因,无 token 成本。

- kind = "flag":归因到的"东西"就是"可避免失败尝试"标记本身,
  无资源/成本可归因;与 Finding.kind(flag)语义一致,不违反
  "Finding.kind 与 Attribution.kind 解耦"铁律(解耦是"不强制一一对应")。
- 铁律:失败 attempt 无 usage → direct.tokens / propagated.tokens /
  unattributed_tokens 三处均为 None(not applicable),不是 0;
  **不把成功重试的 usage 算进失败 attempt**(重试是必要修正,其 usage 不是浪费)。
- candidate_step_ids 存 (turn_id, step_id) 复合键(step_id 每 turn 重新编号,
  裸 int 跨 turn 有歧义;与 TOOL-001 attribution 同约定)。
"""

from __future__ import annotations

from ..core.canonical_trace import Trace
from .base import Attribution, DirectAttribution, PropagatedAttribution


class Tool004AttributionEngine:
    """TOOL-004 的 attribution(flag 标记,无 token 归因)。"""

    def attribute(self, trace: Trace, findings) -> list[Attribution]:
        attributions: list[Attribution] = []
        for idx, f in enumerate(findings):
            if f.rule_id != "TOOL-004":
                continue
            attributions.append(
                Attribution(
                    finding_id=f"finding-{f.rule_id}-{idx}",
                    rule_id=f.rule_id,
                    finding_idx=idx,
                    kind="flag",  # 标记"可避免的失败尝试",无资源/成本可归因
                    direct=DirectAttribution(
                        baseline_step_ids=[],  # 无 baseline 概念
                        # 失败 attempt 的 (turn_id, step_id) 复合键(评审 m2)
                        candidate_step_ids=[f.details["failed_index"]],
                        tokens=None,  # not applicable(失败 attempt 无 usage)
                    ),
                    propagated=PropagatedAttribution(step_ids=[], tokens=None),
                    unattributed_tokens=None,  # not applicable
                    confidence=f.confidence,
                )
            )
        return attributions
