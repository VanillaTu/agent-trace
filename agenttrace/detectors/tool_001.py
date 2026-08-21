"""TOOL-001 Duplicate Tool Call 检测器。

定义:同一 Agent execution 中,相同 Tool + 等价参数被重复执行(同 fingerprint),
且后续执行没有新的可识别上下文价值。

规则(第一版,确定性):
    for each step:
        for each tool_call:
            fingerprint = normalize(tool_name, arguments)
    group calls by fingerprint
    for each group:
        if count > 1:
            emit TOOL-001, 记录 occurrences + occurrence_index

边界(Detector limitation / false-positive boundary):
- 无状态工具(如 get_current_time、get_random、get_timestamp)即使 fingerprint
  相同也通常不是浪费——但第一版 Detector 只负责"标记重复",是否判定可避免
  交给 Attribution Engine。
"""

from __future__ import annotations

from ..core.canonical_trace import Trace
from ..core.normalize import call_fingerprint
from .base import Detector, Evidence, EvidenceChain, Finding

# 无状态工具:每次调用都可能产生不同结果,重复调用不一定浪费
# (标记出来供 Attribution Engine 降权,但 Detector 仍输出候选)
STATELESS_TOOLS = {
    "get_time",
    "get_current_time",
    "get_timestamp",
    "get_random",
    "get_random_number",
    "web_search",  # 结果随时间变化
    "web_search_deepseek",
}


class DuplicateToolCallDetector:
    """TOOL-001:重复工具调用。"""

    rule_id = "TOOL-001"
    version = "0.1.0"

    def detect(self, trace: Trace) -> list[Finding]:
        findings: list[Finding] = []

        # 按 fingerprint 分组,记录 (step, turn, call, index)
        groups: dict[str, list[dict]] = {}
        for step in trace.all_steps():
            for tc in step.tool_calls:
                fp = call_fingerprint(tc.tool_name, tc.arguments)
                tc.fingerprint = fp
                groups.setdefault(fp, []).append(
                    {
                        "step": step,
                        "tool_call": tc,
                        "turn_id": step.turn_id,
                        "step_id": step.step_id,
                    }
                )

        for fp, items in groups.items():
            if len(items) < 2:
                continue
            tool_name = items[0]["tool_call"].tool_name
            severity = "low"
            if tool_name in STATELESS_TOOLS:
                # 无状态工具:候选但降权(confidence 降低,交给 attribution 决定)
                confidence = 0.55
                severity = "low"
            else:
                # 确定性重复:高置信度候选
                confidence = 0.98
                severity = "medium" if len(items) <= 3 else "high"

            # 统一用公共 EvidenceChain(每步一个 link,含 observed_value)
            chain = EvidenceChain()
            for idx, it in enumerate(items):
                chain.add(
                    step_id=it["step_id"],
                    turn_id=it["turn_id"],
                    detail=(
                        f"{tool_name} occurrence #{idx+1}/{len(items)} "
                        f"args={it['tool_call'].arguments[:80]}"
                    ),
                    observed_value=None,
                )

            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    type="duplicate_tool_call",
                    severity=severity,
                    confidence=confidence,
                    occurrences=len(items),
                    kind="cost",  # 候选可避免成本
                    evidence=chain.links,
                    fingerprint=fp,
                    details={
                        "tool_name": tool_name,
                        # (turn_id, step_id) 复合定位:step_id 每个 turn 重新编号,单独存会跨 turn 冲突
                        "occurrence_indexes": [(it["turn_id"], it["step_id"]) for it in items],
                        "stateless": tool_name in STATELESS_TOOLS,
                        "arguments": items[0]["tool_call"].arguments[:200],
                        "evidence_chain": chain,
                    },
                )
            )

        return findings
