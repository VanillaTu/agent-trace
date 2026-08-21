"""v0.5 golden 基线报告 trace 构造器。

用于"默认路径逐字节对比"测试的锚点 trace:
- 覆盖全部 5 类 detector(TOOL-001 / CMP-001 / THINK-001 / RETRY-001 / SUB-001)
- 完全确定性(固定 session_id / usage / events)
- 同一构造器既用于生成 golden 基线文件,也用于回归测试重放
"""

from __future__ import annotations

from agenttrace.core.canonical_trace import (
    Step,
    ToolCall,
    Trace,
    TraceEvent,
    Turn,
    Usage,
)


def build_comprehensive_trace() -> Trace:
    """构造覆盖 5 类 detector 的确定性 trace。"""
    t = Trace(session_id="golden-baseline")
    turn = Turn(turn_id=1)

    # step 1: read_file(dup #1, baseline)+ 高 reasoning(> P99 → THINK-001)
    s1 = Step(step_id=1, turn_id=1)
    s1.usage = Usage(input_tokens=1000, output_tokens=300, reasoning_tokens=4000)
    s1.tool_calls.append(
        ToolCall(call_id="c1", tool_name="read_file", arguments='{"path":"a.py"}')
    )
    turn.steps.append(s1)

    # step 2: read_file(dup #2)+ 中 reasoning
    s2 = Step(step_id=2, turn_id=1)
    s2.usage = Usage(input_tokens=2000, output_tokens=120, reasoning_tokens=50)
    s2.tool_calls.append(
        ToolCall(call_id="c2", tool_name="read_file", arguments='{"path":"a.py"}')
    )
    turn.steps.append(s2)

    t.turns = [turn]

    # CMP-001(observation)
    t.events.append(
        TraceEvent(type="compaction/prune", data={"shadowedTokenCount": 8541})
    )
    # RETRY-001(reliability)
    t.events.append(
        TraceEvent(
            type="llm/finish/error", seq=1, turn_id=1, step_id=1,
            data={"error_code": "TRANSPORT"},
        )
    )
    t.events.append(
        TraceEvent(
            type="llm/retry", seq=2, turn_id=1, step_id=1,
            data={"retryId": "r1", "provider": "ollama", "mode": "normal"},
        )
    )
    t.events.append(
        TraceEvent(
            type="llm/retry-started", seq=3, turn_id=1, step_id=1,
            data={"retryId": "r1", "retry": 1},
        )
    )
    t.events.append(
        TraceEvent(type="llm/finish/success", seq=4, turn_id=1, step_id=1, data={})
    )
    # SUB-001(observation)
    t.events.append(
        TraceEvent(
            type="subagent/descriptor", seq=5, turn_id=1, step_id=1,
            data={"mode": "spawn", "provider": "dsh", "label": "worker"},
        )
    )
    return t
