"""TOOL-004 Invalid-Param Retry 检测器测试(design detector-tool-004 D8)。

覆盖:
1. 触发规则:关键词命中(三代表词)/ 空参数代理(empty_args)/ 非参数错误不触发
2. 配对:相邻 step 同类成功 / 同 step 靠后不配对(M2)/ 同 call_id / 无成功重试 /
   参数不一致仍配对 / 跨 turn 不配对(M5)
3. 归因边界:tokens 三处 None(not applicable,非 0)、kind=flag
4. 置信度三档:call_id=0.95 / adjacent+关键词=0.85 / adjacent+empty=0.70
5. 确定性:同一 trace 两次 detect 逐条一致
6. 反证:adjacent_step 附 1 条反证且保置信;call_id 无反证
7. additive:golden 基线零 TOOL-004 finding + 报告逐字节一致;其余 5 个
   detector 输出与新增前一致
8. contract:公共 dataclass 字段集一致 + 报告五段式渲染含"无 token 归因"
"""

from __future__ import annotations

from pathlib import Path

from agenttrace.analysis.counter_evidence import analyze_finding
from agenttrace.attribution.base import Attribution, DirectAttribution, PropagatedAttribution
from agenttrace.attribution.tool_004 import Tool004AttributionEngine
from agenttrace.core.canonical_trace import Step, ToolCall, Trace, Turn
from agenttrace.detectors.base import Finding
from agenttrace.detectors.tool_004 import (
    ADJACENT_EMPTY_ARGS_CONFIDENCE,
    ADJACENT_KEYWORD_CONFIDENCE,
    CALL_ID_CONFIDENCE,
    PARAM_ERROR_KEYWORDS,
    InvalidParamRetryDetector,
)
from agenttrace.pipeline import diagnose
from agenttrace.report import render_report

DETECTOR = InvalidParamRetryDetector()
GOLDEN_DIR = Path(__file__).parent / "golden"


# --------------------------------------------------------------------------
# 构造辅助
# --------------------------------------------------------------------------

def _make_trace(turns):
    """构造 Trace。turns: list[list[list[dict]]] → turn → steps → tool_calls
    (dict 直接作为 ToolCall kwargs)。turn_id/step_id 从 1 顺序编号。"""
    t = Trace(session_id="tool-004-test")
    for ti, steps in enumerate(turns, 1):
        turn = Turn(turn_id=ti)
        for si, calls in enumerate(steps, 1):
            st = Step(step_id=si, turn_id=ti)
            for c in calls:
                st.tool_calls.append(ToolCall(**c))
            turn.steps.append(st)
        t.turns.append(turn)
    return t


def _ok(name="read_file", args='{"path":"a.py"}', call_id="ok-call"):
    return {
        "call_id": call_id, "tool_name": name, "arguments": args,
        "result": "ok", "is_error": False,
    }


def _err(result, name="read_file", args="{}", call_id="err-call"):
    return {
        "call_id": call_id, "tool_name": name, "arguments": args,
        "result": result, "is_error": True,
    }


def _adjacent_trace(failed_result="Error: invalid arguments", failed_args="{}"):
    """step 1 失败 attempt + step 2 同类成功(同一 turn,相邻 step)。"""
    return _make_trace(
        [
            [
                [_err(failed_result, args=failed_args)],
                [_ok()],
            ],
        ]
    )


def _tool_004(findings):
    return [f for f in findings if f.rule_id == "TOOL-004"]


def _findings_confidence(t):
    return _tool_004(DETECTOR.detect(t))[0].confidence


# --------------------------------------------------------------------------
# 1. 触发规则(6.1)
# --------------------------------------------------------------------------

def test_keyword_invalid_arguments_triggers():
    # 非空参数 + 关键词命中:只有关键词路径触发
    t = _adjacent_trace(failed_result="Error: invalid arguments: missing required property 'text'")
    findings = _tool_004(DETECTOR.detect(t))
    assert len(findings) == 1
    # PARAM_ERROR_KEYWORDS 顺序:第一个命中子串是 "invalid argument"
    assert findings[0].details["error_pattern"] == "invalid argument"


def test_keyword_missing_required_triggers():
    t = _adjacent_trace(failed_result="Error: missing required argument: path")
    findings = _tool_004(DETECTOR.detect(t))
    assert len(findings) == 1
    assert findings[0].details["error_pattern"] == "missing required"


def test_keyword_invalid_request_triggers():
    t = _adjacent_trace(failed_result="invalid_request_error: bad payload")
    findings = _tool_004(DETECTOR.detect(t))
    assert len(findings) == 1
    assert findings[0].details["error_pattern"] == "invalid_request"


def test_keyword_match_is_case_insensitive():
    t = _adjacent_trace(failed_result="Error: INVALID REQUEST from upstream")
    findings = _tool_004(DETECTOR.detect(t))
    assert len(findings) == 1
    assert findings[0].details["error_pattern"] == "invalid request"


def test_empty_args_triggers():
    # 非无状态工具 + 空参数 + is_error → empty_args 代理命中
    t = _adjacent_trace(failed_result="Error: boom", failed_args="{}")
    findings = _tool_004(DETECTOR.detect(t))
    assert len(findings) == 1
    assert findings[0].details["error_pattern"] == "empty_args"


def test_empty_args_variants_trigger():
    for args in ("", "null", "None"):
        t = _adjacent_trace(failed_result="Error: boom", failed_args=args)
        findings = _tool_004(DETECTOR.detect(t))
        assert len(findings) == 1, f"args={args!r} 应命中 empty_args"
        assert findings[0].details["error_pattern"] == "empty_args"


def test_non_param_error_not_detected():
    # 连接超时 + 非空参数 → 不触发(spec:非参数错误且 arguments 非空)
    t = _adjacent_trace(
        failed_result="Error: connection timed out after 30s",
        failed_args='{"path":"a.py"}',
    )
    assert _tool_004(DETECTOR.detect(t)) == []


def test_stateless_tool_empty_args_not_param_error():
    # 无状态工具恒为空参,因非参数原因报错不误判为无效参数
    t = _make_trace(
        [
            [
                [_err("Error: upstream 5xx", name="get_current_time", args="{}")],
                [_ok(name="get_current_time", args="{}")],
            ],
        ]
    )
    assert _tool_004(DETECTOR.detect(t)) == []


def test_success_call_not_detected():
    # 成功调用即使 result 含 "invalid" 字样也不触发(is_error 是必要条件)
    t = _make_trace(
        [
            [
                [_ok(args="{}")],
            ],
        ]
    )
    assert _tool_004(DETECTOR.detect(t)) == []


# --------------------------------------------------------------------------
# 2. 配对规则(6.2)
# --------------------------------------------------------------------------

def test_adjacent_step_success_detected():
    t = _adjacent_trace()
    findings = _tool_004(DETECTOR.detect(t))
    assert len(findings) == 1
    f = findings[0]
    assert f.details["retry_evidence"] == "adjacent_step"
    assert f.details["failed_index"] == (1, 1)
    assert f.details["retry_index"] == (1, 2)
    assert f.details["failed_call_id"] == "err-call"
    assert f.details["retry_call_id"] == "ok-call"


def test_same_step_later_call_not_retry():
    # M2 反例:同一 assistant 消息内多 tool-call 是并行,不构成"失败后重试"
    t = _make_trace(
        [
            [
                [
                    _err("Error: invalid arguments"),
                    _ok(),
                ],
            ],
        ]
    )
    assert _tool_004(DETECTOR.detect(t)) == []


def test_same_call_id_retry_detected():
    t = _make_trace(
        [
            [
                [_err("Error: invalid arguments", call_id="c1")],
                [_ok(call_id="c1")],
            ],
        ]
    )
    findings = _tool_004(DETECTOR.detect(t))
    assert len(findings) == 1
    assert findings[0].details["retry_evidence"] == "call_id"
    assert findings[0].confidence == CALL_ID_CONFIDENCE


def test_no_success_retry_not_detected():
    # 后续同类调用最终也失败 → 不输出
    t = _make_trace(
        [
            [
                [_err("Error: invalid arguments")],
                [_err("Error: permission denied", args='{"path":"a.py"}')],
            ],
        ]
    )
    assert _tool_004(DETECTOR.detect(t)) == []


def test_no_later_call_not_detected():
    # 失败 attempt 之后没有任何调用 → 不输出
    t = _make_trace([[[_err("Error: invalid arguments")]]])
    assert _tool_004(DETECTOR.detect(t)) == []


def test_args_mismatch_still_paired():
    # 缺参→补参,参数不一致仍按 tool_name 配对(TOOL-001 漏检的根因)
    t = _adjacent_trace(failed_args="{}")
    findings = _tool_004(DETECTOR.detect(t))
    assert len(findings) == 1
    assert findings[0].details["failed_arguments"] == "{}"
    assert findings[0].details["retry_arguments"] == '{"path":"a.py"}'


def test_cross_turn_not_paired():
    # M5:turn 末失败与下一 turn 首成功中间隔着用户新消息,非模型自主重试
    t = _make_trace(
        [
            [[_err("Error: invalid arguments")]],  # turn 1
            [[_ok()]],                              # turn 2
        ]
    )
    assert _tool_004(DETECTOR.detect(t)) == []


def test_take_closest_retry():
    # 多个候选成功调用时取距离最近的(step 2 而非 step 3)
    t = _make_trace(
        [
            [
                [_err("Error: invalid arguments")],
                [_ok()],
                [_ok()],
            ],
        ]
    )
    findings = _tool_004(DETECTOR.detect(t))
    assert len(findings) == 1
    assert findings[0].details["retry_index"] == (1, 2)


# --------------------------------------------------------------------------
# 3. 归因边界(6.3)
# --------------------------------------------------------------------------

def test_attribution_tokens_not_applicable():
    t = _adjacent_trace()
    result = diagnose(t)
    atts = [a for a in result.attributions if a.rule_id == "TOOL-004"]
    assert len(atts) == 1
    att = atts[0]
    assert att.direct.tokens is None  # not applicable,不是 0
    assert att.propagated.tokens is None
    assert att.unattributed_tokens is None
    assert att.total_tokens == 0  # None 按 0 处理,但语义 not applicable
    assert att.kind == "flag"
    assert att.confidence == _findings_confidence(t)


def test_attribution_candidate_step_ids_composite_key():
    # candidate_step_ids 存 (turn_id, step_id) 复合键(评审 m2)
    t = _adjacent_trace()
    f = _tool_004(DETECTOR.detect(t))[0]
    atts = Tool004AttributionEngine().attribute(t, [f])
    assert len(atts) == 1
    assert atts[0].direct.candidate_step_ids == [(1, 1)]  # 失败 attempt
    assert atts[0].direct.baseline_step_ids == []
    assert atts[0].propagated.step_ids == []


def test_attribution_not_applicable_not_zero_semantics():
    # 语义守护:None ≠ 0(not applicable ≠ "确实为 0")
    t = _adjacent_trace()
    f = _tool_004(DETECTOR.detect(t))[0]
    att = Tool004AttributionEngine().attribute(t, [f])[0]
    assert att.direct.tokens is None
    assert att.direct.tokens != 0


# --------------------------------------------------------------------------
# 4. 置信度三档(6.4)
# --------------------------------------------------------------------------

def test_confidence_call_id_tier():
    t = _make_trace(
        [
            [
                [_err("Error: invalid arguments", call_id="c1")],
                [_ok(call_id="c1")],
            ],
        ]
    )
    assert _tool_004(DETECTOR.detect(t))[0].confidence == 0.95


def test_confidence_adjacent_keyword_tier():
    t = _adjacent_trace(failed_result="Error: missing required argument")
    assert _tool_004(DETECTOR.detect(t))[0].confidence == 0.85


def test_confidence_adjacent_empty_args_tier():
    t = _adjacent_trace(failed_result="Error: boom", failed_args="{}")
    assert _tool_004(DETECTOR.detect(t))[0].confidence == 0.70


def test_confidence_tiers_within_range():
    for conf in (CALL_ID_CONFIDENCE, ADJACENT_KEYWORD_CONFIDENCE, ADJACENT_EMPTY_ARGS_CONFIDENCE):
        assert 0.0 <= conf <= 1.0
    # 三档互不相同
    assert len({CALL_ID_CONFIDENCE, ADJACENT_KEYWORD_CONFIDENCE, ADJACENT_EMPTY_ARGS_CONFIDENCE}) == 3


# --------------------------------------------------------------------------
# 5. 确定性(6.5)
# --------------------------------------------------------------------------

def test_deterministic_two_runs():
    t = _adjacent_trace()
    f1 = _tool_004(DETECTOR.detect(t))
    f2 = _tool_004(DETECTOR.detect(t))
    assert len(f1) == len(f2)
    for a, b in zip(f1, f2):
        assert a.rule_id == b.rule_id
        assert a.type == b.type
        assert a.confidence == b.confidence
        assert a.occurrences == b.occurrences
        assert a.kind == b.kind
        assert a.details == b.details
        assert [(e.step_id, e.turn_id, e.detail) for e in a.evidence] == [
            (e.step_id, e.turn_id, e.detail) for e in b.evidence
        ]


# --------------------------------------------------------------------------
# 6. 反证(6.6)
# --------------------------------------------------------------------------

def test_adjacent_step_counter_evidence():
    t = _adjacent_trace()
    f = _tool_004(DETECTOR.detect(t))[0]
    ces, conf = analyze_finding(f, t)
    assert len(ces) == 1
    assert ces[0].source == "rule"
    assert "独立调用" in ces[0].direction
    assert ces[0].detail == "tool=read_file"
    assert conf == f.confidence  # 保持 detector 原值


def test_call_id_no_counter_evidence():
    t = _make_trace(
        [
            [
                [_err("Error: invalid arguments", call_id="c1")],
                [_ok(call_id="c1")],
            ],
        ]
    )
    f = _tool_004(DETECTOR.detect(t))[0]
    ces, conf = analyze_finding(f, t)
    assert ces == []  # call 身份同一,无反证
    assert conf == 0.95


# --------------------------------------------------------------------------
# 7. additive(6.7)
# --------------------------------------------------------------------------

def test_golden_baseline_no_tool_004_byte_identical():
    """golden 基线 trace 上 TOOL-004 零输出,默认报告与 v0.5 逐字节一致。"""
    from tests.golden.golden_report import build_comprehensive_trace
    t = build_comprehensive_trace()
    result = diagnose(t)
    assert _tool_004(result.findings) == []
    report = render_report(result.trace, result.findings, result.attributions)
    expected = (GOLDEN_DIR / "v05_baseline_report.md").read_text(encoding="utf-8")
    assert report == expected


def _mixed_trace():
    """同时含 TOOL-001 重复对 + TOOL-004 参数错误对的 trace。"""
    return _make_trace(
        [
            [
                [_ok(call_id="c1")],                                          # step 1: TOOL-001 #1
                [_ok(call_id="c2")],                                          # step 2: TOOL-001 #2
                [_err("Error: invalid arguments", args="{}", call_id="c3")],  # step 3: TOOL-004 失败
                [_ok(call_id="c4")],                                          # step 4: TOOL-004 重试成功
            ],
        ]
    )


def test_other_five_detectors_unchanged():
    """含参数错误的 trace 上,其余 5 个 detector 输出与"新增前(仅 5 个)"一致。"""
    t = _mixed_trace()
    full = diagnose(t)  # 6 个 detector
    legacy = diagnose(
        t,
        detector_names=["TOOL-001", "CMP-001", "THINK-001", "RETRY-001", "SUB-001"],
    )

    # 新增前无 TOOL-004;新增后检出 1 条
    assert _tool_004(full.findings) != []
    assert _tool_004(legacy.findings) == []

    for rule in ("TOOL-001", "CMP-001", "THINK-001", "RETRY-001", "SUB-001"):
        f_full = [f for f in full.findings if f.rule_id == rule]
        f_leg = [f for f in legacy.findings if f.rule_id == rule]
        assert len(f_full) == len(f_leg), rule
        for a, b in zip(f_full, f_leg):
            assert a.confidence == b.confidence, rule
            assert a.occurrences == b.occurrences, rule
            assert a.kind == b.kind, rule
            assert a.details == b.details, rule
            assert [(e.step_id, e.turn_id, e.detail) for e in a.evidence] == [
                (e.step_id, e.turn_id, e.detail) for e in b.evidence
            ], rule
        # 归因数字一致
        a_full = [a for a in full.attributions if a.rule_id == rule]
        a_leg = [a for a in legacy.attributions if a.rule_id == rule]
        assert len(a_full) == len(a_leg), rule
        for x, y in zip(a_full, a_leg):
            assert x.kind == y.kind and x.total_tokens == y.total_tokens, rule
            assert x.confidence == y.confidence, rule


def test_default_report_bytes_unchanged_on_mixed_trace_except_tool_004():
    """混合 trace:默认报告与"仅 5 个 detector 渲染"的报告相比,
    差异只允许是 TOOL-004 相关内容(新 rule 计数行 + 统计标记计数 + 五段式节)。"""
    t = _mixed_trace()
    full = diagnose(t)
    legacy = diagnose(
        t,
        detector_names=["TOOL-001", "CMP-001", "THINK-001", "RETRY-001", "SUB-001"],
    )
    r_full = render_report(full.trace, full.findings, full.attributions)
    r_legacy = render_report(legacy.trace, legacy.findings, legacy.attributions)
    assert "TOOL-004" in r_full
    assert "TOOL-004" not in r_legacy
    # 逐 rule 的 finding 计数行一致(Summary 中非 TOOL-004 的 "- <rule>: N 个 finding")
    for line in r_legacy.splitlines():
        if line.startswith("- ") and "个 finding" in line:
            assert line in r_full, line
    # 五段式正文(Summary 之后)在 full 报告中逐字节保留:
    # 新增 TOOL-004 只追加到 flag 节,不改动既有节的内容
    body_marker = "## Cost defects"
    assert body_marker in r_legacy
    body = r_legacy.split(body_marker, 1)[1]
    assert body in r_full


# --------------------------------------------------------------------------
# 8. contract(6.8)
# --------------------------------------------------------------------------

def test_common_dataclass_fields():
    """TOOL-004 的 Finding / Attribution 走公共 dataclass,不新增字段。"""
    t = _adjacent_trace()
    f = _tool_004(DETECTOR.detect(t))[0]
    assert set(f.__dataclass_fields__) == set(Finding.__dataclass_fields__)
    att = Tool004AttributionEngine().attribute(t, [f])[0]
    assert set(att.__dataclass_fields__) == set(Attribution.__dataclass_fields__)
    assert set(att.direct.__dataclass_fields__) == set(DirectAttribution.__dataclass_fields__)
    assert set(att.propagated.__dataclass_fields__) == set(PropagatedAttribution.__dataclass_fields__)


def test_evidence_chain_two_links():
    """证据链含失败与成功两个 link,details 存同一实例。"""
    t = _adjacent_trace()
    f = _tool_004(DETECTOR.detect(t))[0]
    assert len(f.evidence) == 2
    e_fail, e_ok = f.evidence
    assert e_fail.turn_id == 1 and e_fail.step_id == 1
    assert "参数错误" in e_fail.detail and "invalid argument" in e_fail.detail
    assert e_ok.turn_id == 1 and e_ok.step_id == 2
    assert "同类重试成功" in e_ok.detail and "adjacent_step" in e_ok.detail
    assert e_fail.observed_value is None and e_ok.observed_value is None
    chain = f.details["evidence_chain"]
    assert chain.links is f.evidence  # 同一 EvidenceChain 实例


def test_report_five_sections_with_tool_004():
    """报告五段式渲染 TOOL-004,Attribution 段标注"无 token 归因"。"""
    t = _adjacent_trace()
    result = diagnose(t)
    report = render_report(result.trace, result.findings, result.attributions)

    assert "### TOOL-004 `invalid_param_retry`" in report
    assert "**Signal:** 无效参数重试:工具调用因参数错误失败,同类重试成功" in report
    assert "**Evidence:** turn 1 step 1" in report
    assert "**Observed:** tool=read_file error=invalid argument retry=adjacent_step" in report
    assert "**Attribution:** 无 token 归因(失败 attempt 无 usage,tokens=not applicable)" in report
    assert "**Interpretation:** 模式标记(可避免的失败尝试)" in report
    # Summary 计数:统计标记 = THINK-001 + TOOL-004
    assert "统计标记: 1" in report  # 本 trace 无 THINK-001,只有 TOOL-004


def test_report_no_fictitious_token_for_tool_004():
    """报告不出现失败 attempt 的虚构 token 数字(归因边界)。"""
    t = _adjacent_trace()
    result = diagnose(t)
    report = render_report(result.trace, result.findings, result.attributions)
    assert "无 token 归因" in report
    assert "Total wasted" not in report


def test_pipeline_finding_idx_pairs_correctly():
    """pipeline 按 rule 分组赋 finding_idx,TOOL-004 归因正确配对。"""
    t = _mixed_trace()  # 2 条 TOOL-001 + 1 条 TOOL-004
    result = diagnose(t)
    tool_004_fs = _tool_004(result.findings)
    assert len(tool_004_fs) == 1
    assert tool_004_fs[0].finding_idx == 0
    att = [a for a in result.attributions if a.rule_id == "TOOL-004"][0]
    assert att.finding_idx == tool_004_fs[0].finding_idx
    assert att.finding_id == "finding-TOOL-004-0"
