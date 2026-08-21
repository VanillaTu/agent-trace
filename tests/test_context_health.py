"""detector-ctx-001 上下文健康度(ContextHealth)测试。

覆盖 design.md D7 全场景:
1. 空会话:全 not-applicable(不虚构数值)
2. 指标精确值(M1 口径:current = input + cache_read,含跨 turn 重复)
3. 窗口解析(B1:不虚构窗口,仅认 metadata["context_window"])
4. 压力标记(仅真实窗口且 occupancy > 0.70 才 True)
5. 确定性(两次构建逐字段一致 + 重复组 tie-break)
6. 归因边界(不进 ALL_DETECTORS / ALL_ATTRIBUTION_ENGINES,无成本归因)
7. additive(默认路径逐字节一致 + registry 仍 6 个)
8. 报告集成(enable_analysis=True 渲染健康度块 + 惰性构建)
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from agenttrace.analysis.context_health import (
    OCCUPANCY_HIGH_WATERMARK,
    WINDOW_METADATA_KEY,
    ContextHealth,
    build_context_health,
)
from agenttrace.attribution import ALL_ATTRIBUTION_ENGINES
from agenttrace.core.canonical_trace import Step, ToolCall, Trace, Turn, Usage
from agenttrace.detectors import ALL_DETECTORS
from agenttrace.pipeline import diagnose
from agenttrace.report import render_report

GOLDEN_DIR = Path(__file__).parent / "golden"


# --------------------------------------------------------------------------
# 构造辅助
# --------------------------------------------------------------------------

def _step(step_id, turn_id, input_tokens=0, cache_read=0, tools=()):
    st = Step(step_id=step_id, turn_id=turn_id)
    st.usage = Usage(input_tokens=input_tokens, cache_read_tokens=cache_read)
    for i, (name, args) in enumerate(tools, 1):
        st.tool_calls.append(
            ToolCall(call_id=f"c{step_id}-{i}", tool_name=name, arguments=args)
        )
    return st


def _metric_trace() -> Trace:
    """多 step + 跨 turn 重复 + cache_read 的 trace(5 次工具调用)。"""
    t = Trace(session_id="ctx-metrics")
    # turn 1
    t1 = Turn(turn_id=1)
    t1.steps.append(
        _step(1, 1, input_tokens=1000, cache_read=5000,  # ctx 6000
              tools=[("read_file", '{"path":"a.py"}')])
    )
    t1.steps.append(
        _step(2, 1, input_tokens=2000, cache_read=2000,  # ctx 4000
              tools=[("read_file", '{"path":"a.py"}')])  # dup #2
    )
    t1.steps.append(
        _step(3, 1, input_tokens=3000, cache_read=0,  # ctx 3000
              tools=[("web_search", '{"q":"x"}')])
    )
    # turn 2
    t2 = Turn(turn_id=2)
    t2.steps.append(
        _step(4, 2, input_tokens=1500, cache_read=7000,  # ctx 8500(peak)
              tools=[("read_file", '{"path":"a.py"}')])  # dup #3(跨 turn)
    )
    t2.steps.append(
        _step(5, 2, input_tokens=500, cache_read=100,  # ctx 600(末 step → current)
              tools=[("web_search", '{"q":"x"}')])     # dup(跨 turn)
    )
    t.turns = [t1, t2]
    return t


# --------------------------------------------------------------------------
# 1. 空会话
# --------------------------------------------------------------------------

def test_empty_session_all_not_applicable():
    t = Trace(session_id="empty")
    ch = build_context_health(t)
    assert ch.current_context_tokens == 0
    assert ch.peak_context_tokens == 0
    assert ch.turn_count == 0
    assert ch.total_tool_calls == 0
    assert ch.repeated_tool_calls == 0
    assert ch.repeat_rate is None  # not applicable,非 0
    assert ch.window_tokens is None
    assert ch.window_source == "unknown"
    assert ch.occupancy_ratio is None
    assert ch.pressure_high is False
    assert ch.stats_repeated_groups == []


# --------------------------------------------------------------------------
# 2. 指标精确值(M1 口径)
# --------------------------------------------------------------------------

def test_metrics_exact_values_with_cache_read():
    t = _metric_trace()
    ch = build_context_health(t)
    # M1:current = 末 step input + cache_read = 500 + 100
    assert ch.current_context_tokens == 600
    # peak = max(6000, 4000, 3000, 8500, 600)
    assert ch.peak_context_tokens == 8500
    assert ch.turn_count == 2
    assert ch.total_tool_calls == 5
    # read_file ×3(贡献 2)+ web_search ×2(贡献 1)
    assert ch.repeated_tool_calls == 3
    assert ch.repeat_rate == 3 / 5
    # 确定性排序:len 降序 → read_file 组(3)在前
    assert [g[1] for g in ch.stats_repeated_groups] == [3, 2]
    assert [g[2] for g in ch.stats_repeated_groups] == ["read_file", "web_search"]


def test_no_tool_calls_repeat_rate_none():
    """有 step 但无任何 tool_calls → repeat_rate=None(非 0)。"""
    t = Trace(session_id="no-tools")
    turn = Turn(turn_id=1)
    turn.steps.append(_step(1, 1, input_tokens=1000, cache_read=500))
    t.turns = [turn]
    ch = build_context_health(t)
    assert ch.total_tool_calls == 0
    assert ch.repeated_tool_calls == 0
    assert ch.repeat_rate is None
    assert ch.current_context_tokens == 1500
    assert ch.stats_repeated_groups == []


# --------------------------------------------------------------------------
# 3. 窗口解析(B1 不虚构)
# --------------------------------------------------------------------------

def test_window_from_metadata():
    t = _metric_trace()
    t.metadata = {WINDOW_METADATA_KEY: 128000}
    ch = build_context_health(t)
    assert ch.window_tokens == 128000
    assert ch.window_source == "metadata"
    assert ch.occupancy_ratio == 600 / 128000


def test_window_missing_is_unknown():
    """metadata 无 context_window 字段 → unknown,不虚构窗口/占用率。"""
    t = _metric_trace()
    t.metadata = {"cwd": "/tmp"}  # 其他字段不影响
    ch = build_context_health(t)
    assert ch.window_tokens is None
    assert ch.window_source == "unknown"
    assert ch.occupancy_ratio is None
    assert ch.pressure_high is False


def test_window_invalid_values_not_accepted():
    """context_window 非正 int(0/负/字符串)不算真实窗口。"""
    for bad in (0, -1, "128000", 128000.0):
        t = _metric_trace()
        t.metadata = {WINDOW_METADATA_KEY: bad}
        ch = build_context_health(t)
        assert ch.window_tokens is None, bad
        assert ch.window_source == "unknown", bad
        assert ch.occupancy_ratio is None, bad


# --------------------------------------------------------------------------
# 4. 压力标记
# --------------------------------------------------------------------------

def test_pressure_high_only_with_real_window_over_watermark():
    t = _metric_trace()
    # current = 600;窗口 800 → occupancy 0.75 > 0.70
    t.metadata = {WINDOW_METADATA_KEY: 800}
    ch = build_context_health(t)
    assert ch.occupancy_ratio == 0.75
    assert ch.pressure_high is True


def test_pressure_false_under_watermark():
    t = _metric_trace()
    # current = 600;窗口 128000 → occupancy ≈ 0.005 < 0.70
    t.metadata = {WINDOW_METADATA_KEY: 128000}
    ch = build_context_health(t)
    assert ch.pressure_high is False
    assert ch.occupancy_ratio <= OCCUPANCY_HIGH_WATERMARK


def test_pressure_never_without_window_even_large():
    """窗口 unknown 时恒 False,即使 current 很大(不虚构压力结论)。"""
    t = Trace(session_id="big")
    turn = Turn(turn_id=1)
    turn.steps.append(_step(1, 1, input_tokens=900_000, cache_read=100_000))
    t.turns = [turn]
    ch = build_context_health(t)
    assert ch.current_context_tokens == 1_000_000
    assert ch.window_source == "unknown"
    assert ch.occupancy_ratio is None
    assert ch.pressure_high is False


# --------------------------------------------------------------------------
# 5. 确定性 + tie-break
# --------------------------------------------------------------------------

def test_build_deterministic_field_equal():
    t = _metric_trace()
    ch1 = build_context_health(t)
    ch2 = build_context_health(t)
    assert asdict(ch1) == asdict(ch2)


def test_diagnose_analysis_deterministic():
    from tests.golden.golden_report import build_comprehensive_trace
    t = build_comprehensive_trace()
    r1 = diagnose(t, enable_analysis=True)
    r2 = diagnose(t, enable_analysis=True)
    assert asdict(r1.context_health) == asdict(r2.context_health)


def test_repeated_groups_tie_break_tool_name_asc():
    """重复数相等 → tool_name 升序。"""
    t = Trace(session_id="tie-tool")
    turn = Turn(turn_id=1)
    turn.steps.append(_step(1, 1, tools=[("b_tool", '{"k":1}')]))
    turn.steps.append(_step(2, 1, tools=[("b_tool", '{"k":1}')]))
    turn.steps.append(_step(3, 1, tools=[("a_tool", '{"k":2}')]))
    turn.steps.append(_step(4, 1, tools=[("a_tool", '{"k":2}')]))
    t.turns = [turn]
    ch = build_context_health(t)
    assert [g[1] for g in ch.stats_repeated_groups] == [2, 2]
    assert [g[2] for g in ch.stats_repeated_groups] == ["a_tool", "b_tool"]


def test_repeated_groups_tie_break_fingerprint_asc():
    """同 tool_name 且重复数相等 → fingerprint 升序(第三 key)。"""
    t = Trace(session_id="tie-fp")
    turn = Turn(turn_id=1)
    turn.steps.append(_step(1, 1, tools=[("mytool", '{"p":1}')]))
    turn.steps.append(_step(2, 1, tools=[("mytool", '{"p":1}')]))
    turn.steps.append(_step(3, 1, tools=[("mytool", '{"p":2}')]))
    turn.steps.append(_step(4, 1, tools=[("mytool", '{"p":2}')]))
    t.turns = [turn]
    ch = build_context_health(t)
    fps = [g[0] for g in ch.stats_repeated_groups]
    assert len(fps) == 2
    assert fps == sorted(fps)  # fingerprint 升序
    assert all(g[2] == "mytool" for g in ch.stats_repeated_groups)
    assert all(g[1] == 2 for g in ch.stats_repeated_groups)


# --------------------------------------------------------------------------
# 6. 归因边界
# --------------------------------------------------------------------------

def test_attribution_boundary_no_ctx001():
    assert "CTX-001" not in ALL_ATTRIBUTION_ENGINES
    assert "CTX-001" not in [d.rule_id for d in ALL_DETECTORS]
    from tests.golden.golden_report import build_comprehensive_trace
    t = build_comprehensive_trace()
    result = diagnose(t, enable_analysis=True)
    # attributions 中无任何 CTX-001 条目(无成本归因)
    assert all(a.rule_id != "CTX-001" for a in result.attributions)
    assert result.context_health is not None


def test_registries_still_six():
    """registry 不变:CTX-001 不进检测/归因体系(仍 6 个)。"""
    assert len(ALL_DETECTORS) == 6
    assert len(ALL_ATTRIBUTION_ENGINES) == 6


def test_health_block_no_cost_no_causal_assertion():
    from tests.golden.golden_report import build_comprehensive_trace
    t = build_comprehensive_trace()
    result = diagnose(t, enable_analysis=True)
    report = render_report(
        result.trace, result.findings, result.attributions,
        enable_analysis=True, profile=result.profile,
        context_health=result.context_health,
    )
    # 只取健康度块文本做语义边界检查
    start = report.index("### 上下文健康度")
    end = report.index("\n## ", start) if "\n## " in report[start:] else len(report)
    block = report[start:end]
    assert "浪费" not in block
    assert "导致" not in block
    assert "Total wasted" not in block
    # 只陈述"占用高关联退化风险"——当前无窗口,无压力行,块内无该断言亦可
    assert "上下文健康度" in block


# --------------------------------------------------------------------------
# 7. additive(关键)
# --------------------------------------------------------------------------

def test_default_path_unchanged_and_context_health_none():
    """默认(关闭)路径:context_health 为 None + 报告与 v0.5 golden 逐字节一致。"""
    from tests.golden.golden_report import build_comprehensive_trace
    t = build_comprehensive_trace()
    result = diagnose(t)
    assert result.context_health is None
    assert result.profile is None
    report = render_report(result.trace, result.findings, result.attributions)
    expected = (GOLDEN_DIR / "v05_baseline_report.md").read_text(encoding="utf-8")
    assert report == expected
    # registry 快照断言仍成立(6 个 detector)
    assert [d.rule_id for d in ALL_DETECTORS] == [
        "TOOL-001", "CMP-001", "THINK-001", "RETRY-001", "SUB-001", "TOOL-004",
    ]


# --------------------------------------------------------------------------
# 8. 报告集成
# --------------------------------------------------------------------------

def test_enable_analysis_renders_health_block():
    from tests.golden.golden_report import build_comprehensive_trace
    t = build_comprehensive_trace()
    result = diagnose(t, enable_analysis=True)
    report = render_report(
        result.trace, result.findings, result.attributions,
        enable_analysis=True, profile=result.profile,
        context_health=result.context_health,
    )
    assert "### 上下文健康度(CTX-001)" in report
    # 与综合判断块并列(块顺序:综合判断在前,健康度在后)
    assert report.index("### 综合判断") < report.index("### 上下文健康度")
    # 五段式不变量不受影响(健康度块不是 finding)
    assert report.count("**Confidence:**") == len(result.findings)
    # golden trace:末 step input=2000,cache_read=0 → current=2000
    assert "- 当前上下文: 2000 tokens(input + cache_read)" in report
    assert "- 峰值上下文: 2000 tokens" in report
    assert "- turn 数: 1" in report
    # 窗口 unknown → 占用 not applicable
    assert "- 占用率: not applicable" in report
    assert "上下文窗口: not applicable" in report


def test_enable_analysis_renders_health_block_lazily():
    """未传 context_health 时惰性构建(镜像 profile 惰性测试)。"""
    from tests.golden.golden_report import build_comprehensive_trace
    t = build_comprehensive_trace()
    result = diagnose(t, enable_analysis=True)
    report = render_report(
        result.trace, result.findings, result.attributions, enable_analysis=True,
        profile=result.profile,
    )
    assert "### 上下文健康度(CTX-001)" in report


def test_empty_findings_renders_health_block():
    """空 findings 早返回分支:分析层开启仍渲染健康度块(全 not-applicable)。"""
    t = Trace(session_id="empty")
    t.metadata = {}  # 窗口 unknown
    report = render_report(t, [], [], enable_analysis=True)
    assert "未检出。" in report
    assert "### 上下文健康度(CTX-001)" in report
    assert "- 占用率: not applicable" in report
    assert "无工具调用" in report
    # 关闭时保持 v0.5 行为:不渲染任何分析块
    report_off = render_report(t, [], [])
    assert "上下文健康度" not in report_off
    assert "综合判断" not in report_off


def test_health_block_pressure_line_only_when_true():
    """压力行仅 pressure_high=True 时输出,且文本含关联非因果标注。"""
    from tests.golden.golden_report import build_comprehensive_trace
    t = build_comprehensive_trace()
    # 给一个极小真实窗口强制 occupancy > 0.70(current=2000,window=2500)
    t.metadata = {WINDOW_METADATA_KEY: 2500}
    result = diagnose(t, enable_analysis=True)
    ch = result.context_health
    assert ch.pressure_high is True
    report = render_report(
        result.trace, result.findings, result.attributions,
        enable_analysis=True, profile=result.profile,
        context_health=ch,
    )
    assert "⚠ 上下文压力高,建议压缩" in report
    assert "占用 80.0% > 阈值 70%" in report
    assert "非因果" in report  # 相关性非因果标注
    # 无压力场景不出现压力行
    t2 = build_comprehensive_trace()
    result2 = diagnose(t2, enable_analysis=True)
    assert result2.context_health.pressure_high is False
    report2 = render_report(
        result2.trace, result2.findings, result2.attributions,
        enable_analysis=True, profile=result2.profile,
        context_health=result2.context_health,
    )
    assert "⚠ 上下文压力高" not in report2
