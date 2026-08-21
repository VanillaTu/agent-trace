"""SUB-001 Subagent Delegation / Execution Topology Observation 检测器。

定位(数据驱动):
- **observation,不是 defect,不是 token 归因**
- 数据证据:subagent/descriptor 只声明委托,无 outcome/parent/cost 字段
- 真实数据:15 个 descriptor 全 flat,每会话 1 个,无嵌套

Finding:
    mode / provider / label / agent_model / agent_provider
    kind = observation

诚实边界:
- 无 outcome(数据无 completion 事件)→ 不猜
- 无 parent id → 不重建 parent/subagent 关系
- 无 cost 字段 → tokens = None(无证据不做成本归因)
"""

from __future__ import annotations

from ..core.canonical_trace import Trace
from .base import Detector, Evidence, EvidenceChain, Finding


class SubagentDelegationDetector:
    """SUB-001:subagent 委托观测。"""

    rule_id = "SUB-001"
    version = "0.1.0"

    def detect(self, trace: Trace) -> list[Finding]:
        findings: list[Finding] = []
        for ev in trace.events:
            if ev.type != "subagent/descriptor":
                continue
            data = ev.data
            mode = data.get("mode", "unknown")
            provider = data.get("provider", "unknown")
            label = data.get("label", "") or "(no label)"
            agent_model = data.get("agentModel")
            agent_provider = data.get("agentProvider")

            chain = EvidenceChain()
            chain.add(
                step_id=ev.step_id or 0,
                turn_id=ev.turn_id or 0,
                detail=(
                    f"subagent delegation mode={mode} provider={provider} "
                    f"label={label[:60]}"
                ),
                observed_value=None,
            )

            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    type="subagent_delegation",
                    severity="info",  # 观测,非缺陷
                    confidence=0.99,  # descriptor 是明确事件
                    occurrences=1,
                    kind="observation",
                    evidence=chain.links,
                    details={
                        "mode": mode,
                        "provider": provider,
                        "label": label,
                        "agent_model": agent_model,
                        "agent_provider": agent_provider,
                        "version": data.get("version"),
                        "seq": ev.seq,
                        "time": ev.time,
                        "evidence_chain": chain,
                    },
                )
            )
        return findings
