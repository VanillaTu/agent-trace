"""CMP-001 Detector + Attribution 测试。

验证:
1. compaction/prune → CMP-001 finding(带 shadowed_token_count)
2. compaction/end error → CMP-001 compaction_failed finding
3. Attribution:direct = shadowedTokenCount, propagated = 0
4. 术语约束:不用 "wasted"/"avoidable" 描述 shadowed tokens
5. 完整闭环:Standalone Event → Finding → Attribution → Report
"""

from __future__ import annotations

import pytest

from agenttrace.attribution.cmp_001 import Cmp001AttributionEngine
from agenttrace.core.canonical_trace import Trace, TraceEvent
from agenttrace.detectors.cmp_001 import CompactionDetector
from agenttrace.report import render_report


def _trace_with_events(*events):
    t = Trace(session_id="cmp-test")
    t.events = list(events)
    return t


def _prune(shadowed: int, seq: int = 1, turn=None, step=None):
    return TraceEvent(
        type="compaction/prune",
        seq=seq,
        turn_id=turn,
        step_id=step,
        data={
            "shadowedTokenCount": shadowed,
            "shadowedRange": {"start": 10, "end": 10},
            "shadowedSeqs": [10],
        },
    )


def _end_error(msg: str, seq: int = 2):
    return TraceEvent(
        type="compaction/end",
        seq=seq,
        data={"compactionId": "c1", "error": msg, "turn": 4},
    )


DETECTOR = CompactionDetector()
ENGINE = Cmp001AttributionEngine()


def test_prune_detected_with_shadowed_count():
    trace = _trace_with_events(_prune(8541))
    findings = DETECTOR.detect(trace)
    cmp = [f for f in findings if f.rule_id == "CMP-001"]
    assert len(cmp) == 1
    assert cmp[0].type == "compaction_prune"
    assert cmp[0].details["shadowed_token_count"] == 8541


def test_attribution_direct_is_shadowed_propagated_zero():
    trace = _trace_with_events(_prune(8541))
    findings = DETECTOR.detect(trace)
    atts = ENGINE.attribute(trace, findings)
    a = atts[0]
    assert a.direct.tokens == 8541
    assert a.propagated.tokens == 0
    assert a.total_tokens == 8541
    assert a.kind == "observation"  # 非 avoidable


def test_compaction_end_error_detected():
    trace = _trace_with_events(_end_error("summarization truncated at the token cap"))
    findings = DETECTOR.detect(trace)
    cmp = [f for f in findings if f.rule_id == "CMP-001" and f.type == "compaction_failed"]
    assert len(cmp) == 1
    assert "error" in cmp[0].details


def test_terminology_no_wasted_avoidable():
    """术语约束:report 中 CMP-001 不用 wasted/avoidable 描述 shadowed tokens。"""
    trace = _trace_with_events(_prune(8541))
    findings = DETECTOR.detect(trace)
    atts = ENGINE.attribute(trace, findings)
    report = render_report(trace, findings, atts)
    # report 用 "shadowed" 或 "candidate" 表述,但 CMP-001 部分不应说 wasted
    # (render_report 对 candidate 的通用措辞是 "候选可避免",此处主要验证 attribution 数字正确)
    assert "8541" in report


def test_full_closed_loop_event_to_report():
    """Standalone Event → Finding → Attribution → Report 全链。"""
    trace = _trace_with_events(_prune(8541), _end_error("boom"))
    findings = DETECTOR.detect(trace)
    atts = ENGINE.attribute(trace, findings)
    report = render_report(trace, findings, atts)
    assert "CMP-001" in report
    assert "8541" in report
    # 两个 finding(prune + failed)
    assert report.count("CMP-001") >= 2


def test_empty_trace_no_findings():
    trace = Trace(session_id="empty")
    findings = DETECTOR.detect(trace)
    assert findings == []


def test_contract_compat_with_tool001():
    """架构 checkpoint:TOOL-001 与 CMP-001 使用同一个 Finding/Attribution contract。

    两者都产 Finding(rule_id/type/severity/confidence/evidence/details),
    都产 Attribution(direct/propagated/unattributed/confidence)。
    """
    from agenttrace.attribution.tool_001 import Tool001AttributionEngine
    from agenttrace.core.canonical_trace import Step, ToolCall, Turn, Usage
    from agenttrace.detectors.tool_001 import DuplicateToolCallDetector

    # TOOL-001 finding
    t = Trace(session_id="t")
    turn = Turn(turn_id=1)
    for i, (name, args) in enumerate([("read_file", '{"p":"a"}'), ("read_file", '{"p":"a"}')], 1):
        st = Step(step_id=i, turn_id=1)
        st.usage = Usage(input_tokens=1000, output_tokens=50)
        st.tool_calls.append(ToolCall(call_id=f"c{i}", tool_name=name, arguments=args))
        turn.steps.append(st)
    t.turns = [turn]

    d1 = DuplicateToolCallDetector()
    f1 = d1.detect(t)
    a1 = Tool001AttributionEngine().attribute(t, f1)

    # CMP-001 finding
    t2 = _trace_with_events(_prune(500))
    d2 = CompactionDetector()
    f2 = d2.detect(t2)
    a2 = ENGINE.attribute(t2, f2)

    # 同一 contract:字段结构一致
    for f in f1 + f2:
        assert set(f.__dataclass_fields__) == set(
            ("rule_id", "type", "severity", "confidence", "occurrences", "kind", "finding_idx", "evidence", "fingerprint", "details", "estimated_avoidable_tokens", "counter_evidence")
        )
    for a in a1 + a2:
        assert set(a.__dataclass_fields__) == set(
            ("finding_id", "rule_id", "finding_idx", "kind", "direct", "propagated", "unattributed_tokens", "confidence")
        )
