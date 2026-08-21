"""THINK-001 Attribution Engine。

保守版:
- direct = reasoning_tokens(报告该 step 的 reasoning 消耗)
- propagated = 0
- **不声明 avoidable**(这是 flag,不是 defect;无 counterfactual 证据)
- confidence 继承 finding

注意:direct.tokens 在这里是"该 step 的 reasoning 消耗量"的观测,
不是"可避免的成本"。报告层应据此不显示 avoidable 措辞。
"""

from __future__ import annotations

from ..core.canonical_trace import Trace
from .base import Attribution, DirectAttribution, PropagatedAttribution


class Think001AttributionEngine:
    """THINK-001 的 attribution(观测型,非 cost 型)。"""

    def attribute(self, trace: Trace, findings) -> list[Attribution]:
        attributions: list[Attribution] = []
        for idx, f in enumerate(findings):
            if f.rule_id != "THINK-001":
                continue
            reasoning = f.details.get("reasoning_tokens", 0)
            attributions.append(
                Attribution(
                    finding_id=f"finding-{f.rule_id}-{idx}",
                    rule_id=f.rule_id,
                    finding_idx=idx,
                    kind="observation",  # 归因到 reasoningTokens 观测(非 flag 语义)
                    direct=DirectAttribution(
                        baseline_step_ids=[],
                        candidate_step_ids=[f.evidence[0].step_id] if f.evidence else [],
                        tokens=reasoning,  # 观测值,非 avoidable
                    ),
                    propagated=PropagatedAttribution(step_ids=[], tokens=0),
                    unattributed_tokens=0,
                    confidence=f.confidence,
                )
            )
        return attributions
