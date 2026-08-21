"""CMP-001 Attribution Engine。

对 CMP-001:
- direct = shadowed_token_count(DSH 自带的硬证据)
- propagated = 0(第一版不做压缩的级联成本推断)
- 术语:shadowed / compaction-affected tokens,不用 "wasted"/"avoidable"
"""

from __future__ import annotations

from ..core.canonical_trace import Trace
from .base import Attribution, DirectAttribution, PropagatedAttribution


class Cmp001AttributionEngine:
    """CMP-001 的成本归因引擎(保守版)。"""

    def attribute(self, trace: Trace, findings) -> list[Attribution]:
        attributions: list[Attribution] = []
        for idx, f in enumerate(findings):
            if f.rule_id != "CMP-001":
                continue
            shadowed = f.details.get("shadowed_token_count", 0)
            attributions.append(
                Attribution(
                    finding_id=f"finding-{f.rule_id}-{idx}",
                    rule_id=f.rule_id,
                    finding_idx=idx,
                    kind="observation",  # 观测资源量,非 avoidable
                    direct=DirectAttribution(
                        baseline_step_ids=[],
                        candidate_step_ids=[f.evidence[0].step_id] if f.evidence else [],
                        # shadowed 是 DSH 记录的实际被压缩 token,作为 direct
                        tokens=shadowed,
                    ),
                    propagated=PropagatedAttribution(step_ids=[], tokens=0),
                    unattributed_tokens=0,
                    confidence=f.confidence,
                )
            )
        return attributions
