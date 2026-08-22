"""B1 验证集:固定代表性真实会话 + Canonical Trace 序列化(用于 fixture 固化)。

- `AB_VALIDATION_SESSIONS`:固定验证集会话(含 session_id + expected 下界)。
- `_trace_to_dict` / `_trace_from_dict`:本地序列化/反序列化(因 canonical_trace.py
  无 to_dict 且本项目不改它是铁律);用于导出 `tests/fixtures/b1_validation_sessions/`
  的 JSON fixture,并供测试加载(跨机可复现)。

来源:openspec/changes/b1-ab-validation/evidence.md(109 会话实测)。
"""

from __future__ import annotations

from ..core.canonical_trace import Step, ToolCall, Trace, TraceEvent, Turn, Usage

# 固定验证集(design D6)。expected_*_min 为下界。
# ⚠️ 偏离(事实对齐):D6 原始下界基于"未隔离语义"的证据(含轮询型工具的重复都计入硬可省),
# 但按 D2 的 SEMANTIC_DEBATED_TOOLS 语义隔离,轮询型工具(如 list_sessions/list_agents/memory_list)
# 不计入硬可省。因此这里按语义隔离口径的实测值重设下界(证据 evidence.md §4 未隔离,故数值不同)。
AB_VALIDATION_SESSIONS: list[dict] = [
    {
        "session_id": "session-a79579f3-f897-4a2c-aae7-e3910a206186",
        "label": "高密度 TOOL-001 + TOOL-004 共现",
        "expected_tool001_min": 18,
        "expected_tool004_min": 1,
        "expected_tool_call_reduction_min": 20,
        "note": "27 TOOL-001(20 确定性 + 7 debated)+ 1 TOOL-004;代表性长会话;语义隔离",
    },
    {
        "session_id": "session-1491c2c7-3cf8-4405-97cf-6c70159660f5",
        "label": "高倍率 TOOL-001(N=11 memory_list),无 TOOL-004",
        "expected_tool001_min": 14,
        "expected_tool004_min": 0,
        "expected_tool_call_reduction_min": 20,
        "note": "18 TOOL-001(16 确定性 + 2 debated);N=11 的 memory_list 高倍率组为 debated;语义隔离",
    },
    {
        "session_id": "session-112ce518-4d26-4e86-8a54-69c98175c2dd",
        "label": "低密度小会话(TOOL-001=1 debated + TOOL-004=1)",
        "expected_tool001_min": 0,
        "expected_tool004_min": 1,
        "expected_tool_call_reduction_min": 1,
        "note": "TOOL-001 仅 list_sessions(debated,不入硬可省)+ TOOL-004=1;锚点会话;语义隔离",
    },
    {
        "session_id": "session-4ee09ecf-7629-4067-a058-dcfef827ccb3",
        "label": "高密度 TOOL-001(19 finding)+ TOOL-004 共现",
        "expected_tool001_min": 11,
        "expected_tool004_min": 1,
        "expected_tool_call_reduction_min": 14,
        "note": "19 TOOL-001(13 确定性 + 6 debated)+ 1 TOOL-004;send_session_message 失败;语义隔离",
    },
    {
        "session_id": "session-5cdccd44-fb56-4d7e-ba82-adc2eaa40d0f",
        "label": "高密度 TOOL-001(19 finding)+ TOOL-004 共现",
        "expected_tool001_min": 11,
        "expected_tool004_min": 1,
        "expected_tool_call_reduction_min": 14,
        "note": "19 TOOL-001(13 确定性 + 6 debated)+ 1 TOOL-004;list_agents 高倍率(debated);语义隔离",
    },
]


def _trace_to_dict(trace: Trace) -> dict:
    """把 Canonical Trace 序列化为 JSON 可序列化 dict(本地实现,不改 canonical_trace.py)。"""
    return {
        "session_id": trace.session_id,
        "model": trace.model,
        "metadata": trace.metadata,
        "turns": [
            {
                "turn_id": t.turn_id,
                "start_time": t.start_time,
                "end_time": t.end_time,
                "steps": [
                    {
                        "step_id": s.step_id,
                        "turn_id": s.turn_id,
                        "start_time": s.start_time,
                        "end_time": s.end_time,
                        "usage": {
                            "input_tokens": s.usage.input_tokens,
                            "output_tokens": s.usage.output_tokens,
                            "cache_read_tokens": s.usage.cache_read_tokens,
                            "cache_write_tokens": s.usage.cache_write_tokens,
                            "reasoning_tokens": s.usage.reasoning_tokens,
                        },
                        "tool_calls": [
                            {
                                "call_id": tc.call_id,
                                "tool_name": tc.tool_name,
                                "arguments": tc.arguments,
                                "result": tc.result,
                                "is_error": tc.is_error,
                                "truncated": tc.truncated,
                                "fingerprint": tc.fingerprint,
                            }
                            for tc in s.tool_calls
                        ],
                        "reasoning": s.reasoning,
                        "text": s.text,
                    }
                    for s in t.steps
                ],
            }
            for t in trace.turns
        ],
        "events": [
            {
                "type": e.type,
                "time": e.time,
                "seq": e.seq,
                "turn_id": e.turn_id,
                "step_id": e.step_id,
                "data": e.data,
            }
            for e in trace.events
        ],
    }


def _trace_from_dict(data: dict) -> Trace:
    """从 fixture dict 反序列化为 Canonical Trace(与 _trace_to_dict 对称)。"""
    t = Trace(session_id=data.get("session_id", ""), model=data.get("model", ""))
    t.metadata = data.get("metadata", {}) or {}
    turns: list[Turn] = []
    for td in data.get("turns", []):
        turn = Turn(turn_id=td["turn_id"], start_time=td.get("start_time", 0), end_time=td.get("end_time", 0))
        for sd in td.get("steps", []):
            st = Step(
                step_id=sd["step_id"],
                turn_id=sd["turn_id"],
                start_time=sd.get("start_time", 0),
                end_time=sd.get("end_time", 0),
                reasoning=sd.get("reasoning", ""),
                text=sd.get("text", ""),
            )
            u = sd.get("usage", {})
            st.usage = Usage(
                input_tokens=u.get("input_tokens", 0),
                output_tokens=u.get("output_tokens", 0),
                cache_read_tokens=u.get("cache_read_tokens", 0),
                cache_write_tokens=u.get("cache_write_tokens"),
                reasoning_tokens=u.get("reasoning_tokens"),
            )
            for tcd in sd.get("tool_calls", []):
                st.tool_calls.append(
                    ToolCall(
                        call_id=tcd.get("call_id", ""),
                        tool_name=tcd.get("tool_name", ""),
                        arguments=tcd.get("arguments", ""),
                        result=tcd.get("result", ""),
                        is_error=tcd.get("is_error", False),
                        truncated=tcd.get("truncated", False),
                        fingerprint=tcd.get("fingerprint"),
                    )
                )
            turn.steps.append(st)
        turns.append(turn)
    t.turns = turns
    for ed in data.get("events", []):
        t.events.append(
            TraceEvent(
                type=ed["type"],
                time=ed.get("time", 0),
                seq=ed.get("seq", 0),
                turn_id=ed.get("turn_id"),
                step_id=ed.get("step_id"),
                data=ed.get("data", {}) or {},
            )
        )
    return t
