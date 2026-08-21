"""Recommendation(建议维)测试:分析层补全四元组"建议"输出。

- 默认(enable_analysis=False):不渲染 Recommendation,输出与 v0.5 逐字节一致(确定性铁律不破)
- 开启(enable_analysis=True):每条 finding 追加 Recommendation,行动导向、按 rule 语义区分
- 归因边界:无 token 归因的 kind(flag/reliability)不得推荐"能回收 token"
- 确定性:同输入渲染两次一致
"""

from __future__ import annotations

from agenttrace.pipeline import diagnose
from agenttrace.report import RULE_META, render_report
from tests.golden.golden_report import build_comprehensive_trace


def _report(enabled: bool) -> str:
    t = build_comprehensive_trace()
    res = diagnose(t, enable_analysis=enabled)
    return render_report(
        res.trace,
        res.findings,
        res.attributions,
        enable_analysis=enabled,
        profile=res.profile,
    )


def test_default_has_no_recommendation():
    report = _report(False)
    assert "**Recommendation:**" not in report


def test_analysis_adds_recommendation_per_finding():
    report = _report(True)
    # comprehensive trace 触发 5 个 finding(= 5 条 Recommendation)
    assert report.count("**Recommendation:**") == 5


def test_recommendation_after_interpretation():
    report = _report(True)
    # 每条 Recommendation 紧跟其 Interpretation(同一 finding 内)
    assert report.index("**Interpretation:**") < report.index("**Recommendation:**")


def test_recommendation_text_by_rule():
    report = _report(True)
    # TOOL-001(cost)→幂等/去重,而非"能回收 token"
    assert "幂等" in report
    # RETRY-001(reliability,无 cost)→守归因边界:"无 token 归因"
    assert "无 token 归因" in report
    # CMP-001(observation)→容量基线
    assert "容量基线" in report


def test_recommendation_deterministic():
    assert _report(True) == _report(True)


def test_rule_meta_has_recommendation_for_all_rules():
    assert set(RULE_META) >= {
        "TOOL-001",
        "CMP-001",
        "THINK-001",
        "RETRY-001",
        "SUB-001",
        "TOOL-004",
    }
    for rule, meta in RULE_META.items():
        assert meta.get("recommendation"), f"{rule} 缺 recommendation"


def test_no_cost_kind_does_not_claim_recoverable_tokens():
    # 无 token 归因的 kind(flag/reliability)不得推荐"可回收"
    for rule in ("RETRY-001", "TOOL-004", "THINK-001"):
        assert "可回收" not in RULE_META[rule]["recommendation"], rule
