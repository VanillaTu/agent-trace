"""Unified Pipeline:Trace → Detectors → Findings → Attribution → Analysis → Report。

架构 checkpoint #1/#2/#3:Registry 驱动,无 detector-specific if/else。

    Raw DSH → Adapter → Canonical Trace
        → Detector Registry (ALL_DETECTORS) → Finding[]
        → Attribution Registry (ALL_ATTRIBUTION_ENGINES) → Attribution[]
        → Analysis (Stage 3, enable_analysis=True):counter-evidence + 置信度 + 上下文健康度 + 画像
        → Report

关键保证:
- 新 detector 只做"注册",不复制代码
- 一个 detector 出错不阻塞其他(隔离异常)
- 缺失字段不导致整个 trace 失败
- 确定性铁律:enable_analysis 默认 False,关闭时输出与 v0.5 逐字节一致
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .attribution import ALL_ATTRIBUTION_ENGINES
from .core.canonical_trace import Trace
from .detectors import ALL_DETECTORS
from .detectors.base import Finding


@dataclass
class DiagnosisResult:
    """一次完整诊断的产物。"""

    trace: Trace
    findings: list[Finding] = field(default_factory=list)
    attributions: list = field(default_factory=list)
    detector_errors: dict[str, str] = field(default_factory=dict)
    attribution_errors: dict[str, str] = field(default_factory=dict)
    # 分析层开启时的会话画像(Stage 3 产物);关闭时为 None
    profile: Optional[object] = None
    # 分析层开启时的上下文健康度观测(Stage 3 产物,ContextHealth 实例);关闭时为 None
    context_health: Optional[object] = None
    # 分析层开启时的 Token 记账不变量观测(Stage 3 产物,TokenInvariant 实例);关闭时为 None
    token_invariant: Optional[object] = None
    # 分析层开启时的跨会话 Lineage 观测(Stage 3 产物,SessionLineage 实例);关闭或未传 session_map 时为 None
    session_lineage: Optional[object] = None
    # 分析层开启时的修复前后 A/B 对比验证观测(Stage 3 产物,ABResult 实例);关闭时为 None
    ab_result: Optional[object] = None
    # 分析层开启时的 LLM 语义判断候选清单(Stage 3 产物,list[SemanticCandidate]);关闭时为 None
    semantic_candidates: Optional[list] = None


def diagnose(
    trace: Trace,
    detector_names: list[str] | None = None,
    enable_analysis: bool = False,
    session_map: dict[str, Trace] | None = None,
) -> DiagnosisResult:
    """跑完整 pipeline。detector_names=None 时跑全部注册的 detector。

    每个 detector 独立运行:一个出错记录到 detector_errors,不阻塞其他。

    enable_analysis(默认 False):开启分析层 Stage 3(反证 + 置信度完善 + 会话画像)。
    关闭时分析阶段整体跳过,输出与 v0.5 逐字节一致(确定性铁律)。

    session_map(默认 None):跨会话 lineage 所需的 {session_id: Trace} 映射。
    传入时(且 enable_analysis=True)构建 SessionLineage;None 时 session_lineage 保持 None。
    """
    result = DiagnosisResult(trace=trace)

    # 选择 detector
    if detector_names is None:
        detectors = [d() for d in ALL_DETECTORS]
    else:
        by_name = {d.rule_id: d for d in ALL_DETECTORS}
        detectors = [by_name[n]() for n in detector_names if n in by_name]

    # Stage 1: Detectors → Findings
    for det in detectors:
        try:
            result.findings.extend(det.detect(trace))
        except Exception as e:  # 隔离异常,不阻塞其他 detector
            result.detector_errors[det.rule_id] = str(e)

    # Stage 2: Attribution Registry → Attribution
    # 按 rule_id 分组,每组只跑一次对应 engine
    findings_by_rule: dict[str, list[Finding]] = {}
    for f in result.findings:
        findings_by_rule.setdefault(f.rule_id, []).append(f)

    # 给每个 finding 赋 finding_idx(rule 内序号),供 report/attribution 配对
    for rule, rule_findings in findings_by_rule.items():
        for idx, f in enumerate(rule_findings):
            f.finding_idx = idx

    for rule, rule_findings in findings_by_rule.items():
        engine_cls = ALL_ATTRIBUTION_ENGINES.get(rule)
        if engine_cls is None:
            continue
        try:
            engine = engine_cls()
            atts = engine.attribute(trace, rule_findings)
            result.attributions.extend(atts)
        except Exception as e:
            result.attribution_errors[rule] = str(e)

    # Stage 3: Analysis(反证 + 置信度完善 + 上下文健康度 + 会话画像 + 跨会话 lineage + A/B 验证)
    # 默认关闭;开启时挂载在 attribution 之后(画像依赖 attribution 输出)
    if enable_analysis:
        from .analysis.ab_validation import build_ab_validation
        from .analysis.c1_semantic import build_semantic_candidates
        from .analysis.context_health import build_context_health
        from .analysis.counter_evidence import refine_findings
        from .analysis.profile import build_profile
        from .analysis.session_lineage import build_session_lineage
        from .analysis.token_invariant import build_token_invariant

        refine_findings(result.findings, trace)
        result.context_health = build_context_health(trace)  # 不进 findings/attributions
        result.profile = build_profile(result.findings, result.attributions)
        result.token_invariant = build_token_invariant(trace)  # 不进 findings/attributions
        result.ab_result = build_ab_validation(trace)  # B1:不进 findings/attributions
        result.semantic_candidates = build_semantic_candidates(trace, result.findings)  # C1
        # A2:跨会话 lineage 需要 session_map;None 时保持 None(单会话无跨会话数据)
        if session_map is not None:
            result.session_lineage = build_session_lineage(trace.session_id, session_map)

    return result
