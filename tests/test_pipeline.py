"""Pipeline `--detector` 过滤器独立测试(此前从未单独测过)。

用覆盖 5 类 detector 的 comprehensive golden trace,验证 diagnose() 的
detector_names 参数真正"限定运行子集",而不是悄悄跑全部:

- 全跑(None):5 类 rule 都出现
- 只跑 TOOL-001:结果只含 TOOL-001(证明其他 detector 被排除)
- 空列表:不跑任何 detector,零 findings
- 未知名(如 --detector NOPE):静默丢弃,不报错
"""

from __future__ import annotations

from agenttrace.pipeline import diagnose
from tests.golden.golden_report import build_comprehensive_trace

ALL5 = ["CMP-001", "RETRY-001", "SUB-001", "THINK-001", "TOOL-001"]


def test_run_all_detectors_by_default():
    trace = build_comprehensive_trace()
    res = diagnose(trace)
    assert {f.rule_id for f in res.findings} == set(ALL5)


def test_filter_single_restricts_subset():
    trace = build_comprehensive_trace()
    res = diagnose(trace, detector_names=["TOOL-001"])
    # 只跑 TOOL-001:若过滤失效,其余 4 类 findings 会泄入
    assert {f.rule_id for f in res.findings} == {"TOOL-001"}
    assert len(res.findings) > 0


def test_filter_multi_allows_subset():
    trace = build_comprehensive_trace()
    res = diagnose(trace, detector_names=["TOOL-001", "CMP-001"])
    rules = {f.rule_id for f in res.findings}
    assert rules <= {"TOOL-001", "CMP-001"}
    assert "TOOL-001" in rules and "CMP-001" in rules


def test_filter_empty_selects_none():
    trace = build_comprehensive_trace()
    res = diagnose(trace, detector_names=[])
    assert res.findings == []


def test_filter_unknown_silently_dropped():
    trace = build_comprehensive_trace()
    res = diagnose(trace, detector_names=["NOPE"])
    assert res.findings == []
    assert res.detector_errors == {}


def test_filter_restricts_attribution_too():
    trace = build_comprehensive_trace()
    res = diagnose(trace, detector_names=["TOOL-001"])
    # 归因也只对选中的 rule 跑
    assert {a.rule_id for a in res.attributions} == {"TOOL-001"}
