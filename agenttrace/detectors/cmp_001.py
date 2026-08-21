"""CMP-001 Compaction / Prune 检测器。

定义(克制版):检测发生了 compaction/prune,并报告被 shadowed 的 token 数量
及相关 execution context。

重要术语约束:
- 不用 "wasted" / "avoidable" —— compaction 本身可能是必要的(上下文管理)
- 用 "shadowed tokens" / "compaction-affected tokens"
- 是否升级为 cost finding 留待以后有足够证据判断"异常压缩"时再做

数据源:Trace.events 中的 compaction 事件:
- compaction/prune:  data.shadowedTokenCount(被 shadowed 的 token 数)
- compaction/start:  data.compactionId, turn
- compaction/end:    data.error(压缩失败时)
- compaction/summary

Finding:
    rule_id = CMP-001
    type    = compaction_observed
    shadowed_token_count
    compaction_type
    related turn/step
    confidence
"""

from __future__ import annotations

from ..core.canonical_trace import Trace
from .base import Detector, Evidence, EvidenceChain, Finding

COMPACTION_EVENTS = {"compaction/prune", "compaction/start", "compaction/end", "compaction/summary"}


class CompactionDetector:
    """CMP-001:检测 compaction / prune 事件。"""

    rule_id = "CMP-001"
    version = "0.1.0"

    def detect(self, trace: Trace) -> list[Finding]:
        findings: list[Finding] = []

        for ev in trace.events:
            if ev.type not in COMPACTION_EVENTS:
                continue
            data = ev.data

            # 按 prune 聚合:prune 有 shadowedTokenCount,是最硬的证据
            if ev.type == "compaction/prune":
                shadowed = data.get("shadowedTokenCount", 0)
                shadowed_range = data.get("shadowedRange", {})
                shadowed_seqs = data.get("shadowedSeqs", [])
                chain = EvidenceChain()
                chain.add(
                    step_id=ev.step_id or 0,
                    turn_id=ev.turn_id or 0,
                    detail=f"compaction/prune (range {shadowed_range})",
                    observed_value=shadowed,  # shadowed tokens 是硬证据
                )
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        type="compaction_prune",
                        severity="info",  # 不是缺陷,是观测
                        confidence=0.99,  # DSH 自带数据,确定性
                        occurrences=1,
                        kind="observation",  # 观测资源量
                        evidence=chain.links,
                        details={
                            "event_type": ev.type,
                            "shadowed_token_count": shadowed,
                            "shadowed_range": shadowed_range,
                            "shadowed_seqs": shadowed_seqs,
                            "seq": ev.seq,
                            "time": ev.time,
                            "evidence_chain": chain,
                        },
                    )
                )
            elif ev.type == "compaction/end":
                # 记录压缩是否失败
                error = data.get("error")
                if error:
                    chain2 = EvidenceChain()
                    chain2.add(
                        step_id=ev.step_id or 0,
                        turn_id=ev.turn_id or 0,
                        detail=f"compaction/end error: {error}",
                    )
                    findings.append(
                        Finding(
                            rule_id=self.rule_id,
                            type="compaction_failed",
                            severity="warning",
                            confidence=0.99,
                            occurrences=1,
                            kind="observation",
                            evidence=chain2.links,
                            details={
                                "event_type": ev.type,
                                "compaction_id": data.get("compactionId"),
                                "error": error,
                                "seq": ev.seq,
                                "evidence_chain": chain2,
                            },
                        )
                    )
            # start / summary 也可单独记录,但 v0.1 只对 prune 和 end-error 出 finding

        return findings
