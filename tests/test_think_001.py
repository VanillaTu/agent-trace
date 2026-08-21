"""THINK-001 Detector + Attribution 测试。

验证:
1. reasoning >= P99 → extreme flag(not defect)
2. P95 <= reasoning < P99 → high flag
3. reasoning < P95 → 不报
4. reasoning missing(None)→ 不报,不报错
5. output_tokens == 0 → 不算 ratio,不当作 0
6. 置信度:tool call 时 0.9,无 tool 时 0.8
7. 术语:不叫 over-reasoning,不声明 avoidable
"""

from __future__ import annotations

import pytest

from agenttrace.attribution.think_001 import Think001AttributionEngine
from agenttrace.core.canonical_trace import Step, ToolCall, Trace, Turn, Usage
from agenttrace.detectors.think_001 import (
    REASONING_P95,
    REASONING_P99,
    ReasoningIntensityDetector,
)

DETECTOR = ReasoningIntensityDetector()
ENGINE = Think001AttributionEngine()


def _trace_with_step(reasoning, output, has_tool=True, tool_name="read_file"):
    t = Trace(session_id="think-test")
    st = Step(step_id=1, turn_id=1)
    st.usage = Usage(input_tokens=1000, output_tokens=output, reasoning_tokens=reasoning)
    if has_tool:
        st.tool_calls.append(ToolCall(call_id="c1", tool_name=tool_name, arguments="{}"))
    t.turns = [Turn(turn_id=1, steps=[st])]
    return t


def test_extreme_flag_at_p99():
    t = _trace_with_step(REASONING_P99, 4000, has_tool=True)
    findings = DETECTOR.detect(t)
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_id == "THINK-001"
    assert "extreme" in f.type
    assert f.confidence == 0.9  # 有 tool call


def test_extreme_flag_no_tool_lower_confidence():
    t = _trace_with_step(REASONING_P99, 4000, has_tool=False)
    findings = DETECTOR.detect(t)
    assert len(findings) == 1
    assert findings[0].confidence == 0.8  # 无 tool call,更可疑但仍非缺陷


def test_high_flag_at_p95():
    t = _trace_with_step(REASONING_P95, 2000)
    findings = DETECTOR.detect(t)
    assert len(findings) == 1
    assert "high" in findings[0].type
    assert findings[0].confidence == 0.7


def test_below_p95_not_reported():
    t = _trace_with_step(500, 2000)  # < P95
    findings = DETECTOR.detect(t)
    assert findings == []


def test_missing_reasoning_not_reported_no_error():
    # reasoning_tokens=None → 跳过,不报错
    t = Trace(session_id="think")
    st = Step(step_id=1, turn_id=1)
    st.usage = Usage(input_tokens=1000, output_tokens=500, reasoning_tokens=None)
    t.turns = [Turn(turn_id=1, steps=[st])]
    findings = DETECTOR.detect(t)
    assert findings == []


def test_output_zero_ratio_safe():
    # output=0 时不算 ratio,不当作 0
    t = _trace_with_step(REASONING_P99, 0, has_tool=True)
    findings = DETECTOR.detect(t)
    assert len(findings) == 1
    assert findings[0].details["reasoning_ratio"] is None  # 不算 ratio


def test_terminology_not_overreasoning():
    t = _trace_with_step(REASONING_P99, 4000)
    findings = DETECTOR.detect(t)
    report_text = str(findings[0].details)
    assert "over_reasoning" not in report_text.lower()
    assert "过度推理" not in report_text
    # 用 intensity / flag 措辞
    assert "level" in findings[0].details


def test_attribution_observational_not_avoidable():
    t = _trace_with_step(REASONING_P99, 4000)
    findings = DETECTOR.detect(t)
    atts = ENGINE.attribute(t, findings)
    a = atts[0]
    assert a.direct.tokens == REASONING_P99  # 观测值
    assert a.propagated.tokens == 0
    # 置信度继承
    assert a.confidence == findings[0].confidence


def test_multiple_steps_flags():
    t = Trace(session_id="think")
    turn = Turn(turn_id=1)
    specs = [
        (REASONING_P99 + 100, 4000, True),  # extreme
        (REASONING_P95, 2000, False),  # high
        (500, 2000, True),  # 不报
    ]
    for i, (r, o, tool) in enumerate(specs, 1):
        st = Step(step_id=i, turn_id=1)
        st.usage = Usage(input_tokens=1000, output_tokens=o, reasoning_tokens=r)
        if tool:
            st.tool_calls.append(ToolCall(call_id=f"c{i}", tool_name="x", arguments="{}"))
        turn.steps.append(st)
    t.turns = [turn]
    findings = DETECTOR.detect(t)
    assert len(findings) == 2  # 只报 extreme + high
