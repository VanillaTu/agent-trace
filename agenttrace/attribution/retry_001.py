"""RETRY-001 Attribution Engine。

定位:reliability observation,无 token cost。
- kind = observation
- direct/propagated = 0(不产生虚构 token cost)
- 数据证据:失败的 retry attempt usage=0
- 未来若 provider 能可靠关联第二次 attempt usage,才允许 kind=cost(未来 capability)
"""

from __future__ import annotations

from ..core.canonical_trace import Trace
from .base import Attribution, DirectAttribution, PropagatedAttribution


class Retry001AttributionEngine:
    """RETRY-001 的 attribution(可靠性观测,无 cost)。"""

    def attribute(self, trace: Trace, findings) -> list[Attribution]:
        attributions: list[Attribution] = []
        for idx, f in enumerate(findings):
            if f.rule_id != "RETRY-001":
                continue
            attributions.append(
                Attribution(
                    finding_id=f"finding-{f.rule_id}-{idx}",
                    rule_id=f.rule_id,
                    finding_idx=idx,
                    kind="reliability",  # 可靠性事件,无 token cost
                    direct=DirectAttribution(
                        baseline_step_ids=[],
                        candidate_step_ids=[f.evidence[0].step_id] if f.evidence else [],
                        tokens=None,  # not applicable
                    ),
                    propagated=PropagatedAttribution(step_ids=[], tokens=None),
                    unattributed_tokens=None,  # not applicable
                    confidence=f.confidence,
                )
            )
        return attributions
