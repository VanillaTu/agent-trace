"""v0.3 semantic/architecture checkpoint 测试。

验证语义模型是否稳定到可扩展,重点:
1. Finding.kind 与 Attribution.kind 不强制绑定
2. tokens 支持 not_applicable(None ≠ 0)
3. EvidenceChain 公共化(四个 detector 统一)
4. 4×4 语义矩阵
5. report 四组语义隔离
6. SUB-001 不改核心 contract 可接入
"""

from __future__ import annotations

import pytest

from agenttrace.attribution import ALL_ATTRIBUTION_ENGINES
from agenttrace.core.canonical_trace import Step, ToolCall, Trace, TraceEvent, Turn, Usage
from agenttrace.detectors import ALL_DETECTORS
from agenttrace.detectors.base import EvidenceChain
from agenttrace.pipeline import diagnose
from agenttrace.report import render_report


def _full_trace():
    """构造同时含 4 种 kind 的 trace。"""
    t = Trace(session_id="semantic")
    turn = Turn(turn_id=1)

    # TOOL-001 (cost): 重复调用 + 高 reasoning
    s1 = Step(step_id=1, turn_id=1)
    s1.usage = Usage(input_tokens=1000, output_tokens=300, reasoning_tokens=4000)  # > P99
    s1.tool_calls.append(ToolCall(call_id="c1", tool_name="read_file", arguments='{"p":"a"}'))
    turn.steps.append(s1)

    s2 = Step(step_id=2, turn_id=1)
    s2.usage = Usage(input_tokens=2000, output_tokens=120, reasoning_tokens=50)
    s2.tool_calls.append(ToolCall(call_id="c2", tool_name="read_file", arguments='{"p":"a"}'))
    turn.steps.append(s2)

    t.turns = [turn]
    # CMP-001 (observation)
    t.events.append(TraceEvent(type="compaction/prune", data={"shadowedTokenCount": 8541}))
    # RETRY-001 (reliability)
    t.events.append(TraceEvent(type="llm/finish/error", seq=1, turn_id=1, step_id=1, data={"error_code": "TRANSPORT"}))
    t.events.append(TraceEvent(type="llm/retry", seq=2, turn_id=1, step_id=1, data={"retryId": "r1", "provider": "ollama", "mode": "normal"}))
    t.events.append(TraceEvent(type="llm/retry-started", seq=3, turn_id=1, step_id=1, data={"retryId": "r1", "retry": 1}))
    t.events.append(TraceEvent(type="llm/finish/success", seq=4, turn_id=1, step_id=1, data={}))
    return t


def test_semantic_matrix_4x4():
    """检查 6:4×4 语义矩阵。

    | rule | Finding.kind | Attribution.kind | tokens | avoidable |
    """
    t = _full_trace()
    result = diagnose(t)

    f_kind = {f.rule_id: f.kind for f in result.findings}
    a_kind = {a.rule_id: a.kind for a in result.attributions}

    assert f_kind["TOOL-001"] == "cost"
    assert f_kind["CMP-001"] == "observation"
    assert f_kind["THINK-001"] == "flag"
    assert f_kind["RETRY-001"] == "reliability"

    # Attribution.kind 不与 Finding.kind 强制绑定:
    # THINK finding=flag 但 attribution=observation
    # RETRY finding=reliability 但 attribution=reliability
    assert a_kind["TOOL-001"] == "cost"
    assert a_kind["CMP-001"] == "observation"
    assert a_kind["THINK-001"] == "observation"  # flag → observation
    assert a_kind["RETRY-001"] == "reliability"


def test_tokens_not_applicable_semantics():
    """检查 4:tokens 支持 not_applicable(None ≠ 0)。"""
    t = _full_trace()
    result = diagnose(t)
    retry_att = next(a for a in result.attributions if a.rule_id == "RETRY-001")
    # RETRY 的 tokens 是 None(not applicable),不是 0
    assert retry_att.direct.tokens is None
    assert retry_att.propagated.tokens is None


def test_evidence_chain_public_abstraction():
    """检查 5:EvidenceChain 是公共 abstraction,四个 detector 都产出。"""
    t = _full_trace()
    result = diagnose(t)
    for f in result.findings:
        chain = f.details.get("evidence_chain")
        assert chain is not None, f"{f.rule_id} 缺少 evidence_chain"
        assert isinstance(chain, EvidenceChain)
        assert len(chain.links) >= 1


def test_report_four_kinds_separated():
    """检查 3:report 按 Finding.kind 四组隔离。"""
    t = _full_trace()
    result = diagnose(t)
    report = render_report(result.trace, result.findings, result.attributions)
    assert "Cost defects" in report
    assert "Resource observations" in report
    assert "Statistical flags" in report
    assert "Reliability events" in report
    # 禁止 "Total wasted tokens"
    assert "Total wasted" not in report


def test_no_fictitious_cost_in_summary():
    """检查 summary 不混算三种语义。"""
    t = _full_trace()
    result = diagnose(t)
    report = render_report(result.trace, result.findings, result.attributions)
    # 汇总只列 finding 数,不把不同 kind 的 tokens 相加
    assert "个 finding" in report


def test_sub001_can_plug_in_without_contract_change():
    """检查 7:SUB-001 不改核心 contract 可接入。

    模拟一个"执行拓扑"detector(不实现,只验证 contract 足够)。
    """
    class SubAgentTopologyDetector:
        rule_id = "SUB-001"
        version = "0.1.0"

        def detect(self, trace: Trace):
            # 拓扑问题:可能有 finding 但完全没有 token 成本
            from agenttrace.detectors.base import Finding
            return [
                Finding(
                    rule_id="SUB-001",
                    type="subagent_topology",
                    severity="info",
                    confidence=0.8,
                    occurrences=1,
                    kind="observation",  # 拓扑是观测,无 token
                )
            ]

    class SubAgentAttribution:
        def attribute(self, trace, findings):
            from agenttrace.attribution.base import Attribution, DirectAttribution, PropagatedAttribution
            return [
                Attribution(
                    finding_id=f"finding-SUB-001-{i}",
                    rule_id="SUB-001",
                    finding_idx=f.finding_idx,
                    kind="observation",
                    direct=DirectAttribution(tokens=None),  # 拓扑无 token
                    propagated=PropagatedAttribution(tokens=None),
                    unattributed_tokens=None,
                    confidence=f.confidence,
                )
                for i, f in enumerate(findings)
            ]

    # 验证:不需改 Finding/Attribution/Report 核心 schema 就能表达 SUB-001
    det = SubAgentTopologyDetector()
    t = Trace(session_id="sub")
    findings = det.detect(t)
    atts = SubAgentAttribution().attribute(t, findings)
    report = render_report(t, findings, atts)
    assert "SUB-001" in report
    assert "observation" in findings[0].kind
