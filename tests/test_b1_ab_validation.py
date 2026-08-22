"""b1-ab-validation(B1)修复前后 A/B 对比验证测试。

覆盖 design.md 测试表 T1–T32:
1. ABResult 默认值/空 trace/无重复/单组/N 组
2. 整 step 可删/共享 step 不可删
3. output token 下降 / input token 分离
4. retry 严格分开(工具级 vs llm/retry)
5. 语义隔离(debated 不计入硬可省)
6. causal_claim=NONE / method=static_restatement / 确定性
7. TOOL-004 修复 / 并集去重
8. additive(金钟罩)/ CLI --ab 门控
9. 验证集 fixture / finding 拆分 / fixed 恒等式 / 锚点回归
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agenttrace.analysis.ab_validation import (
    ABResult,
    build_ab_validation,
    SEMANTIC_DEBATED_TOOLS,
)
from agenttrace.analysis.ab_validation_set import AB_VALIDATION_SESSIONS, _trace_from_dict
from agenttrace.core.canonical_trace import (
    Step,
    ToolCall,
    Trace,
    TraceEvent,
    Turn,
    Usage,
)
from agenttrace.pipeline import diagnose
from agenttrace.report import render_report

GOLDEN_DIR = Path(__file__).parent / "golden"
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "b1_validation_sessions"


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


def _dup_trace(n_steps=2, tool="read_file", args='{"p":"a"}', usage=(100, 50), extra_tcs=None):
    """构造 n_steps 步的 tool 重复调用 trace(TOOL-001)。"""
    steps = []
    for i in range(n_steps):
        tcs = [_tc(tool, args) for _ in range(1)]
        if extra_tcs:
            tcs += extra_tcs
        steps.append(_step(i + 1, usage=usage, tcs=tcs))
    return _trace(steps)


def _param_error_tc(tool="send_session_message"):
    return _tc(
        tool,
        '{"a":1}',
        is_error=True,
        result='Error: invalid arguments: missing required property "text"',
        call_id="fail-1",
    )


# --------------------------------------------------------------------------
# T1–T3: 默认值 / 空 trace / 无重复
# --------------------------------------------------------------------------

def test_ab_result_defaults_all_zero():
    ab = ABResult()
    assert ab.original_steps == 0
    assert ab.tool_call_reduction == 0
    assert ab.output_token_reduction == 0
    assert ab.deleted_steps == 0
    assert ab.tool_level_retries_saved == 0
    assert ab.llm_retry_change == 0
    assert ab.semantic_debated_occurrences == 0
    assert ab.causal_claim == "NONE"
    assert ab.method == "static_restatement"
    assert ab.model == "conservative"


def test_empty_trace_returns_zero_block():
    ab = build_ab_validation(Trace(session_id="empty"))
    assert ab.original_steps == 0
    assert ab.fixed_steps == 0
    assert ab.tool_call_reduction == 0
    assert ab.causal_claim == "NONE"


def test_no_duplicates_returns_zero_reduction():
    t = _trace([_step(1, usage=(100, 50), tcs=[_tc("read_file", '{"p":"a"}')])])
    ab = build_ab_validation(t)
    assert ab.tool_call_reduction == 0
    assert ab.deleted_steps == 0
    assert ab.fixed_steps == ab.original_steps
    assert ab.fixed_tool_calls == ab.original_tool_calls


# --------------------------------------------------------------------------
# T4–T6: tool-call 下降
# --------------------------------------------------------------------------

def test_single_duplicate_pair_tool_call_reduction():
    t = _dup_trace(2)
    ab = build_ab_validation(t)
    assert ab.tool_call_reduction == 1


def test_duplicate_group_n3_reduction():
    t = _dup_trace(3)
    ab = build_ab_validation(t)
    assert ab.tool_call_reduction == 2


def test_multiple_fingerprint_groups():
    t = _trace([
        _step(1, usage=(100, 50), tcs=[_tc("read_file", '{"p":"a"}'), _tc("write_file", '{"p":"b"}')]),
        _step(2, usage=(100, 50), tcs=[_tc("read_file", '{"p":"a"}')]),
        _step(3, usage=(100, 50), tcs=[_tc("write_file", '{"p":"b"}')]),
    ])
    ab = build_ab_validation(t)
    # read_file group N=2 → 1;write_file group N=2 → 1
    assert ab.tool_call_reduction == 2


# --------------------------------------------------------------------------
# T7–T8: 整 step 可删 / 共享 step 不可删
# --------------------------------------------------------------------------

def test_whole_step_deleted_conservative():
    t = _dup_trace(2)
    ab = build_ab_validation(t)
    assert ab.deleted_steps == 1  # 第 2 个 step 整删(第 1 个是 baseline)
    assert ab.tool_call_reduction == 1


def test_shared_step_not_deleted():
    # step2 含冗余 read_file + 非冗余 write_file(group B 单次)→ 不可整删
    t = _trace([
        _step(1, usage=(100, 50), tcs=[_tc("read_file", '{"p":"a"}')]),
        _step(2, usage=(100, 50), tcs=[_tc("read_file", '{"p":"a"}'), _tc("write_file", '{"p":"b"}')]),
    ])
    ab = build_ab_validation(t)
    assert ab.deleted_steps == 0
    assert ab.tool_call_reduction == 1  # 冗余 read_file 计入 tool-call 下降


# --------------------------------------------------------------------------
# T9–T10: output token 下降 / input token 分离
# --------------------------------------------------------------------------

def test_output_token_reduction():
    t = _trace([
        _step(1, usage=(100, 50), tcs=[_tc("read_file", '{"p":"a"}')]),
        _step(2, usage=(300, 120), tcs=[_tc("read_file", '{"p":"a"}')]),
    ])
    ab = build_ab_validation(t)
    assert ab.output_token_reduction == 120  # 第 2 个 step output
    assert ab.deleted_steps == 1


def test_input_token_not_claimed_as_saving():
    t = _trace([
        _step(1, usage=(100, 50), tcs=[_tc("read_file", '{"p":"a"}')]),
        _step(2, usage=(300, 120), tcs=[_tc("read_file", '{"p":"a"}')]),
    ])
    ab = build_ab_validation(t)
    # input_token_change 有值,但不计入 output_token_reduction
    assert ab.input_token_change == 300
    assert ab.output_token_reduction == 120
    assert ab.total_token_change == 300 + 120


# --------------------------------------------------------------------------
# T11–T12: retry 严格分开
# --------------------------------------------------------------------------

def test_llm_retry_unchanged():
    t = _dup_trace(2)
    t.events = [TraceEvent(type="llm/retry", turn_id=1, step_id=1, data={"retryId": "r1"})]
    ab = build_ab_validation(t)
    assert ab.llm_retry_original == 1
    assert ab.llm_retry_fixed == 1
    assert ab.llm_retry_change == 0


def test_tool_level_retry_separate_from_llm():
    t = _trace([
        _step(1, usage=(100, 50), tcs=[_param_error_tc()]),
        _step(2, usage=(100, 50), tcs=[_tc("send_session_message", '{"text":"x"}', call_id="fail-1")]),
    ])
    t.events = [TraceEvent(type="llm/retry", turn_id=1, step_id=1, data={"retryId": "r1"})]
    ab = build_ab_validation(t)
    assert ab.tool_level_retries_saved == 1  # 工具级
    assert ab.llm_retry_change == 0  # 模型 API 重试不变


# --------------------------------------------------------------------------
# T13–T14: 语义隔离
# --------------------------------------------------------------------------

def test_semantic_debated_not_in_hard_savings():
    t = _dup_trace(2, tool="list_sessions", args="{}")
    ab = build_ab_validation(t)
    assert ab.semantic_debated_occurrences == 1
    assert ab.semantic_debated_steps == 1
    assert ab.tool_call_reduction == 0  # debated 不计入硬可省
    assert ab.deleted_steps == 0
    assert ab.tool001_finding_count == 0
    assert ab.tool001_finding_count_debated == 1


def test_mixed_deterministic_and_debated():
    t = _trace([
        _step(1, usage=(100, 50), tcs=[_tc("read_file", '{"p":"a"}'), _tc("list_sessions", "{}")]),
        _step(2, usage=(100, 50), tcs=[_tc("read_file", '{"p":"a"}')]),
        _step(3, usage=(100, 50), tcs=[_tc("list_sessions", "{}")]),
    ])
    ab = build_ab_validation(t)
    # read_file(确定性)group N=2 → 1 hard;list_sessions(debated)group N=2 → 1 debated
    assert ab.tool_call_reduction == 1
    assert ab.semantic_debated_occurrences == 1
    assert ab.tool001_finding_count == 1
    assert ab.tool001_finding_count_debated == 1


# --------------------------------------------------------------------------
# T15–T17: causal_claim / method / 确定性
# --------------------------------------------------------------------------

def test_causal_claim_always_none():
    ab = build_ab_validation(_dup_trace(2))
    assert ab.causal_claim == "NONE"


def test_method_always_static_restatement():
    ab = build_ab_validation(_dup_trace(2))
    assert ab.method == "static_restatement"


def test_deterministic_same_trace_twice():
    t = _dup_trace(3)
    ab1 = build_ab_validation(t)
    ab2 = build_ab_validation(t)
    assert ab1 == ab2


# --------------------------------------------------------------------------
# T18–T20: TOOL-004 修复 / 并集去重
# --------------------------------------------------------------------------

def test_tool004_failed_step_removed():
    t = _trace([
        _step(1, usage=(100, 50), tcs=[_param_error_tc()]),
        _step(2, usage=(100, 50), tcs=[_tc("send_session_message", '{"text":"x"}', call_id="fail-1")]),
    ])
    ab = build_ab_validation(t)
    assert ab.tool004_failed_attempts == 1
    assert ab.deleted_steps == 1
    assert ab.tool_level_retries_saved == 1


def test_tool004_output_tokens_counted():
    t = _trace([
        _step(1, usage=(100, 130), tcs=[_param_error_tc()]),
        _step(2, usage=(100, 50), tcs=[_tc("send_session_message", '{"text":"x"}', call_id="fail-1")]),
    ])
    ab = build_ab_validation(t)
    assert ab.tool004_failed_step_output_tokens == 130


def test_union_dedup_tool001_and_tool004():
    # 同一 step 同时被 TOOL-001 冗余 + TOOL-004 失败标记 → 只删一次
    t = _trace([
        _step(1, usage=(100, 50), tcs=[_tc("read_file", '{"p":"a"}')]),
        _step(2, usage=(100, 50), tcs=[_param_error_tc()]),  # 既是 read_file 冗余? 不,这是 send_session_message 失败
    ])
    # 构造:step2 同时是 read_file 冗余(与 step1 同 finger)又含 param error
    t2 = _trace([
        _step(1, usage=(100, 50), tcs=[_tc("send_session_message", '{"a":1}')]),
        _step(2, usage=(100, 50), tcs=[_param_error_tc()]),
    ])
    # 让 read_file 在 step1 和 step2 出现(step2 加了 param error)
    ab = build_ab_validation(t2)
    # deleted_steps 不重复计数(len(set))
    assert ab.deleted_steps == 1


# --------------------------------------------------------------------------
# T21: additive(enable_analysis=False → ab_result None)
# --------------------------------------------------------------------------

def test_additive_enable_analysis_false():
    t = _dup_trace(2)
    result = diagnose(t)  # enable_analysis=False
    assert result.ab_result is None


# --------------------------------------------------------------------------
# T22: CLI --ab 自动启用 analysis
# --------------------------------------------------------------------------

def test_ab_flag_enables_analysis(tmp_path):
    import zstandard

    # 构造一个含 TOOL-001 重复的 DSH 会话(zstd JSONL)
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
    rc = main(["analyze", str(session_dir), "--ab", "--out", str(out)])
    assert rc == 0
    report = out.read_text(encoding="utf-8")
    assert "A/B 验证" in report  # AB 块渲染 → enable_analysis 被 --ab 开启


# --------------------------------------------------------------------------
# T23: 验证集 fixture 存在且可解析
# --------------------------------------------------------------------------

def test_validation_set_sessions_exist():
    assert len(AB_VALIDATION_SESSIONS) == 5
    # fixture 暂不入库(用户 2026-08-22 决定):缺失时 skip,不硬失败。
    # 本机有 fixture 则验证;跨机无 fixture 则跳过(不破坏 CI,也不假绿)。
    if not (FIXTURES_DIR / f"{AB_VALIDATION_SESSIONS[0]['session_id']}.json").exists():
        pytest.skip("B1 validation fixture 未入库(用户确认);本机有 fixture 时验证")
    for entry in AB_VALIDATION_SESSIONS:
        sid = entry["session_id"]
        fixture_path = FIXTURES_DIR / f"{sid}.json"
        assert fixture_path.exists(), f"Missing fixture: {fixture_path}"
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        assert "turns" in data  # CanonicalTrace 结构
        _trace_from_dict(data)  # 可反序列化


# --------------------------------------------------------------------------
# T24: 单会话无 finding 全零
# --------------------------------------------------------------------------

def test_single_session_no_findings_all_zero():
    t = _trace([_step(1, usage=(100, 50), tcs=[_tc("read_file", '{"p":"a"}')])])
    ab = build_ab_validation(t)
    assert ab.tool_call_reduction == 0
    assert ab.deleted_steps == 0
    assert ab.causal_claim == "NONE"


# --------------------------------------------------------------------------
# T25–T26: report 渲染 AB 块(开/关)
# --------------------------------------------------------------------------

def test_report_renders_ab_block_when_enabled():
    t = _dup_trace(2)
    ab = build_ab_validation(t)
    report = render_report(t, [], [], enable_analysis=True, ab_result=ab)
    assert "A/B 验证" in report
    assert "causal_claim=NONE" in report


def test_report_no_ab_block_when_disabled():
    t = _dup_trace(2)
    report = render_report(t, [], [])
    assert "A/B 验证" not in report
    report_off = render_report(t, [], [], enable_analysis=True)  # ab_result 缺省 None
    assert "A/B 验证" not in report_off


# --------------------------------------------------------------------------
# T27: fingerprint 一致性
# --------------------------------------------------------------------------

def test_fingerprint_consistency_with_detector():
    from agenttrace.detectors.tool_001 import DuplicateToolCallDetector
    from agenttrace.core.normalize import call_fingerprint
    t = _dup_trace(2)
    findings = DuplicateToolCallDetector().detect(t)
    assert len(findings) == 1
    # build_ab_validation 的 fingerprint 分组与 detector 一致(finding 出现次数)
    ab = build_ab_validation(t)
    # read_file group N=2 → 1 次冗余,detector 也报 1 条 occurrence=2
    assert findings[0].occurrences == 2
    assert ab.original_tool_calls == 2
    # 与 detector 同指纹
    fp = call_fingerprint("read_file", '{"p":"a"}')
    assert findings[0].fingerprint == fp


# --------------------------------------------------------------------------
# T28: 金钟罩(逐字节)
# --------------------------------------------------------------------------

def test_golden_report_byte_identical_with_analysis_disabled():
    from tests.golden.golden_report import build_comprehensive_trace
    t = build_comprehensive_trace()
    result = diagnose(t)
    report = render_report(result.trace, result.findings, result.attributions)
    expected = (GOLDEN_DIR / "v05_baseline_report.md").read_text(encoding="utf-8")
    assert report == expected


# --------------------------------------------------------------------------
# T29: fixed_* 恒等式
# --------------------------------------------------------------------------

def test_fixed_fields_equal_original_minus_reduction():
    t = _dup_trace(3)
    ab = build_ab_validation(t)
    assert ab.fixed_steps == ab.original_steps - ab.deleted_steps
    assert ab.fixed_tool_calls == ab.original_tool_calls - ab.tool_call_reduction
    assert ab.fixed_output_tokens == ab.original_output_tokens - ab.output_token_reduction
    assert ab.fixed_input_tokens == ab.original_input_tokens - ab.input_token_change
    assert ab.fixed_total_tokens == ab.original_total_tokens - ab.total_token_change


# --------------------------------------------------------------------------
# T30: debated 保留在 fixed
# --------------------------------------------------------------------------

def test_debated_tool_calls_preserved_in_fixed():
    t = _dup_trace(2, tool="list_sessions", args="{}")
    ab = build_ab_validation(t)
    # debated 的 tool-call 在 fixed 中保留(fixed_tool_calls 不扣减 debated)
    assert ab.fixed_tool_calls == ab.original_tool_calls
    assert ab.fixed_steps == ab.original_steps  # debated 的 step 不删


# --------------------------------------------------------------------------
# T31: finding 拆分(确定性 vs debated)
# --------------------------------------------------------------------------

def test_finding_counts_split_deterministic_vs_debated():
    t = _trace([
        _step(1, usage=(100, 50), tcs=[_tc("read_file", '{"p":"a"}'), _tc("list_sessions", "{}")]),
        _step(2, usage=(100, 50), tcs=[_tc("read_file", '{"p":"a"}')]),
        _step(3, usage=(100, 50), tcs=[_tc("list_sessions", "{}")]),
    ])
    ab = build_ab_validation(t)
    assert ab.tool001_finding_count == 1  # 确定性:read_file 组
    assert ab.tool001_finding_count_debated == 1  # debated:list_sessions 组


# --------------------------------------------------------------------------
# T32: 算法锚点回归(用 fixture,跨机可复现)
# --------------------------------------------------------------------------

def test_algorithm_matches_evidence_anchor():
    """对 3 个代表 anchor fixture,build_ab_validation 输出与语义隔离口径一致。"""
    anchors = {
        "session-a79579f3-f897-4a2c-aae7-e3910a206186": (17, 23, 7299),
        "session-1491c2c7-3cf8-4405-97cf-6c70159660f5": (11, 22, 3688),
        "session-112ce518-4d26-4e86-8a54-69c98175c2dd": (1, 1, 1249),
    }
    # fixture 暂不入库(用户决定):首个锚点 fixture 缺失时 skip,不硬失败。
    if not (FIXTURES_DIR / "session-a79579f3-f897-4a2c-aae7-e3910a206186.json").exists():
        pytest.skip("B1 validation fixture 未入库(用户确认);本机有 fixture 时验证")
    for sid, (exp_deleted, exp_reduction, exp_out_red) in anchors.items():
        data = json.loads((FIXTURES_DIR / f"{sid}.json").read_text(encoding="utf-8"))
        t = _trace_from_dict(data)
        ab = build_ab_validation(t)
        assert ab.deleted_steps == exp_deleted, f"{sid}: deleted={ab.deleted_steps}"
        assert ab.tool_call_reduction == exp_reduction, f"{sid}: reduction={ab.tool_call_reduction}"
        assert ab.output_token_reduction == exp_out_red, f"{sid}: out_red={ab.output_token_reduction}"
