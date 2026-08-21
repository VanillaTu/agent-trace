"""RETRY-001 Detector + Attribution 测试。

验证:
1. retry 事件 → finding(provider/mode/error_code/retry_count)
2. outcome:recovered / failed / unknown
3. lifecycle:attempt→fail→retry→success
4. zero-usage 语义:失败 attempt usage=0,不产生虚构 cost
5. attribution:kind=observation, tokens=0(不产生虚构 token cost)
6. contract 兼容(与前三个 detector 相同)
"""

from __future__ import annotations

import pytest

from agenttrace.attribution.retry_001 import Retry001AttributionEngine
from agenttrace.core.canonical_trace import Trace, TraceEvent
from agenttrace.detectors.retry_001 import ModelRetryDetector

DETECTOR = ModelRetryDetector()
ENGINE = Retry001AttributionEngine()


def _retry(rid, seq, turn=1, step=1, provider="ollama", mode="normal"):
    return TraceEvent(
        type="llm/retry", seq=seq, turn_id=turn, step_id=step,
        data={"retryId": rid, "turn": turn, "step": step, "provider": provider, "mode": mode},
    )


def _retry_started(rid, seq, retry_n, turn=1, step=1):
    return TraceEvent(
        type="llm/retry-started", seq=seq, turn_id=turn, step_id=step,
        data={"retryId": rid, "turn": turn, "step": step, "retry": retry_n},
    )


def _finish(kind, seq, turn=1, step=1, code=None, msg=None):
    data = {"reason": {"kind": kind}}
    if code:
        data["error_code"] = code
        data["error_message"] = msg
    return TraceEvent(type=f"llm/finish/{kind}", seq=seq, turn_id=turn, step_id=step, data=data)


def test_retry_detected_basic():
    # fail → retry → retry-started → success
    t = Trace(session_id="r1")
    t.events = [
        _finish("error", 1, code="TRANSPORT", msg="Connection error."),
        _retry("rid1", 2),
        _retry_started("rid1", 3, 1),
        _finish("success", 4),
    ]
    findings = DETECTOR.detect(t)
    retry = [f for f in findings if f.rule_id == "RETRY-001"]
    assert len(retry) == 1
    f = retry[0]
    assert f.details["provider"] == "ollama"
    assert f.details["retry_count"] == 1
    assert f.details["outcome"] == "recovered"


def test_outcome_failed():
    # fail → retry → retry-started → fail → retry → retry-started → fail
    t = Trace(session_id="r2")
    t.events = [
        _finish("error", 1, code="TRANSPORT"),
        _retry("rid2", 2),
        _retry_started("rid2", 3, 1),
        _finish("error", 4, code="TRANSPORT"),
        _retry("rid2", 5),
        _retry_started("rid2", 6, 2),
        _finish("error", 7, code="TRANSPORT"),
    ]
    findings = DETECTOR.detect(t)
    f = [x for x in findings if x.rule_id == "RETRY-001"][0]
    assert f.details["outcome"] == "failed"
    assert f.details["retry_count"] == 2


def test_outcome_unknown_when_no_finish_after():
    # 只有 retry,没有后续 finish → unknown
    t = Trace(session_id="r3")
    t.events = [
        _retry("rid3", 1),
        _retry_started("rid3", 2, 1),
    ]
    findings = DETECTOR.detect(t)
    f = [x for x in findings if x.rule_id == "RETRY-001"][0]
    assert f.details["outcome"] == "unknown"
    assert f.confidence < 0.95  # 未知时降置信度


def test_error_code_captured():
    t = Trace(session_id="r4")
    t.events = [
        _finish("error", 1, code="RATE_LIMIT", msg="429: exceeded"),
        _retry("rid4", 2),
        _retry_started("rid4", 3, 1),
        _finish("success", 4),
    ]
    findings = DETECTOR.detect(t)
    f = [x for x in findings if x.rule_id == "RETRY-001"][0]
    assert f.details["error_code"] == "RATE_LIMIT"


def test_attribution_no_fictitious_cost():
    """kind=observation,tokens=0(不产生虚构 token cost)。"""
    t = Trace(session_id="r5")
    t.events = [
        _finish("error", 1, code="TRANSPORT"),
        _retry("rid5", 2),
        _retry_started("rid5", 3, 1),
        _finish("success", 4),
    ]
    findings = DETECTOR.detect(t)
    atts = ENGINE.attribute(t, findings)
    a = atts[0]
    assert a.kind == "reliability"  # 可靠性事件
    assert a.direct.tokens is None  # not applicable,不是 0
    assert a.propagated.tokens is None
    assert a.total_tokens == 0  # None 按 0 计(但语义是 not applicable)


def test_multiple_retry_ids_isolated():
    """多个 retryId 不串。"""
    t = Trace(session_id="r6")
    t.events = [
        _finish("error", 1, code="TRANSPORT"),
        _retry("rida", 2),
        _retry_started("rida", 3, 1),
        _finish("success", 4),
        _finish("error", 5, code="RATE_LIMIT"),
        _retry("ridb", 6),
        _retry_started("ridb", 7, 1),
        _finish("success", 8),
    ]
    findings = DETECTOR.detect(t)
    retry = [f for f in findings if f.rule_id == "RETRY-001"]
    assert len(retry) == 2
    ids = {f.details["retry_id"] for f in retry}
    assert ids == {"rida", "ridb"}
    # 各自的 error code 正确
    by_id = {f.details["retry_id"]: f.details["error_code"] for f in retry}
    assert by_id["rida"] == "TRANSPORT"
    assert by_id["ridb"] == "RATE_LIMIT"


def test_retry_storm_high_count():
    """极端:TRANSPORT ×4 → failed(可靠性观测)。"""
    t = Trace(session_id="r7")
    t.events = []
    seq = 1
    for i in range(4):
        t.events.append(_finish("error", seq, code="TRANSPORT")); seq += 1
        t.events.append(_retry("rids", seq)); seq += 1
        t.events.append(_retry_started("rids", seq, i + 1)); seq += 1
    t.events.append(_finish("error", seq, code="TRANSPORT"))
    findings = DETECTOR.detect(t)
    f = [x for x in findings if x.rule_id == "RETRY-001"][0]
    assert f.details["retry_count"] == 4
    assert f.details["outcome"] == "failed"
    assert f.details["error_code"] == "TRANSPORT"


def test_no_retry_no_finding():
    t = Trace(session_id="r8")
    t.events = [_finish("success", 1)]
    findings = DETECTOR.detect(t)
    assert findings == []


def test_contract_compat():
    """RETRY-001 与 TOOL/CMP/THINK 同 contract。"""
    t = Trace(session_id="r9")
    t.events = [
        _finish("error", 1, code="TRANSPORT"),
        _retry("rid9", 2),
        _retry_started("rid9", 3, 1),
        _finish("success", 4),
    ]
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
