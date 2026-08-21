"""TOOL-001 Golden Trace 测试样本定义。

每个样本:一个构造的 Trace + 期望的检测结果(机器可验证)。
用于 pytest 断言 Precision/Recall。

场景覆盖:
- T1  简单重复 A(x) A(x)                          → 检出 TOOL-001, 2 次
- T2  交错 A(x) B(y) A(x)                          → 检出 TOOL-001, 2 次
- T3  不同参数 A(x) A(y)                            → 不检出(不同 fingerprint)
- T4  参数 key 顺序 A({"x":1,"y":2}) A({"y":2,"x":1}) → 检出(归一化后相同)
- T5  四连调 A(x) x4                               → 检出, 4 次, high severity
- T6  无状态工具 get_current_time() x2               → 候选(低置信度,stateless 标记)
- T7  无重复 B(x) C(y)                             → 不检出
- T8  带失败重试 read(f) err read(f)                → 检出 TOOL-001(2 次),is_error 标记
"""

from __future__ import annotations

from agenttrace.core.canonical_trace import Step, ToolCall, Trace, Turn, Usage

ALL_STATELESS = {"get_current_time", "get_time", "get_random"}


def _step(turn_id, step_id, tools, input_tokens=1000, output_tokens=100, reasoning=""):
    st = Step(step_id=step_id, turn_id=turn_id)
    st.usage = Usage(input_tokens=input_tokens, output_tokens=output_tokens)
    st.reasoning = reasoning
    for i, (name, args, is_err) in enumerate(tools):
        st.tool_calls.append(
            ToolCall(
                call_id=f"call_{turn_id}_{step_id}_{i}",
                tool_name=name,
                arguments=args,
                result="err" if is_err else "ok",
                is_error=is_err,
            )
        )
    return st


def t1_duplicate_simple():
    t = Trace(session_id="t1")
    t.turns = [Turn(turn_id=1, steps=[
        _step(1, 1, [("read_file", '{"path":"a.py"}', False)]),
        _step(1, 2, [("read_file", '{"path":"a.py"}', False)]),
    ])]
    return t, {"rule_id": "TOOL-001", "occurrences": 2, "n_findings": 1, "confidence_gt": 0.9}


def t2_duplicate_interleaved():
    t = Trace(session_id="t2")
    t.turns = [Turn(turn_id=1, steps=[
        _step(1, 1, [("read_file", '{"path":"a.py"}', False)]),
        _step(1, 2, [("write_file", '{"path":"b.py"}', False)]),
        _step(1, 3, [("read_file", '{"path":"a.py"}', False)]),
    ])]
    return t, {"rule_id": "TOOL-001", "occurrences": 2, "n_findings": 1, "confidence_gt": 0.9}


def t3_different_args():
    t = Trace(session_id="t3")
    t.turns = [Turn(turn_id=1, steps=[
        _step(1, 1, [("read_file", '{"path":"a.py"}', False)]),
        _step(1, 2, [("read_file", '{"path":"b.py"}', False)]),
    ])]
    return t, {"rule_id": "TOOL-001", "occurrences": 0, "n_findings": 0}


def t4_arg_key_order():
    t = Trace(session_id="t4")
    t.turns = [Turn(turn_id=1, steps=[
        _step(1, 1, [("api_call", '{"x":1,"y":2}', False)]),
        _step(1, 2, [("api_call", '{"y":2,"x":1}', False)]),
    ])]
    return t, {"rule_id": "TOOL-001", "occurrences": 2, "n_findings": 1, "confidence_gt": 0.9}


def t5_quadruple():
    t = Trace(session_id="t5")
    t.turns = [Turn(turn_id=1, steps=[
        _step(1, i, [("query_db", '{"sql":"SELECT * FROM t"}', False)]) for i in range(1, 5)
    ])]
    return t, {"rule_id": "TOOL-001", "occurrences": 4, "n_findings": 1, "severity": "high", "confidence_gt": 0.9}


def t6_stateless_boundary():
    t = Trace(session_id="t6")
    t.turns = [Turn(turn_id=1, steps=[
        _step(1, 1, [("get_current_time", "{}", False)]),
        _step(1, 2, [("get_current_time", "{}", False)]),
    ])]
    # 无状态工具:仍输出候选(低置信度),但 stateless 标记
    return t, {"rule_id": "TOOL-001", "occurrences": 2, "n_findings": 1, "confidence_lt": 0.8, "stateless": True}


def t7_no_duplicate():
    t = Trace(session_id="t7")
    t.turns = [Turn(turn_id=1, steps=[
        _step(1, 1, [("build", "{}", False)]),
        _step(1, 2, [("test", "{}", False)]),
    ])]
    return t, {"rule_id": "TOOL-001", "occurrences": 0, "n_findings": 0}


def t8_error_retry():
    t = Trace(session_id="t8")
    t.turns = [Turn(turn_id=1, steps=[
        _step(1, 1, [("read_file", '{"path":"settings.yaml"}', True)]),
        _step(1, 2, [("read_file", '{"path":"settings.yaml"}', False)]),
    ])]
    return t, {"rule_id": "TOOL-001", "occurrences": 2, "n_findings": 1, "has_error": True, "confidence_gt": 0.9}


ALL_GOLDEN = {
    "t1_duplicate_simple": t1_duplicate_simple,
    "t2_duplicate_interleaved": t2_duplicate_interleaved,
    "t3_different_args": t3_different_args,
    "t4_arg_key_order": t4_arg_key_order,
    "t5_quadruple": t5_quadruple,
    "t6_stateless_boundary": t6_stateless_boundary,
    "t7_no_duplicate": t7_no_duplicate,
    "t8_error_retry": t8_error_retry,
}
