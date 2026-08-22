"""token-invariant-check(A1)Token 记账双写不变量测试。

覆盖 design.md 测试表 20 用例:
1. 数据块(TokenInvariant)空/单来源/双写一致/不一致/溢出量/因子/下界/确定性
2. adapter 事件生成(chunk+message 双写 → duplicate / inconsistent;单来源不报)
3. pipeline 门控(enable_analysis 开/关)
4. additive(不进 registry、默认路径逐字节不变)
5. E1–E4(3+ 来源 all-pairs / 除零保护 / data 一致性 / golden 全零块)
6. double_write_multiplier 恒 2.0、naive_double 下界语义
"""

from __future__ import annotations

import json

from agenttrace.adapters.dsh_adapter import parse_dsh_jsonl
from agenttrace.analysis.token_invariant import TokenInvariant, build_token_invariant
from agenttrace.attribution import ALL_ATTRIBUTION_ENGINES
from agenttrace.core.canonical_trace import (
    Step,
    Trace,
    TraceEvent,
    Turn,
    Usage,
)
from agenttrace.detectors import ALL_DETECTORS
from agenttrace.pipeline import diagnose
from agenttrace.report import render_report


# --------------------------------------------------------------------------
# 构造辅助
# --------------------------------------------------------------------------

def _usage(input_tokens=0, output_tokens=0, cache_read=0, reasoning=None):
    u = {
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheReadTokens": cache_read,
    }
    if reasoning is not None:
        u["reasoningTokens"] = reasoning
    return u


def _chunk_line(turn, step, usage, seq=1):
    return json.dumps(
        {
            "type": "assistant/chunk", "seq": seq, "time": 0,
            "data": {"turn": turn, "step": step, "chunk": {"type": "usage", "usage": usage}},
        }
    )


def _message_line(turn, step, usage, seq=2):
    return json.dumps(
        {
            "type": "assistant/message", "seq": seq, "time": 0,
            "data": {"turn": turn, "step": step, "message": {}, "usage": usage},
        }
    )


def _parse_jsonl(lines, tmp_path):
    p = tmp_path / "session.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return parse_dsh_jsonl(str(p))


def _dup_event(turn_id, step_id, total_tokens, source_count=2):
    return TraceEvent(
        type="token/usage-duplicate", turn_id=turn_id, step_id=step_id,
        data={
            "source_count": source_count,
            "total_tokens": total_tokens,
            "sources": ["chunk", "message"],
        },
    )


def _inc_event(turn_id, step_id, source_count=2):
    return TraceEvent(
        type="token/usage-inconsistent", turn_id=turn_id, step_id=step_id,
        data={"source_count": source_count, "sources": ["chunk", "message"]},
    )


def _trace_with_steps(steps_spec, events=None):
    """steps_spec: list of (input_tokens, output_tokens)。"""
    t = Trace(session_id="ti")
    turn = Turn(turn_id=1)
    for i, (input_tokens, output_tokens) in enumerate(steps_spec, 1):
        st = Step(step_id=i, turn_id=1)
        st.usage = Usage(input_tokens=input_tokens, output_tokens=output_tokens)
        turn.steps.append(st)
    t.turns = [turn]
    t.events = events or []
    return t


# --------------------------------------------------------------------------
# 1. 空 / 单来源
# --------------------------------------------------------------------------

def test_empty_trace_no_duplicate():
    t = Trace(session_id="empty")
    ti = build_token_invariant(t)
    assert ti.duplicate_usage_steps == 0
    assert ti.naive_double_count_tokens == 0
    assert ti.over_count_factor == 1.0
    assert ti.dedup_required is False
    assert ti.total_deduped_tokens == 0
    assert ti.inconsistent_usage_steps == 0


def test_single_source_no_duplicate(tmp_path):
    """仅 chunk usage、无 message usage → 不生成 duplicate 事件,不误报。"""
    t = _parse_jsonl([_chunk_line(1, 1, _usage(100, 50))], tmp_path)
    assert not any(e.type == "token/usage-duplicate" for e in t.events)
    ti = build_token_invariant(t)
    assert ti.duplicate_usage_steps == 0
    assert ti.dedup_required is False


# --------------------------------------------------------------------------
# 2. adapter 事件生成(双写一致 vs 不一致)
# --------------------------------------------------------------------------

def test_dual_write_generates_duplicate_event(tmp_path):
    t = _parse_jsonl(
        [_chunk_line(1, 1, _usage(100, 50), seq=1),
         _message_line(1, 1, _usage(100, 50), seq=2)],
        tmp_path,
    )
    dup = [e for e in t.events if e.type == "token/usage-duplicate"]
    assert len(dup) == 1
    assert dup[0].data["total_tokens"] == 150
    assert dup[0].data["source_count"] == 2
    assert dup[0].data["sources"] == ["chunk", "message"]


def test_dual_write_numeric_inconsistent(tmp_path):
    t = _parse_jsonl(
        [_chunk_line(1, 1, _usage(100, 50), seq=1),
         _message_line(1, 1, _usage(999, 999), seq=2)],
        tmp_path,
    )
    inc = [e for e in t.events if e.type == "token/usage-inconsistent"]
    assert len(inc) == 1
    assert not any(e.type == "token/usage-duplicate" for e in t.events)


# --------------------------------------------------------------------------
# 3. 溢出量 / 因子
# --------------------------------------------------------------------------

def test_naive_double_count_tokens():
    t = _trace_with_steps([(100, 50), (200, 60)], [_dup_event(1, 1, 150), _dup_event(1, 2, 260)])
    ti = build_token_invariant(t)
    assert ti.naive_double_count_tokens == 150 + 260


def test_over_count_factor_full_dual_write():
    # 2 step 全双写:total_deduped = 150+260=410,naive=410 → factor 2.0
    t = _trace_with_steps([(100, 50), (200, 60)], [_dup_event(1, 1, 150), _dup_event(1, 2, 260)])
    ti = build_token_invariant(t)
    assert ti.total_deduped_tokens == 410
    assert ti.over_count_factor == 2.0


def test_over_count_factor_partial_dual_write():
    # 2 step,仅 step1 双写:total=410,naive=150 → factor=(410+150)/410 ≈ 1.3659
    t = _trace_with_steps([(100, 50), (200, 60)], [_dup_event(1, 1, 150)])
    ti = build_token_invariant(t)
    assert 1.0 < ti.over_count_factor < 2.0


def test_over_count_factor_no_dual_write():
    t = _trace_with_steps([(100, 50), (200, 60)], [])
    ti = build_token_invariant(t)
    assert ti.over_count_factor == 1.0
    assert ti.duplicate_usage_steps == 0


def test_dedup_required_hedged():
    t = _trace_with_steps([(100, 50)], [_dup_event(1, 1, 150)])
    ti = build_token_invariant(t)
    assert ti.dedup_required is True


def test_inconsistent_not_in_overflow():
    # 1 dup(150)+ 1 inc → naive 只含 150(不含不一致 step)
    t = _trace_with_steps([(100, 50), (200, 60)], [_dup_event(1, 1, 150), _inc_event(1, 2)])
    ti = build_token_invariant(t)
    assert ti.naive_double_count_tokens == 150
    assert ti.inconsistent_usage_steps == 1
    assert ti.duplicate_usage_steps == 1


def test_deterministic():
    t = _trace_with_steps([(100, 50), (200, 60)], [_dup_event(1, 1, 150), _inc_event(1, 2)])
    ti1 = build_token_invariant(t)
    ti2 = build_token_invariant(t)
    assert ti1 == ti2  # dataclass 逐字段相等


# --------------------------------------------------------------------------
# 4. 非 Finding / 门控
# --------------------------------------------------------------------------

def test_token_invariant_not_a_finding():
    assert all(d.rule_id != "token-invariant" for d in ALL_DETECTORS)
    assert "token-invariant" not in ALL_ATTRIBUTION_ENGINES
    assert "token/usage-duplicate" not in ALL_ATTRIBUTION_ENGINES
    t = _trace_with_steps([(100, 50)], [_dup_event(1, 1, 150)])
    ti = build_token_invariant(t)
    assert isinstance(ti, TokenInvariant)  # 数据块,非 Finding


def test_disable_analysis_no_token_invariant():
    from tests.golden.golden_report import build_comprehensive_trace
    t = build_comprehensive_trace()
    result = diagnose(t)
    assert result.token_invariant is None


def test_enable_analysis_adds_token_invariant():
    from tests.golden.golden_report import build_comprehensive_trace
    t = build_comprehensive_trace()
    result = diagnose(t, enable_analysis=True)
    assert result.token_invariant is not None
    assert isinstance(result.token_invariant, TokenInvariant)


# --------------------------------------------------------------------------
# 5. E1–E4
# --------------------------------------------------------------------------

def test_source_count_gt_2_all_consistent(tmp_path):
    """E1:3 来源 all-pairs 判定(F1 修复)。"""
    # 3 来源全一致 → duplicate(source_count=3),无 assert 崩溃(按真实数据去掉 assert)
    t = _parse_jsonl(
        [_chunk_line(1, 1, _usage(0, 0), seq=1),
         _chunk_line(1, 1, _usage(0, 0), seq=2),
         _chunk_line(1, 1, _usage(0, 0), seq=3)],
        tmp_path,
    )
    dup = [e for e in t.events if e.type == "token/usage-duplicate"]
    assert len(dup) == 1
    assert dup[0].data["source_count"] == 3
    # 3 来源中第 3 个不一致 → all-pairs 判 inconsistent(不误报 duplicate)
    t2 = _parse_jsonl(
        [_chunk_line(1, 1, _usage(0, 0), seq=1),
         _chunk_line(1, 1, _usage(0, 0), seq=2),
         _chunk_line(1, 1, _usage(5, 5), seq=3)],
        tmp_path,
    )
    inc = [e for e in t2.events if e.type == "token/usage-inconsistent"]
    assert len(inc) == 1
    assert not any(e.type == "token/usage-duplicate" for e in t2.events)


def test_zero_deduped_tokens_no_div_zero():
    """E2:全部 step usage=0 → factor=1.0,不抛除零异常。"""
    t = _trace_with_steps([(0, 0), (0, 0)], [_dup_event(1, 1, 0)])
    ti = build_token_invariant(t)
    assert ti.total_deduped_tokens == 0
    assert ti.over_count_factor == 1.0
    assert ti.naive_double_count_tokens == 0


def test_event_data_total_matches_step_usage(tmp_path):
    """E3:duplicate 事件 data.total_tokens == Step.usage.total_tokens()。"""
    t = _parse_jsonl(
        [_chunk_line(1, 1, _usage(100, 50), seq=1),
         _message_line(1, 1, _usage(100, 50), seq=2)],
        tmp_path,
    )
    dup = [e for e in t.events if e.type == "token/usage-duplicate"][0]
    step = t.turns[0].steps[0]
    assert step.usage.total_tokens() == 150
    assert dup.data["total_tokens"] == step.usage.total_tokens()


def test_golden_enable_analysis_zero_block():
    """E4:golden trace + enable_analysis=True → token_invariant 非 None,数值全零块。"""
    from tests.golden.golden_report import build_comprehensive_trace
    t = build_comprehensive_trace()
    result = diagnose(t, enable_analysis=True)
    ti = result.token_invariant
    assert ti is not None
    assert ti.duplicate_usage_steps == 0
    assert ti.naive_double_count_tokens == 0
    assert ti.over_count_factor == 1.0
    assert ti.double_write_multiplier == 2.0
    assert ti.inconsistent_usage_steps == 0
    assert ti.dedup_required is False
    assert ti.total_deduped_tokens == sum(s.usage.total_tokens() for s in t.all_steps())
    # 报告集成:渲染"未检测到双写"块
    report = render_report(
        t, result.findings, result.attributions, enable_analysis=True,
        profile=result.profile, context_health=result.context_health,
        token_invariant=ti,
    )
    assert "### 架构不变量检查 — Token 记账(A1)" in report
    assert "未检测到 usage 双写" in report


# --------------------------------------------------------------------------
# 6. double_write_multiplier / 下界语义
# --------------------------------------------------------------------------

def test_double_write_multiplier():
    t = _trace_with_steps([(100, 50)], [_dup_event(1, 1, 150)])
    ti = build_token_invariant(t)
    assert ti.double_write_multiplier == 2.0


def test_naive_double_is_lower_bound():
    """部分 step 不一致 → naive 不含不一致 step → 下界语义。"""
    t = _trace_with_steps([(100, 50), (200, 60)], [_dup_event(1, 1, 150), _inc_event(1, 2)])
    ti = build_token_invariant(t)
    assert ti.naive_double_count_tokens == 150  # 不含 inc step(下界)
    assert ti.inconsistent_usage_steps == 1
