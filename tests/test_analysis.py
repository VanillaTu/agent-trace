"""complete-analysis-layer 分析层测试。

覆盖:
1. CounterEvidence dataclass + Finding.counter_evidence 默认空
2. TOOL-001 反证规则(间隔大/小、无状态工具、中间档)
3. CMP/THINK/RETRY/SUB 观测性反证
4. 置信度完善(降置信 + 保持原值 + 值域)
5. 画像排序(成本×置信度、tie-break、非 cost 排后、top-3、空)
6. 开关门控(关闭逐字节一致、开启生效)
7. 确定性(两次运行逐条一致)
8. 归因边界(反证/置信度不改变 attribution token)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agenttrace.analysis.counter_evidence import (
    DEFAULT_GAP_THRESHOLD,
    GAP_LARGE_CONFIDENCE,
    HIGH_CONFIDENCE_FLOOR,
    analyze_finding,
    refine_findings,
)
from agenttrace.analysis.profile import ProfileItem, SessionProfile, build_profile
from agenttrace.attribution.base import (
    Attribution,
    DirectAttribution,
    PropagatedAttribution,
)
from agenttrace.core.canonical_trace import (
    Step,
    ToolCall,
    Trace,
    TraceEvent,
    Turn,
    Usage,
)
from agenttrace.detectors.base import CounterEvidence, Finding
from agenttrace.detectors.tool_001 import DuplicateToolCallDetector
from agenttrace.pipeline import diagnose
from agenttrace.report import render_report

DETECTOR = DuplicateToolCallDetector()
GOLDEN_DIR = Path(__file__).parent / "golden"


# --------------------------------------------------------------------------
# 构造辅助
# --------------------------------------------------------------------------

def _tool_trace(tools, turn_id=1):
    """tools: list of (tool_name, args) → 每个一条 step(同一 turn)。"""
    t = Trace(session_id="analysis-tool")
    turn = Turn(turn_id=turn_id)
    for i, (name, args) in enumerate(tools, 1):
        st = Step(step_id=i, turn_id=turn_id)
        st.usage = Usage(input_tokens=1000, output_tokens=100)
        st.tool_calls.append(
            ToolCall(call_id=f"c{i}", tool_name=name, arguments=args)
        )
        turn.steps.append(st)
    t.turns = [turn]
    return t


def _interval_small_trace():
    return _tool_trace(
        [("read_file", '{"path":"a.py"}'), ("read_file", '{"path":"a.py"}')]
    )


def _interval_large_trace():
    # read_file 出现在 step1 与 step8,间隔 7 步 > 阈值 5
    tools = [("read_file", '{"path":"a.py"}')]
    for i in range(6):
        tools.append(("write_file", f'{{"path":"f{i}.py"}}'))
    tools.append(("read_file", '{"path":"a.py"}'))
    return _tool_trace(tools)


def _stateless_trace():
    return _tool_trace(
        [("get_current_time", "{}"), ("get_current_time", "{}")]
    )


def _middle_tier_trace():
    # fingerprint 相同(key 顺序不同),但原始参数字符串不同 → 中间档
    return _tool_trace(
        [("api_call", '{"x":1,"y":2}'), ("api_call", '{"y":2,"x":1}')]
    )


def _make_finding(rule_id, idx, kind, confidence, ce_count=0):
    f = Finding(
        rule_id=rule_id, type="t", severity="info",
        confidence=confidence, occurrences=1, kind=kind, finding_idx=idx,
    )
    f.counter_evidence = [
        CounterEvidence(direction="d") for _ in range(ce_count)
    ]
    return f


def _make_attribution(rule_id, idx, kind, tokens):
    return Attribution(
        finding_id=f"f-{rule_id}-{idx}",
        rule_id=rule_id,
        finding_idx=idx,
        kind=kind,
        direct=DirectAttribution(tokens=tokens),
        propagated=PropagatedAttribution(tokens=0),
        unattributed_tokens=0,
        confidence=0.9,
    )


# --------------------------------------------------------------------------
# 1. 数据结构
# --------------------------------------------------------------------------

def test_finding_counter_evidence_default_empty():
    f = Finding(rule_id="X", type="t", severity="info", confidence=0.5, occurrences=1)
    assert f.counter_evidence == []
    assert CounterEvidence(direction="d").source == "rule"
    assert CounterEvidence(direction="d").detail == ""


# --------------------------------------------------------------------------
# 2-4. 反证 + 置信度规则
# --------------------------------------------------------------------------

def test_stateless_counter_evidence_keep_low_confidence():
    t = _stateless_trace()
    findings = DETECTOR.detect(t)
    assert len(findings) == 1
    f = findings[0]
    assert f.details["stateless"] is True
    assert f.confidence == 0.55
    ces, conf = analyze_finding(f, t)
    assert conf == 0.55  # 保持 detector 低置信
    assert len(ces) == 1
    assert "无状态工具" in ces[0].direction
    assert ces[0].source == "rule"


def test_interval_large_lowers_confidence_with_counter_evidence():
    t = _interval_large_trace()
    findings = DETECTOR.detect(t)
    f = findings[0]
    assert f.confidence == 0.98  # detector 原值
    ces, conf = analyze_finding(f, t)
    assert conf < 0.6
    assert conf == GAP_LARGE_CONFIDENCE
    assert len(ces) == 1
    assert "间隔" in ces[0].direction
    assert "7 步" in ces[0].detail  # 实际间隔


def test_interval_small_high_confidence_no_counter_evidence():
    t = _interval_small_trace()
    findings = DETECTOR.detect(t)
    f = findings[0]
    ces, conf = analyze_finding(f, t)
    assert conf >= HIGH_CONFIDENCE_FLOOR
    assert conf == 0.98
    assert ces == []


def test_middle_tier_keep_original_no_counter_evidence():
    t = _middle_tier_trace()
    findings = DETECTOR.detect(t)
    f = findings[0]
    assert f.confidence == 0.98
    ces, conf = analyze_finding(f, t)
    assert conf == 0.98  # 保持原值
    assert ces == []


def test_observational_counter_evidence_for_four_rules():
    from tests.golden.golden_report import build_comprehensive_trace
    t = build_comprehensive_trace()
    result = diagnose(t, enable_analysis=True)
    by_rule = {f.rule_id: f for f in result.findings}
    for rule in ("CMP-001", "THINK-001", "RETRY-001", "SUB-001"):
        f = by_rule[rule]
        assert len(f.counter_evidence) == 1, rule
        assert f.counter_evidence[0].source == "rule", rule


def test_observational_rules_keep_confidence():
    from tests.golden.golden_report import build_comprehensive_trace
    t = build_comprehensive_trace()
    r_off = diagnose(t)
    r_on = diagnose(t, enable_analysis=True)
    off_conf = {f.rule_id: f.confidence for f in r_off.findings}
    on_conf = {f.rule_id: f.confidence for f in r_on.findings}
    for rule in ("CMP-001", "THINK-001", "RETRY-001", "SUB-001"):
        assert on_conf[rule] == off_conf[rule], rule


def test_unknown_rule_keeps_confidence_empty_counter_evidence():
    t = _interval_small_trace()
    f = Finding(rule_id="UNKNOWN-001", type="x", severity="info",
                confidence=0.7, occurrences=1)
    ces, conf = analyze_finding(f, t)
    assert ces == []
    assert conf == 0.7


def test_tool_001_missing_details_keeps_original_confidence():
    """Pro 评审 Major#2 修复:缺 details/occurrence_indexes 时保守保持原值,
    不得把 0.55 错误拔高到 0.9(无证据不拔高)。"""
    f = Finding(rule_id="TOOL-001", type="t", severity="low",
                confidence=0.55, occurrences=1)
    ces, conf = analyze_finding(f, Trace("x"))
    assert conf == 0.55
    assert ces == []


def test_tool_001_unlocatable_occurrences_keeps_original_confidence():
    """occurrence 无法在 trace 中定位(证据不足)→ 保守保持原值,不降不升。"""
    t = _tool_trace([("read_file", '{"path":"a.py"}')])  # 只有 1 个 step
    f = Finding(
        rule_id="TOOL-001", type="t", severity="low",
        confidence=0.98, occurrences=2,
        details={"occurrence_indexes": [(1, 1), (99, 99)]},  # (99,99) 定位失败
    )
    ces, conf = analyze_finding(f, t)
    assert conf == 0.98
    assert ces == []


def test_threshold_n_invalid_raises():
    """Pro 评审 Minor 项:threshold_n 非法值(≤0/非 int/None/bool)抛 ValueError。"""
    t = _interval_small_trace()
    f = DETECTOR.detect(t)[0]
    for bad in (0, -1, None, 1.5, "5", True):
        with pytest.raises(ValueError):
            analyze_finding(f, t, threshold_n=bad)  # type: ignore[arg-type]
    # 合法值不抛
    ces, conf = analyze_finding(f, t, threshold_n=5)
    assert isinstance(conf, float)


def test_confidence_within_range():
    for conf in (GAP_LARGE_CONFIDENCE, HIGH_CONFIDENCE_FLOOR, 0.55, 0.98):
        assert 0.0 <= conf <= 1.0


def test_refine_findings_deterministic():
    t = _interval_large_trace()
    f1 = DETECTOR.detect(t)
    refine_findings(f1, t)
    f2 = DETECTOR.detect(t)
    refine_findings(f2, t)
    for a, b in zip(f1, f2):
        assert a.confidence == b.confidence
        assert [c.direction for c in a.counter_evidence] == [
            c.direction for c in b.counter_evidence
        ]


def test_counter_evidence_does_not_change_attribution():
    """归因边界:反证/置信度不改变 attribution 的 token 与 kind。"""
    t = _interval_large_trace()
    r_off = diagnose(t)
    r_on = diagnose(t, enable_analysis=True)
    # 先断言两侧数量一致,防止 zip 静默错位(Pro 评审 Nit 项)
    assert len(r_off.attributions) == len(r_on.attributions)
    for a_off, a_on in zip(r_off.attributions, r_on.attributions):
        assert a_off.total_tokens == a_on.total_tokens
        assert a_off.kind == a_on.kind
        assert a_off.confidence == a_on.confidence


# --------------------------------------------------------------------------
# 5. 画像排序
# --------------------------------------------------------------------------

def test_profile_sorts_by_cost_times_confidence():
    findings = [
        _make_finding("TOOL-001", 0, "cost", 0.9),  # 100 × 0.9 = 90
        _make_finding("TOOL-001", 1, "cost", 0.5),  # 200 × 0.5 = 100
    ]
    attributions = [
        _make_attribution("TOOL-001", 0, "cost", 100),
        _make_attribution("TOOL-001", 1, "cost", 200),
    ]
    profile = build_profile(findings, attributions)
    assert [i.finding_idx for i in profile.items] == [1, 0]


def test_profile_tie_break_rule_id_asc():
    findings = [
        _make_finding("TOOL-001", 0, "cost", 0.9),  # 100 × 0.9 = 90
        _make_finding("CMP-001", 0, "cost", 0.9),   # 100 × 0.9 = 90
    ]
    attributions = [
        _make_attribution("TOOL-001", 0, "cost", 100),
        _make_attribution("CMP-001", 0, "cost", 100),
    ]
    profile = build_profile(findings, attributions)
    # 同分同置信度 → rule_id 升序:CMP-001 < TOOL-001
    assert [i.rule_id for i in profile.items] == ["CMP-001", "TOOL-001"]


def test_profile_tie_break_confidence_desc():
    findings = [
        _make_finding("TOOL-001", 0, "cost", 0.5),  # 100 × 0.5 = 50
        _make_finding("TOOL-001", 1, "cost", 0.9),  # 100 × 0.9 = 90
    ]
    attributions = [
        _make_attribution("TOOL-001", 0, "cost", 100),
        _make_attribution("TOOL-001", 1, "cost", 100),
    ]
    profile = build_profile(findings, attributions)
    assert [i.finding_idx for i in profile.items] == [1, 0]


def test_profile_non_cost_sorted_after():
    findings = [
        _make_finding("RETRY-001", 0, "reliability", 0.99),  # cost 0
        _make_finding("TOOL-001", 0, "cost", 0.5),            # 100 × 0.5 = 50
    ]
    attributions = [
        _make_attribution("RETRY-001", 0, "reliability", None),
        _make_attribution("TOOL-001", 0, "cost", 100),
    ]
    profile = build_profile(findings, attributions)
    assert [i.rule_id for i in profile.items] == ["TOOL-001", "RETRY-001"]


def test_profile_top_3_limit():
    findings = [_make_finding("TOOL-001", i, "cost", 0.9) for i in range(5)]
    attributions = [_make_attribution("TOOL-001", i, "cost", 100) for i in range(5)]
    profile = build_profile(findings, attributions)
    assert len(profile.items) == 3


def test_profile_empty():
    profile = build_profile([], [])
    assert profile.items == []
    assert "cost 缺陷 0 处" in profile.health_summary


def test_profile_health_summary_no_causal_assertion():
    from tests.golden.golden_report import build_comprehensive_trace
    t = build_comprehensive_trace()
    result = diagnose(t, enable_analysis=True)
    profile = result.profile
    assert "候选可避免" in profile.health_summary
    assert "浪费" not in profile.health_summary  # 禁止因果断言


def test_profile_deterministic():
    findings = [
        _make_finding("TOOL-001", 0, "cost", 0.9, ce_count=1),
        _make_finding("CMP-001", 0, "observation", 0.99),
    ]
    attributions = [
        _make_attribution("TOOL-001", 0, "cost", 100),
        _make_attribution("CMP-001", 0, "observation", 8541),
    ]
    p1 = build_profile(findings, attributions)
    p2 = build_profile(findings, attributions)
    assert [(i.rule_id, i.finding_idx, i.attributable_cost, i.confidence) for i in p1.items] == [
        (i.rule_id, i.finding_idx, i.attributable_cost, i.confidence) for i in p2.items
    ]
    assert p1.health_summary == p2.health_summary


# --------------------------------------------------------------------------
# 6. 开关门控
# --------------------------------------------------------------------------

def test_disable_analysis_byte_identical_to_v05():
    """确定性铁律:默认(关闭)输出与 v0.5 逐字节一致。"""
    from tests.golden.golden_report import build_comprehensive_trace
    t = build_comprehensive_trace()
    result = diagnose(t)
    report = render_report(result.trace, result.findings, result.attributions)
    expected = (GOLDEN_DIR / "v05_baseline_report.md").read_text(encoding="utf-8")
    assert report == expected


def test_disable_analysis_no_profile_no_counter_evidence():
    """关闭时:无画像、无反证、置信度不改写。"""
    from tests.golden.golden_report import build_comprehensive_trace
    t = build_comprehensive_trace()
    result = diagnose(t)
    assert result.profile is None
    for f in result.findings:
        assert f.counter_evidence == []
    report = render_report(result.trace, result.findings, result.attributions)
    assert "综合判断" not in report
    assert "Counter-evidence" not in report


def test_enable_analysis_adds_profile_and_counter_evidence():
    from tests.golden.golden_report import build_comprehensive_trace
    t = build_comprehensive_trace()
    result = diagnose(t, enable_analysis=True)
    assert result.profile is not None
    report = render_report(
        result.trace, result.findings, result.attributions,
        enable_analysis=True, profile=result.profile,
    )
    assert "综合判断" in report
    assert "健康度概述" in report
    assert "Confidence" in report
    assert "Counter-evidence" in report


def test_enable_analysis_per_finding_rendering():
    """开启时:每条 finding 追加 Confidence + Counter-evidence。"""
    from tests.golden.golden_report import build_comprehensive_trace
    t = build_comprehensive_trace()
    result = diagnose(t, enable_analysis=True)
    report = render_report(
        result.trace, result.findings, result.attributions,
        enable_analysis=True, profile=result.profile,
    )
    # 每条 finding 都有 Confidence 行(5 个 finding)
    assert report.count("**Confidence:**") == len(result.findings)
    # 每条 finding 都有 Counter-evidence 行
    assert report.count("**Counter-evidence") == len(result.findings)


def test_enable_analysis_report_renders_profile_lazily():
    """report 未显式传 profile 时,惰性从 findings+attributions 计算。"""
    from tests.golden.golden_report import build_comprehensive_trace
    t = build_comprehensive_trace()
    result = diagnose(t, enable_analysis=True)
    report = render_report(
        result.trace, result.findings, result.attributions, enable_analysis=True
    )
    assert "综合判断" in report
    assert "健康度概述" in report


def test_empty_findings_analysis_renders_no_items():
    """Pro 评审 Major#1 修复:空 findings + 开分析时仍渲染综合判断块,
    注明"无可调查项"(session-profile spec 0 条场景)。"""
    t = Trace(session_id="empty")
    report = render_report(t, [], [], enable_analysis=True)
    assert "未检出。" in report
    assert "综合判断" in report
    assert "无可调查项" in report
    # 关闭时保持 v0.5 行为:不渲染分析块
    report_off = render_report(t, [], [])
    assert "综合判断" not in report_off


def test_profile_all_non_cost_sorted_by_confidence():
    """Pro 评审测试缺口:全 non-cost(score 全 0)→ 置信度降序接管。"""
    findings = [
        _make_finding("RETRY-001", 0, "reliability", 0.4),
        _make_finding("CMP-001", 0, "observation", 0.99),
        _make_finding("SUB-001", 0, "observation", 0.7),
    ]
    attributions = [
        _make_attribution("RETRY-001", 0, "reliability", None),
        _make_attribution("CMP-001", 0, "observation", 100),
        _make_attribution("SUB-001", 0, "observation", 100),
    ]
    profile = build_profile(findings, attributions)
    assert [i.rule_id for i in profile.items] == ["CMP-001", "SUB-001", "RETRY-001"]


def test_default_path_confidence_untouched():
    """Pro 评审测试缺口:关闭时 finding.confidence 与 detector 原值一致
    (结构性开关之外的显式守护)。"""
    from tests.golden.golden_report import build_comprehensive_trace
    t = build_comprehensive_trace()
    det = DuplicateToolCallDetector()
    original = {f.rule_id: f.confidence for f in det.detect(t)}
    result = diagnose(t)
    for f in result.findings:
        if f.rule_id in original:
            assert f.confidence == original[f.rule_id], f.rule_id


def test_analysis_report_deterministic_bytes():
    """Pro 评审测试缺口:分析层开启时整份报告两次渲染逐字节一致。"""
    from tests.golden.golden_report import build_comprehensive_trace
    t = build_comprehensive_trace()
    result = diagnose(t, enable_analysis=True)
    r1 = render_report(result.trace, result.findings, result.attributions,
                       enable_analysis=True, profile=result.profile)
    r2 = render_report(result.trace, result.findings, result.attributions,
                       enable_analysis=True, profile=result.profile)
    assert r1 == r2
