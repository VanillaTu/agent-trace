"""v0.2 Attribution Engine 测试(TOOL-001)。

验证:
1. Direct:baseline 不计入候选,duplicate 计入(按 output_tokens)
2. Conservative Propagation:duplicate 后紧邻 step 的 output 计入 propagated
3. Unattributed:duplicate step 的 input_tokens(历史 context)不强行归因
4. Confidence:stateless 降权 vs 确定性高置信
5. 完整流水线:detect → attribute → report 输出可读报告
"""

from __future__ import annotations

import pytest

from agenttrace.attribution.tool_001 import Tool001AttributionEngine
from agenttrace.core.canonical_trace import Step, ToolCall, Trace, Turn, Usage
from agenttrace.detectors.tool_001 import DuplicateToolCallDetector
from agenttrace.report import render_report


def _make_trace(steps_spec):
    """steps_spec: list of (tool_name, args, is_error, input_tokens, output_tokens)"""
    trace = Trace(session_id="attr-test")
    turn = Turn(turn_id=1)
    for idx, (name, args, is_err, inp, out) in enumerate(steps_spec, start=1):
        st = Step(step_id=idx, turn_id=1)
        st.usage = Usage(input_tokens=inp, output_tokens=out)
        st.tool_calls.append(
            ToolCall(
                call_id=f"call_{idx}",
                tool_name=name,
                arguments=args,
                result="err" if is_err else "ok",
                is_error=is_err,
            )
        )
        turn.steps.append(st)
    trace.turns = [turn]
    return trace


DETECTOR = DuplicateToolCallDetector()
ENGINE = Tool001AttributionEngine()


def test_direct_baseline_not_counted_duplicates_counted():
    # read x3: step1 baseline(不计), step2/3 duplicate(计入 output)
    trace = _make_trace(
        [
            ("read_file", '{"path":"a.py"}', False, 4000, 100),  # baseline
            ("read_file", '{"path":"a.py"}', False, 5000, 120),  # dup
            ("read_file", '{"path":"a.py"}', False, 6000, 90),  # dup
        ]
    )
    findings = DETECTOR.detect(trace)
    atts = ENGINE.attribute(trace, findings)
    assert len(atts) == 1
    a = atts[0]
    # direct.tokens = dup 的 output 之和 = 120 + 90
    assert a.direct.tokens == 120 + 90
    assert a.direct.baseline_step_ids == [(1, 1)]  # (turn, step) 复合
    assert a.direct.candidate_step_ids == [(1, 2), (1, 3)]
    # unattributed = dup 的 input 之和(历史 context,不强行归因)
    assert a.unattributed_tokens == 5000 + 6000


def test_propagated_only_output_of_next_step():
    # read x2 + 后续 step:propagated 只算后续 step 的 output
    trace = _make_trace(
        [
            ("read_file", '{"path":"a.py"}', False, 4000, 100),
            ("read_file", '{"path":"a.py"}', False, 5000, 120),
            ("write_file", '{"path":"b.py"}', False, 6000, 200),  # 后续 step
        ]
    )
    findings = DETECTOR.detect(trace)
    atts = ENGINE.attribute(trace, findings)
    a = atts[0]
    assert a.propagated.step_ids == [(1, 3)]  # (turn, step) 复合
    assert a.propagated.tokens == 200  # 只算 output,不算 input(6000 历史 context)


def test_total_candidate_tokens():
    trace = _make_trace(
        [
            ("read_file", '{"path":"a.py"}', False, 4000, 100),
            ("read_file", '{"path":"a.py"}', False, 5000, 120),
            ("write_file", '{"path":"b.py"}', False, 6000, 200),
        ]
    )
    findings = DETECTOR.detect(trace)
    atts = ENGINE.attribute(trace, findings)
    a = atts[0]
    assert a.total_tokens == (120 + 200)  # direct 120 + propagated 200
    assert a.kind == "cost"


def test_stateless_confidence_downgraded():
    trace = _make_trace(
        [
            ("get_current_time", "{}", False, 1000, 10),
            ("get_current_time", "{}", False, 2000, 12),
        ]
    )
    findings = DETECTOR.detect(trace)
    atts = ENGINE.attribute(trace, findings)
    a = atts[0]
    # stateless:confidence 降低,不直接认定浪费
    assert a.confidence < 0.95
    assert a.confidence == 0.60
    # 但仍给出候选(direct tokens = dup output)
    assert a.direct.tokens == 12


def test_deterministic_high_confidence():
    trace = _make_trace(
        [
            ("query_db", '{"sql":"SELECT 1"}', False, 1000, 10),
            ("query_db", '{"sql":"SELECT 1"}', False, 2000, 12),
        ]
    )
    findings = DETECTOR.detect(trace)
    atts = ENGINE.attribute(trace, findings)
    assert atts[0].confidence == 0.95


def test_evidence_chain_rendered():
    trace = _make_trace(
        [
            ("read_file", '{"path":"a.py"}', False, 4000, 100),
            ("read_file", '{"path":"a.py"}', False, 5000, 120),
        ]
    )
    findings = DETECTOR.detect(trace)
    ENGINE.attribute(trace, findings)
    chain = findings[0].details["evidence_chain"]
    rendered = chain.render()
    assert "occurrence #1/2" in rendered
    assert "occurrence #2/2" in rendered
    assert "finding" in rendered


def test_full_pipeline_report():
    trace = _make_trace(
        [
            ("read_file", '{"path":"config.yaml"}', False, 4000, 100),
            ("read_file", '{"path":"config.yaml"}', False, 5000, 120),
            ("read_file", '{"path":"config.yaml"}', False, 6000, 90),
            ("write_file", '{"path":"out.txt"}', False, 7000, 50),
        ]
    )
    findings = DETECTOR.detect(trace)
    atts = ENGINE.attribute(trace, findings)
    report = render_report(trace, findings, atts)
    assert "TOOL-001" in report
    assert "证据链" in report
    assert "候选可避免" in report or "candidate" in report.lower()
    # 报告应含归因行
    assert "direct=" in report


def test_cross_turn_same_step_id_regression():
    """回归测试:跨 turn 相同 step_id 不能互相覆盖(会话"1" E2E 发现的真实 bug)。

    构造两个 turn,每个 turn 都有 step_id=1,且重复调用跨 turn 出现。
    验证 attribution 用 (turn, step) 复合 key 正确取到 token。
    """
    t = Trace(session_id="cross-turn")
    # turn 1:step 1 是 read_file(第一次,output=100)
    turn1 = Turn(turn_id=1)
    s1 = Step(step_id=1, turn_id=1)
    s1.usage = Usage(input_tokens=4000, output_tokens=100)
    s1.tool_calls.append(ToolCall(call_id="c1", tool_name="read_file", arguments='{"path":"a.py"}'))
    turn1.steps.append(s1)

    # turn 2:step 1 是 read_file(第二次,output=999,若 step_id 覆盖则错误取到 turn1 的 100)
    turn2 = Turn(turn_id=2)
    s2 = Step(step_id=1, turn_id=2)
    s2.usage = Usage(input_tokens=5000, output_tokens=999)
    s2.tool_calls.append(ToolCall(call_id="c2", tool_name="read_file", arguments='{"path":"a.py"}'))
    turn2.steps.append(s2)

    t.turns = [turn1, turn2]

    findings = DETECTOR.detect(t)
    assert len(findings) == 1  # 跨 turn 相同 (name,args) 应检出重复

    atts = ENGINE.attribute(t, findings)
    a = atts[0]
    # duplicate 是 turn2 step1,其 output=999(不是 turn1 的 100)
    assert a.direct.tokens == 999, f"应取 turn2 step1 的 output=999,实际={a.direct.tokens}"
    # candidate 是 (turn2, step1)
    assert a.direct.candidate_step_ids == [(2, 1)]
    assert a.direct.baseline_step_ids == [(1, 1)]
