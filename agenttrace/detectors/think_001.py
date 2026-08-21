"""THINK-001 reasoning-token intensity flag 检测器。

定位(数据驱动,克制版):
- **不是 defect 检测器,是 observability flag**
- 只报告"reasoning 消耗异常高",不判定"过度推理"
- 不声明 avoidable(无 counterfactual 证据)

阈值来源(56 会话、2042 step 真实分布):
    P95 = 1498  → high-intensity flag
    P99 = 3451  → extreme flag

证据:reasoning_tokens / output_tokens / reasoning_ratio + baseline P50/P95/P99
置信度:
    reasoning >= P99 且有 tool call → 0.9(决策点特征,非缺陷)
    reasoning >= P99 无 tool call   → 0.8(更可疑,仍非缺陷)
    P95 <= reasoning < P99          → 0.7
"""

from __future__ import annotations

from ..core.canonical_trace import Trace
from .base import Detector, Evidence, EvidenceChain, Finding

# 来自真实分布的 baseline(2042 steps)
REASONING_P95 = 1498
REASONING_P99 = 3451
REASONING_P50 = 162


class ReasoningIntensityDetector:
    """THINK-001:reasoning-token intensity anomaly flag。"""

    rule_id = "THINK-001"
    version = "0.1.0"

    def detect(self, trace: Trace) -> list[Finding]:
        findings: list[Finding] = []

        for st in trace.all_steps():
            r = st.usage.reasoning_tokens
            if r is None:
                continue
            o = st.usage.output_tokens
            ratio = (r / o) if o and o > 0 else None
            has_tool = bool(st.tool_calls)

            if r >= REASONING_P99:
                severity = "info"
                confidence = 0.9 if has_tool else 0.8
                level = "extreme"
            elif r >= REASONING_P95:
                severity = "info"
                confidence = 0.7
                level = "high"
            else:
                continue

            chain = EvidenceChain()
            chain.add(
                step_id=st.step_id,
                turn_id=st.turn_id,
                detail=(
                    f"reasoning intensity {level} (output={o}"
                    f"{f' ratio={ratio:.2f}' if ratio is not None else ', no output'}) "
                    f"tools={[tc.tool_name for tc in st.tool_calls]}"
                ),
                observed_value=r,  # reasoning_tokens
            )

            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    type=f"reasoning_intensity_{level}",
                    severity=severity,
                    confidence=confidence,
                    occurrences=1,
                    kind="flag",  # 统计强度标记
                    evidence=chain.links,
                    details={
                        "reasoning_tokens": r,
                        "output_tokens": o,
                        "reasoning_ratio": ratio,
                        "has_tool_call": has_tool,
                        "level": level,
                        "baseline": {"p50": REASONING_P50, "p95": REASONING_P95, "p99": REASONING_P99},
                        "flag_reason": f"reasoning_tokens >= {'P99' if level == 'extreme' else 'P95'}",
                        "evidence_chain": chain,
                    },
                )
            )

        return findings
