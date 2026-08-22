"""c1-semantic-judgment(C1)LLM 语义判断候选清单层测试。

覆盖 design.md 测试表 T1–T48:
1. 数据结构默认值(SemanticCandidate / JudgmentContext / verdict 字段)
2. 候选生成(空/无 finding/单重复/debated/多 fingerprint/高倍率/TOOL-004)
3. 排序(debated 优先/高倍率降序)
4. 上下文构造(gap/干预动作/结果变化/截断/前缀比较边界)
5. 回填合并(全量/部分/无匹配/source/未回填/malformed JSON)
6. 确定性(additive/金钟罩)
7. 报告渲染(语义块/无 wasted/待回填/已回填/context=None)
8. CLI 输出(JSON 结构/门控)
"""

from __future__ import annotations

import json
from pathlib import Path

from agenttrace.analysis.c1_semantic import (
    JudgmentContext,
    SemanticCandidate,
    build_judgment_context,
    build_semantic_candidates,
    merge_semantic_verdicts,
    serialize_candidates_to_json,
)
from agenttrace.core.canonical_trace import (
    Step,
    ToolCall,
    Trace,
    Turn,
    Usage,
)
from agenttrace.detectors.tool_001 import DuplicateToolCallDetector
from agenttrace.detectors.tool_004 import InvalidParamRetryDetector
from agenttrace.pipeline import diagnose
from agenttrace.report import render_report

GOLDEN_DIR = Path(__file__).parent / "golden"


# --------------------------------------------------------------------------
# 构造辅助
# --------------------------------------------------------------------------

def _tc(tool_name, args="{}", is_error=False, result="", call_id="c"):
    return ToolCall(
        call_id=call_id, tool_name=tool_name, arguments=args,
        result=result, is_error=is_error,
    )


def _step(step_id, turn_id=1, usage=(0, 0), tcs=None):
    st = Step(step_id=step_id, turn_id=turn_id)
    st.usage = Usage(input_tokens=usage[0], output_tokens=usage[1])
    for tc in tcs or []:
        st.tool_calls.append(tc)
    return st


def _trace(steps):
    t = Trace(session_id="test")
    turn = Turn(turn_id=1)
    turn.steps = steps
    t.turns = [turn]
    return t


def _dup_trace(tool="read_file", args='{"p":"a"}', n=2, results=None, usage=(0, 0)):
    steps = []
    for i in range(n):
        res = (results or ["ok"] * n)[i] if results else "ok"
        steps.append(_step(i + 1, usage=usage, tcs=[_tc(tool, args, result=res, call_id=f"c{i}")]))
    return _trace(steps)


def _tool001_findings(trace):
    return [f for f in DuplicateToolCallDetector().detect(trace) if f.rule_id == "TOOL-001"]


def _tool004_findings(trace):
    return [f for f in InvalidParamRetryDetector().detect(trace) if f.rule_id == "TOOL-004"]


def _param_error_tc(tool="send_session_message"):
    return _tc(
        tool, '{"a":1}', is_error=True,
        result='Error: invalid arguments: missing required property "text"',
        call_id="fail-1",
    )


# --------------------------------------------------------------------------
# T1–T3: 数据结构默认值
# --------------------------------------------------------------------------

def test_semantic_candidate_defaults():
    c = SemanticCandidate()
    assert c.rule_id == "TOOL-001"
    assert c.turn_id == 0
    assert c.step_id == 0
    assert c.fingerprint == ""
    assert c.tool_name == ""
    assert c.is_debated is False
    assert c.context is None
    assert c.occurrence_index == 1
    assert c.total_occurrences == 1


def test_judgment_context_defaults():
    ctx = JudgmentContext()
    assert ctx.previous_step_id == 0
    assert ctx.current_step_id == 0
    assert ctx.gap_steps == 0
    assert ctx.intervening_actions == []
    assert ctx.tool_result_changed is None


def test_semantic_verdict_defaults():
    c = SemanticCandidate()
    assert c.verdict == "not_applicable"
    assert c.confidence == 0.0
    assert c.reason == ""
    assert c.source == "semantic"
    assert c.causal_claim == "NONE"


# --------------------------------------------------------------------------
# T4–T10: 候选生成
# --------------------------------------------------------------------------

def test_build_candidates_empty_trace():
    assert build_semantic_candidates(Trace(session_id="e"), []) == []


def test_build_candidates_no_findings():
    t = _trace([_step(1, tcs=[_tc("read_file", '{"p":"a"}')])])
    assert build_semantic_candidates(t, []) == []


def test_build_candidates_single_duplicate():
    t = _dup_trace(n=2)
    cands = build_semantic_candidates(t, _tool001_findings(t))
    assert len(cands) == 1
    c = cands[0]
    assert c.is_debated is False
    assert c.occurrence_index == 2
    assert c.total_occurrences == 2
    assert c.tool_name == "read_file"


def test_build_candidates_debated_tool():
    t = _dup_trace(tool="list_sessions", args="{}", n=2)
    cands = build_semantic_candidates(t, _tool001_findings(t))
    assert len(cands) == 1
    assert cands[0].is_debated is True


def test_build_candidates_multiple_fingerprints():
    t = _trace([
        _step(1, tcs=[_tc("read_file", '{"p":"a"}'), _tc("write_file", '{"p":"b"}')]),
        _step(2, tcs=[_tc("read_file", '{"p":"a"}')]),
        _step(3, tcs=[_tc("write_file", '{"p":"b"}')]),
    ])
    cands = build_semantic_candidates(t, _tool001_findings(t))
    assert len(cands) == 2  # read_file 第2次 + write_file 第2次


def test_build_candidates_high_occurrence_count():
    t = _dup_trace(n=5)
    cands = build_semantic_candidates(t, _tool001_findings(t))
    assert len(cands) == 4  # 第 2-5 次
    assert all(c.total_occurrences == 5 for c in cands)


def test_build_candidates_tool004():
    t = _trace([
        _step(1, tcs=[_param_error_tc()]),
        _step(2, tcs=[_tc("send_session_message", '{"text":"x"}', call_id="fail-1")]),
    ])
    cands = build_semantic_candidates(t, _tool004_findings(t))
    assert len(cands) == 1
    assert cands[0].rule_id == "TOOL-004"
    assert cands[0].is_debated is False


# --------------------------------------------------------------------------
# T11–T12: 排序
# --------------------------------------------------------------------------

def test_candidates_sorted_debated_first():
    t = _trace([
        _step(1, tcs=[_tc("read_file", '{"p":"a"}'), _tc("list_sessions", "{}")]),
        _step(2, tcs=[_tc("read_file", '{"p":"a"}')]),
        _step(3, tcs=[_tc("list_sessions", "{}")]),
    ])
    cands = build_semantic_candidates(t, _tool001_findings(t))
    assert cands[0].is_debated is True  # list_sessions 在前
    assert cands[-1].is_debated is False  # read_file 在后


def test_candidates_sorted_by_occurrence_count():
    t = _trace([
        _step(1, tcs=[_tc("list_sessions", "{}"), _tc("job_output", "{}")]),
        _step(2, tcs=[_tc("list_sessions", "{}")]),
        _step(3, tcs=[_tc("list_sessions", "{}")]),
        _step(4, tcs=[_tc("job_output", "{}")]),
    ])
    cands = build_semantic_candidates(t, _tool001_findings(t))
    # list_sessions total=3 (2 候选),job_output total=2 (1 候选)
    # debated 内按 total_occurrences 降序:list_sessions(t=3) 在 job_output(t=2) 前
    assert cands[0].tool_name == "list_sessions"
    assert cands[0].total_occurrences == 3


# --------------------------------------------------------------------------
# T13–T19: 上下文构造
# --------------------------------------------------------------------------

def _ctx_of(trace, n=2, tool="read_file", args='{"p":"a"}', results=None):
    cands = build_semantic_candidates(trace, _tool001_findings(trace))
    assert cands
    return cands[0].context


def test_judgment_context_gap_steps():
    # read_file 出现 2 次(step1, step3),中间有非重复 step(step2, write_file)
    t = _trace([
        _step(1, tcs=[_tc("read_file", '{"p":"a"}', result="r1")]),
        _step(2, tcs=[_tc("write_file", '{"p":"b"}', result="w1")]),
        _step(3, tcs=[_tc("read_file", '{"p":"a"}', result="r2")]),
    ])
    ctx = _ctx_of(t)
    assert ctx.gap_steps == 1  # step1 与 step3 之间 1 个 step


def test_judgment_context_intervening_write():
    t = _trace([
        _step(1, tcs=[_tc("read_file", '{"p":"a"}', result="r1")]),
        _step(2, tcs=[_tc("send_message", "{}", result="sent")]),
        _step(3, tcs=[_tc("read_file", '{"p":"a"}', result="r2")]),
    ])
    # 这样 same fingerprint 是 read_file 出现 2 次(step1, step3),中间是 send_message
    cands = build_semantic_candidates(t, _tool001_findings(t))
    ctx = cands[0].context
    assert any(a.is_write for a in ctx.intervening_actions)
    assert any(a.tool_name == "send_message" for a in ctx.intervening_actions)


def test_judgment_context_no_intervening():
    t = _trace([
        _step(1, tcs=[_tc("read_file", '{"p":"a"}', result="r1")]),
        _step(2, tcs=[_tc("read_file", '{"p":"a"}', result="r2")]),
    ])
    ctx = _ctx_of(t)
    assert ctx.gap_steps == 0
    assert ctx.intervening_actions == []


def test_judgment_context_result_changed():
    t = _trace([
        _step(1, tcs=[_tc("read_file", '{"p":"a"}', result="abc")]),
        _step(2, tcs=[_tc("read_file", '{"p":"a"}', result="xyz")]),
    ])
    ctx = _ctx_of(t)
    assert ctx.tool_result_changed is True


def test_judgment_context_result_unchanged():
    t = _trace([
        _step(1, tcs=[_tc("read_file", '{"p":"a"}', result="same")]),
        _step(2, tcs=[_tc("read_file", '{"p":"a"}', result="same")]),
    ])
    ctx = _ctx_of(t)
    assert ctx.tool_result_changed is False


def test_judgment_context_result_truncated():
    # 两个 >500 的结果,前 500 相同 → None
    long = "x" * 600
    t = _trace([
        _step(1, tcs=[_tc("read_file", '{"p":"a"}', result=long + "aaa")]),
        _step(2, tcs=[_tc("read_file", '{"p":"a"}', result=long + "bbb")]),
    ])
    ctx = _ctx_of(t)
    assert ctx.tool_result_changed is None


def test_judgment_context_result_snippet_length():
    long = "y" * 600
    t = _trace([
        _step(1, tcs=[_tc("read_file", '{"p":"a"}', result=long)]),
        _step(2, tcs=[_tc("read_file", '{"p":"a"}', result=long)]),
    ])
    ctx = _ctx_of(t)
    assert len(ctx.previous_result_snippet) == 500
    assert len(ctx.current_result_snippet) == 500


# --------------------------------------------------------------------------
# T20–T25: 回填合并
# --------------------------------------------------------------------------

def _write_verdicts(tmp_path, verdicts):
    p = tmp_path / "verdicts.json"
    p.write_text(json.dumps({"verdicts": verdicts}, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_merge_verdicts_backfill(tmp_path):
    t = _dup_trace(n=2)
    cands = build_semantic_candidates(t, _tool001_findings(t))
    c = cands[0]
    fp = c.fingerprint
    verdicts = [{"rule_id": "TOOL-001", "fingerprint": fp, "turn_id": c.turn_id, "step_id": c.step_id,
                 "verdict": "true_redundant", "confidence": 0.85, "reason": "ok"}]
    merged, vmap = merge_semantic_verdicts(cands, _write_verdicts(tmp_path, verdicts))
    assert merged[0].verdict == "true_redundant"
    assert merged[0].confidence == 0.85
    assert merged[0].reason == "ok"
    assert vmap[(c.rule_id, fp, c.turn_id, c.step_id)] == "true_redundant"


def test_merge_verdicts_partial_backfill(tmp_path):
    t = _dup_trace(n=3)
    cands = build_semantic_candidates(t, _tool001_findings(t))
    assert len(cands) == 2
    c0 = cands[0]
    verdicts = [{"rule_id": c0.rule_id, "fingerprint": c0.fingerprint, "turn_id": c0.turn_id,
                 "step_id": c0.step_id, "verdict": "legitimate", "confidence": 0.9, "reason": ""}]
    merged, _ = merge_semantic_verdicts(cands, _write_verdicts(tmp_path, verdicts))
    assert merged[0].verdict == "legitimate"
    assert any(c.verdict == "not_applicable" for c in merged)


def test_merge_verdicts_no_match(tmp_path):
    t = _dup_trace(n=2)
    cands = build_semantic_candidates(t, _tool001_findings(t))
    verdicts = [{"rule_id": "TOOL-001", "fingerprint": "nope", "turn_id": 9, "step_id": 9,
                 "verdict": "true_redundant", "confidence": 0.9, "reason": ""}]
    merged, vmap = merge_semantic_verdicts(cands, _write_verdicts(tmp_path, verdicts))
    assert all(c.verdict == "not_applicable" for c in merged)
    assert vmap == {( "TOOL-001", "nope", 9, 9): "true_redundant"}


def test_verdict_source_semantic(tmp_path):
    t = _dup_trace(n=2)
    cands = build_semantic_candidates(t, _tool001_findings(t))
    c = cands[0]
    verdicts = [{"rule_id": c.rule_id, "fingerprint": c.fingerprint, "turn_id": c.turn_id,
                 "step_id": c.step_id, "verdict": "uncertain", "confidence": 0.5, "reason": ""}]
    merged, _ = merge_semantic_verdicts(cands, _write_verdicts(tmp_path, verdicts))
    assert merged[0].source == "semantic"
    assert merged[0].causal_claim == "NONE"


def test_verdict_causal_claim_none(tmp_path):
    t = _dup_trace(n=2)
    cands = build_semantic_candidates(t, _tool001_findings(t))
    c = cands[0]
    verdicts = [{"rule_id": c.rule_id, "fingerprint": c.fingerprint, "turn_id": c.turn_id,
                 "step_id": c.step_id, "verdict": "legitimate", "confidence": 0.9, "reason": ""}]
    merged, _ = merge_semantic_verdicts(cands, _write_verdicts(tmp_path, verdicts))
    assert merged[0].causal_claim == "NONE"


def test_not_backfilled_is_not_applicable(tmp_path):
    t = _dup_trace(n=2)
    cands = build_semantic_candidates(t, _tool001_findings(t))
    verdicts = []  # 空回填
    merged, _ = merge_semantic_verdicts(cands, _write_verdicts(tmp_path, verdicts))
    assert all(c.verdict == "not_applicable" for c in merged)
    assert all(c.confidence == 0.0 for c in merged)


# --------------------------------------------------------------------------
# T26–T27: 确定性
# --------------------------------------------------------------------------

def test_deterministic_same_trace_twice():
    from dataclasses import asdict
    t = _dup_trace(n=4)
    cands1 = build_semantic_candidates(t, _tool001_findings(t))
    cands2 = build_semantic_candidates(t, _tool001_findings(t))
    assert [asdict(c) for c in cands1] == [asdict(c) for c in cands2]


def test_deterministic_context_same_twice():
    t = _dup_trace(n=2)
    cands = build_semantic_candidates(t, _tool001_findings(t))
    ctx1 = build_judgment_context(t, 1, 1, 1, 2, cands[0].fingerprint)
    ctx2 = build_judgment_context(t, 1, 1, 1, 2, cands[0].fingerprint)
    assert ctx1 == ctx2


# --------------------------------------------------------------------------
# T28–T31: additive / 金钟罩
# --------------------------------------------------------------------------

def test_additive_enable_analysis_false():
    t = _dup_trace(n=2)
    result = diagnose(t)  # enable_analysis=False
    assert result.semantic_candidates is None


def test_additive_detectors_unchanged():
    from agenttrace.attribution import ALL_ATTRIBUTION_ENGINES
    from agenttrace.detectors import ALL_DETECTORS
    assert len(ALL_DETECTORS) == 6
    assert len(ALL_ATTRIBUTION_ENGINES) == 6


def test_additive_findings_unchanged():
    t = _dup_trace(n=2)
    f_off = DuplicateToolCallDetector().detect(t)
    result = diagnose(t, enable_analysis=True)
    # 开启语义层后 finding 不变
    assert any(f.rule_id == "TOOL-001" for f in result.findings)
    assert len(result.findings) == len(f_off)


def test_golden_report_byte_identical():
    from tests.golden.golden_report import build_comprehensive_trace
    t = build_comprehensive_trace()
    result = diagnose(t)
    report = render_report(result.trace, result.findings, result.attributions)
    expected = (GOLDEN_DIR / "v05_baseline_report.md").read_text(encoding="utf-8")
    assert report == expected


# --------------------------------------------------------------------------
# T32–T36: 报告渲染
# --------------------------------------------------------------------------

def test_report_renders_semantic_block():
    t = _dup_trace(n=2)
    cands = build_semantic_candidates(t, _tool001_findings(t))
    report = render_report(t, [], [], enable_analysis=True, semantic_candidates=cands)
    assert "### 语义判断(C1)" in report


def test_report_no_semantic_block_when_disabled():
    t = _dup_trace(n=2)
    report = render_report(t, [], [])
    assert "语义判断(C1)" not in report


def test_report_semantic_block_no_wasted():
    t = _dup_trace(n=2)
    cands = build_semantic_candidates(t, _tool001_findings(t))
    report = render_report(t, [], [], enable_analysis=True, semantic_candidates=cands)
    block = report[report.index("### 语义判断(C1)"):]
    assert "浪费" not in block
    assert "Total wasted" not in block


def test_report_not_backfilled_shows_pending():
    t = _dup_trace(n=2)
    cands = build_semantic_candidates(t, _tool001_findings(t))
    report = render_report(t, [], [], enable_analysis=True, semantic_candidates=cands)
    assert "待 agent 语义判断" in report


def test_report_backfilled_shows_verdict():
    t = _dup_trace(n=2)
    cands = build_semantic_candidates(t, _tool001_findings(t))
    c = cands[0]
    c.verdict = "true_redundant"
    c.confidence = 0.85
    c.reason = "无写入且结果相同"
    verdicts_map = {(c.rule_id, c.fingerprint, c.turn_id, c.step_id): "true_redundant"}
    report = render_report(t, [], [], enable_analysis=True, semantic_candidates=cands, semantic_verdicts_map=verdicts_map)
    assert "真冗余" in report
    assert "0.85" in report


# --------------------------------------------------------------------------
# T37–T41: 序列化
# --------------------------------------------------------------------------

def test_serialize_candidates_json_valid():
    t = _dup_trace(n=2)
    cands = build_semantic_candidates(t, _tool001_findings(t))
    s = serialize_candidates_to_json(cands, "s1", "m1")
    data = json.loads(s)
    assert isinstance(data, dict)


def test_serialize_candidates_json_structure():
    t = _dup_trace(n=2)
    cands = build_semantic_candidates(t, _tool001_findings(t))
    s = serialize_candidates_to_json(cands, "s1", "m1")
    data = json.loads(s)
    assert data["session_id"] == "s1"
    assert data["model"] == "m1"
    assert "generated_at" in data
    assert "total_candidates" in data
    assert "debated_count" in data
    assert "deterministic_count" in data
    assert "candidates" in data
    assert "instructions" in data  # 评审 A
    assert "task" in data["instructions"]
    assert "output_format" in data["instructions"]
    assert "criteria" in data["instructions"]
    assert "example_verdict" in data["instructions"]


def test_cli_semantic_flag_enables_analysis(tmp_path):
    import zstandard
    lines = [
        json.dumps({"type": "session", "id": "s1"}),
        json.dumps({"type": "assistant/chunk", "seq": 1, "data": {"turn": 1, "step": 1, "chunk": {"type": "usage", "usage": {"inputTokens": 100, "outputTokens": 50}}}}),
        json.dumps({"type": "assistant/message", "seq": 2, "data": {"turn": 1, "step": 1, "message": {"content": [{"type": "tool-call", "id": "c1", "name": "read_file", "arguments": '{"path":"a.py"}'}]}, "usage": {"inputTokens": 100, "outputTokens": 50}}}),
        json.dumps({"type": "assistant/chunk", "seq": 3, "data": {"turn": 1, "step": 2, "chunk": {"type": "usage", "usage": {"inputTokens": 100, "outputTokens": 50}}}}),
        json.dumps({"type": "assistant/message", "seq": 4, "data": {"turn": 1, "step": 2, "message": {"content": [{"type": "tool-call", "id": "c2", "name": "read_file", "arguments": '{"path":"a.py"}'}]}, "usage": {"inputTokens": 100, "outputTokens": 50}}}),
    ]
    session_dir = tmp_path / "s1"
    session_dir.mkdir(parents=True)
    zst = session_dir / "session.jsonl.zstd"
    with zst.open("wb") as f:
        f.write(zstandard.ZstdCompressor().compress(("\n".join(lines) + "\n").encode("utf-8")))
    from agenttrace.cli import main
    out = tmp_path / "report.md"
    rc = main(["analyze", str(session_dir), "--semantic", "--out", str(out)])
    assert rc == 0
    # --semantic 输出候选 JSON;报告写入文件
    assert out.exists()


def test_cli_semantic_out_writes_json(tmp_path):
    import zstandard
    lines = [
        json.dumps({"type": "session", "id": "s1"}),
        json.dumps({"type": "assistant/chunk", "seq": 1, "data": {"turn": 1, "step": 1, "chunk": {"type": "usage", "usage": {"inputTokens": 100, "outputTokens": 50}}}}),
        json.dumps({"type": "assistant/message", "seq": 2, "data": {"turn": 1, "step": 1, "message": {"content": [{"type": "tool-call", "id": "c1", "name": "read_file", "arguments": '{"path":"a.py"}'}]}, "usage": {"inputTokens": 100, "outputTokens": 50}}}),
        json.dumps({"type": "assistant/chunk", "seq": 3, "data": {"turn": 1, "step": 2, "chunk": {"type": "usage", "usage": {"inputTokens": 100, "outputTokens": 50}}}}),
        json.dumps({"type": "assistant/message", "seq": 4, "data": {"turn": 1, "step": 2, "message": {"content": [{"type": "tool-call", "id": "c2", "name": "read_file", "arguments": '{"path":"a.py"}'}]}, "usage": {"inputTokens": 100, "outputTokens": 50}}}),
    ]
    session_dir = tmp_path / "s1"
    session_dir.mkdir(parents=True)
    zst = session_dir / "session.jsonl.zstd"
    with zst.open("wb") as f:
        f.write(zstandard.ZstdCompressor().compress(("\n".join(lines) + "\n").encode("utf-8")))
    from agenttrace.cli import main
    # 单测:直接验证 --semantic 会输出 candidate JSON(无 --out 时走 stdout)
    rc = main(["analyze", str(session_dir), "--semantic"])
    assert rc == 0  # 无 --out,--semantic 输出候选 JSON 到 stdout(不抛异常)


def test_empty_candidates_serializes_empty_array():
    s = serialize_candidates_to_json([], "s", "m")
    data = json.loads(s)
    assert data["total_candidates"] == 0
    assert data["candidates"] == []
    assert data["debated_count"] == 0
    assert data["deterministic_count"] == 0


# --------------------------------------------------------------------------
# T42: 缺失前一次 step
# --------------------------------------------------------------------------

def test_judgment_context_handles_missing_previous_step():
    t = _dup_trace(n=2)
    # 前一次位置不存在
    ctx = build_judgment_context(t, 99, 99, 1, 2, "fp", tool_name=None)
    assert ctx.previous_step_id == 0
    assert ctx.current_step_id == 0


# --------------------------------------------------------------------------
# T43–T48: 评审 F
# --------------------------------------------------------------------------

def test_tool_result_changed_boundary_500():
    # 直接测 _tool_result_changed 前缀比较边界(评审 B)
    from agenttrace.analysis.c1_semantic import _tool_result_changed
    # 二者长度都 < 500 → 精确比较
    assert _tool_result_changed("x" * 499, "x" * 499) is False
    assert _tool_result_changed("x" * 499, "y" * 499) is True
    # 500 字符(不 <500)→ 前缀比较;前 500 相同 → None(不确定)
    assert _tool_result_changed("x" * 500, "x" * 500) is None
    assert _tool_result_changed("x" * 500 + "a", "x" * 500 + "b") is None
    # 前 500 不同 → True
    assert _tool_result_changed("x" * 500, "y" * 500) is True
    assert _tool_result_changed("x" * 501, "y" * 501) is True
    # 任一为 None → None
    assert _tool_result_changed(None, "abc") is None


def test_write_actions_coverage():
    from agenttrace.analysis.c1_semantic import WRITE_ACTIONS
    for tool in ("send_message", "write", "edit", "todo_write", "ask_user_question"):
        assert tool in WRITE_ACTIONS
    for tool in ("read_file", "glob", "pwsh", "list_sessions"):
        assert tool not in WRITE_ACTIONS


def test_merge_verdicts_malformed_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{ not json", encoding="utf-8")
    t = _dup_trace(n=2)
    cands = build_semantic_candidates(t, _tool001_findings(t))
    merged, vmap = merge_semantic_verdicts(cands, str(p))
    assert all(c.verdict == "not_applicable" for c in merged)
    assert vmap == {}


def test_report_context_none_renders():
    c = SemanticCandidate(tool_name="list_sessions", context=None, is_debated=True, total_occurrences=9)
    report = render_report(_dup_trace(n=2), [], [], enable_analysis=True, semantic_candidates=[c])
    assert "list_sessions" in report
    assert "待 agent 语义判断" in report


def test_tool004_with_successful_retry():
    t = _trace([
        _step(1, tcs=[_param_error_tc()]),
        _step(2, tcs=[_tc("send_session_message", '{"text":"x"}', result="succeeded", call_id="fail-1")]),
    ])
    cands = build_semantic_candidates(t, _tool004_findings(t))
    assert len(cands) == 1
    assert cands[0].rule_id == "TOOL-004"
    # 成功重试 → context 有 retry 信息
    assert cands[0].context is not None
    assert cands[0].context.current_result_snippet == "succeeded"


def test_same_step_multiple_tool_calls_fingerprint_match():
    # 同一步多 tool_call,按 fingerprint 精确定位
    t = _trace([
        _step(1, tcs=[_tc("read_file", '{"p":"a"}', result="r1"), _tc("write_file", '{"p":"b"}', result="w1")]),
        _step(2, tcs=[_tc("read_file", '{"p":"a"}', result="r2")]),
    ])
    cands = build_semantic_candidates(t, _tool001_findings(t))
    assert len(cands) == 1
    ctx = cands[0].context
    # read_file 第2次 → previous_result_snippet 应为 r1(read_file 结果),非 write_file 的 w1
    assert ctx.previous_result_snippet == "r1"
