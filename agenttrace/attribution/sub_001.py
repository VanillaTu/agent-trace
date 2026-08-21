"""SUB-001 Attribution Engine。

定位:execution topology observation,无 token cost。
- kind = observation
- tokens = None(descriptor 无成本字段,无证据不做成本归因)
"""

from __future__ import annotations

from ..core.canonical_trace import Trace
from .base import Attribution, DirectAttribution, PropagatedAttribution


class Sub001AttributionEngine:
    """SUB-001 的 attribution(拓扑观测,无 cost)。"""

    def attribute(self, trace: Trace, findings) -> list[Attribution]:
        attributions: list[Attribution] = []
        for idx, f in enumerate(findings):
            if f.rule_id != "SUB-001":
                continue
            attributions.append(
                Attribution(
                    finding_id=f"finding-{f.rule_id}-{idx}",
                    rule_id=f.rule_id,
                    finding_idx=idx,
                    kind="observation",
                    direct=DirectAttribution(
                        baseline_step_ids=[],
                        candidate_step_ids=[f.evidence[0].step_id] if f.evidence else [],
                        tokens=None,  # not applicable(无成本证据)
                    ),
                    propagated=PropagatedAttribution(step_ids=[], tokens=None),
                    unattributed_tokens=None,
                    confidence=f.confidence,
                )
            )
        return attributions
