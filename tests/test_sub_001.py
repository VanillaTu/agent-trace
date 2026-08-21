"""SUB-001 Detector + Attribution 测试。

覆盖用户定的场景:
- 单 subagent
- 多 subagent / fan-out
- incomplete descriptor(缺字段)
- label 为空
- lifecycle incomplete(数据无 lifecycle → 不猜)
- contract 兼容(不改核心 contract)
"""

from __future__ import annotations

import pytest

from agenttrace.attribution.sub_001 import Sub001AttributionEngine
from agenttrace.core.canonical_trace import Trace, TraceEvent
from agenttrace.detectors.sub_001 import SubagentDelegationDetector

DETECTOR = SubagentDelegationDetector()
ENGINE = Sub001AttributionEngine()


def _desc(seq=1, mode="one-shot", provider="fork", label="task", **extra):
    data = {"version": 2, "mode": mode, "provider": provider, "label": label}
    data.update(extra)
    return TraceEvent(type="subagent/descriptor", seq=seq, data=data)


def test_single_subagent_detected():
    t = Trace(session_id="s1")
    t.events = [_desc(seq=1, label="fetch docs")]
    findings = DETECTOR.detect(t)
    sub = [f for f in findings if f.rule_id == "SUB-001"]
    assert len(sub) == 1
    f = sub[0]
    assert f.kind == "observation"
    assert f.details["mode"] == "one-shot"
    assert f.details["provider"] == "fork"
    assert f.details["label"] == "fetch docs"


def test_multiple_subagent_fanout():
    """多 subagent(fan-out):每个都检出,不互相覆盖。"""
    t = Trace(session_id="s2")
    t.events = [
        _desc(seq=1, label="task A"),
        _desc(seq=2, mode="continuable", provider="spawn", label="task B"),
        _desc(seq=3, label="task C"),
    ]
    findings = DETECTOR.detect(t)
    sub = [f for f in findings if f.rule_id == "SUB-001"]
    assert len(sub) == 3
    # 各自 provider/mode 正确
    by_label = {f.details["label"]: f for f in sub}
    assert by_label["task A"].details["provider"] == "fork"
    assert by_label["task B"].details["provider"] == "spawn"
    assert by_label["task B"].details["mode"] == "continuable"


def test_incomplete_descriptor_missing_fields():
    """incomplete descriptor:缺字段不报错,用 unknown 兜底。"""
    t = Trace(session_id="s3")
    # 只有 version,无 mode/provider/label
    t.events = [TraceEvent(type="subagent/descriptor", seq=1, data={"version": 2})]
    findings = DETECTOR.detect(t)
    sub = [f for f in findings if f.rule_id == "SUB-001"]
    assert len(sub) == 1
    assert sub[0].details["mode"] == "unknown"
    assert sub[0].details["provider"] == "unknown"
    assert sub[0].details["label"] == "(no label)"


def test_empty_label_handled():
    t = Trace(session_id="s4")
    t.events = [_desc(seq=1, label="")]
    findings = DETECTOR.detect(t)
    sub = [f for f in findings if f.rule_id == "SUB-001"]
    assert sub[0].details["label"] == "(no label)"


def test_attribution_observation_no_tokens():
    """kind=observation, tokens=None(无成本证据)。"""
    t = Trace(session_id="s5")
    t.events = [_desc(seq=1)]
    findings = DETECTOR.detect(t)
    atts = ENGINE.attribute(t, findings)
    a = atts[0]
    assert a.kind == "observation"
    assert a.direct.tokens is None  # not applicable,不是 0
    assert a.propagated.tokens is None


def test_no_descriptor_no_finding():
    t = Trace(session_id="s6")
    t.events = [TraceEvent(type="tool/call", seq=1, data={})]
    findings = DETECTOR.detect(t)
    assert findings == []


def test_contract_compat():
    """SUB-001 不修改核心 contract(与前 4 个 detector 同构)。"""
    t = Trace(session_id="s7")
    t.events = [_desc(seq=1)]
    findings = DETECTOR.detect(t)
    atts = ENGINE.attribute(t, findings)
    for f in findings:
        assert set(f.__dataclass_fields__) == set(
            ("rule_id", "type", "severity", "confidence", "occurrences", "kind", "finding_idx", "evidence", "fingerprint", "details", "estimated_avoidable_tokens", "counter_evidence")
        )
    for a in atts:
        assert set(a.__dataclass_fields__) == set(
            ("finding_id", "rule_id", "finding_idx", "kind", "direct", "propagated", "unattributed_tokens", "confidence")
        )


def test_evidence_chain_present():
    t = Trace(session_id="s8")
    t.events = [_desc(seq=1)]
    findings = DETECTOR.detect(t)
    chain = findings[0].details["evidence_chain"]
    assert len(chain.links) == 1
    assert "subagent delegation" in chain.links[0].detail
