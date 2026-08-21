"""TOOL-001 Detector 测试 + Precision/Recall 基线。

Golden Trace 每个样本都有机器可验证的期望:
- n_findings:期望检出的 Finding 数
- occurrences:期望的重复次数
- confidence_gt / confidence_lt:置信度边界
- stateless / has_error:特殊标记

Precision/Recall 基线(基于 8 个 golden 样本):
- 阳性样本(t3, t7 为阴性):6 个
- 期望检出 TOOL-001 的样本:6 个
"""

from __future__ import annotations

import pytest

from agenttrace.detectors.tool_001 import DuplicateToolCallDetector
from tests.golden.golden_tool_001 import ALL_GOLDEN

DETECTOR = DuplicateToolCallDetector()


def _detect(case_name: str):
    trace, expected = ALL_GOLDEN[case_name]()
    findings = DETECTOR.detect(trace)
    tool_findings = [f for f in findings if f.rule_id == "TOOL-001"]
    return trace, expected, tool_findings


# ---------- 精确断言 ----------

@pytest.mark.parametrize(
    "case, expected_n",
    [
        ("t1_duplicate_simple", 1),
        ("t2_duplicate_interleaved", 1),
        ("t4_arg_key_order", 1),
        ("t5_quadruple", 1),
        ("t6_stateless_boundary", 1),
        ("t8_error_retry", 1),
        ("t3_different_args", 0),
        ("t7_no_duplicate", 0),
    ],
)
def test_finding_count(case, expected_n):
    _, _, findings = _detect(case)
    assert len(findings) == expected_n, f"{case}: expected {expected_n} findings, got {len(findings)}"


@pytest.mark.parametrize(
    "case, expected_occurrences",
    [
        ("t1_duplicate_simple", 2),
        ("t2_duplicate_interleaved", 2),
        ("t4_arg_key_order", 2),
        ("t5_quadruple", 4),
        ("t6_stateless_boundary", 2),
        ("t8_error_retry", 2),
    ],
)
def test_occurrences(case, expected_occurrences):
    _, expected, findings = _detect(case)
    if findings:
        assert findings[0].occurrences == expected_occurrences, (
            f"{case}: expected occurrences={expected_occurrences}, got {findings[0].occurrences}"
        )


@pytest.mark.parametrize(
    "case, confidence_field, bound",
    [
        ("t1_duplicate_simple", "confidence_gt", 0.9),
        ("t2_duplicate_interleaved", "confidence_gt", 0.9),
        ("t4_arg_key_order", "confidence_gt", 0.9),
        ("t5_quadruple", "confidence_gt", 0.9),
        ("t8_error_retry", "confidence_gt", 0.9),
        ("t6_stateless_boundary", "confidence_lt", 0.8),
    ],
)
def test_confidence(case, confidence_field, bound):
    _, expected, findings = _detect(case)
    if not findings:
        return
    conf = findings[0].confidence
    if confidence_field == "confidence_gt":
        assert conf >= bound, f"{case}: confidence {conf} < {bound}"
    else:
        assert conf < bound, f"{case}: confidence {conf} >= {bound}"


def test_stateless_marker():
    _, _, findings = _detect("t6_stateless_boundary")
    assert findings and findings[0].details.get("stateless") is True


def test_severity_high_for_quadruple():
    _, _, findings = _detect("t5_quadruple")
    assert findings and findings[0].severity == "high"


def test_fingerprint_ignores_key_order():
    from agenttrace.core.normalize import call_fingerprint
    fp1 = call_fingerprint("api_call", '{"x":1,"y":2}')
    fp2 = call_fingerprint("api_call", '{"y":2,"x":1}')
    assert fp1 == fp2, "key order should not change fingerprint"


def test_fingerprint_differs_for_diff_args():
    from agenttrace.core.normalize import call_fingerprint
    assert call_fingerprint("read_file", '{"path":"a.py"}') != call_fingerprint(
        "read_file", '{"path":"b.py"}'
    )


def test_evidence_points_to_steps():
    _, _, findings = _detect("t1_duplicate_simple")
    assert findings
    step_ids = sorted(e.step_id for e in findings[0].evidence)
    assert step_ids == [1, 2], f"evidence steps: {step_ids}"


# ---------- Precision / Recall 基线 ----------

def _golden_labels():
    """返回 (case -> 是否应检出 TOOL-001)。"""
    return {
        "t1_duplicate_simple": True,
        "t2_duplicate_interleaved": True,
        "t3_different_args": False,
        "t4_arg_key_order": True,
        "t5_quadruple": True,
        "t6_stateless_boundary": True,
        "t7_no_duplicate": False,
        "t8_error_retry": True,
    }


def test_precision_recall_baseline():
    labels = _golden_labels()
    tp = fp_ = fn = tn = 0
    for case, should_detect in labels.items():
        _, _, findings = _detect(case)
        detected = len(findings) > 0
        if should_detect and detected:
            tp += 1
        elif not should_detect and detected:
            fp_ += 1
        elif should_detect and not detected:
            fn += 1
        else:
            tn += 1

    precision = tp / (tp + fp_) if (tp + fp_) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    # 基线:精确率与召回率都应 100%(TOOL-001 是确定性规则)
    assert precision == 1.0, f"precision {precision}, TP={tp} FP={fp_}"
    assert recall == 1.0, f"recall {recall}, TP={tp} FN={fn}"

    print(f"\n[Precision/Recall baseline] TP={tp} FP={fp_} FN={fn} TN={tn}")
    print(f"  Precision={precision:.2f} Recall={recall:.2f} F1={f1:.2f}")
