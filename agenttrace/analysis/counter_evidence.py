"""分析层:反证(counter-evidence)与置信度完善。

纯规则静态反证 + 置信度完善。全部纯函数:无随机、无时间、无外部调用,
同一 trace 两次运行结果逐条一致(确定性铁律)。

归因边界:
- 反证与置信度是"分析"不是"归因",不参与成本归因、不发明 token 成本。
- confidence 语义 = "该 finding 成立的证据强度",不是成本金额的可信度。

规则表(第一版,见 design.md D3):
    TOOL-001  间隔大 / 无状态工具 → 反证 + 降/保置信
    CMP-001   观测性反证(压缩可能是必要上下文管理)
    THINK-001 观测性反证(推理强度高不证明不必要)
    RETRY-001 观测性反证(重试可能是正确容错,usage=0 无成本)
    SUB-001   观测性反证(委托可能是合理并行/分工)
    TOOL-004  adjacent_step → 反证(可能是新的独立调用,保置信);call_id → 无反证
    其他     空反证,置信度保持原值
"""

from __future__ import annotations

from ..core.canonical_trace import Trace
from ..core.normalize import call_fingerprint
from ..detectors.base import CounterEvidence, Finding

# 反证阈值(相邻 occurrence 间隔步数),默认 5。
# 真实分布校准(76 会话 / 131 条 TOOL-001):gap 中位数 13,min 2,max 252,
# 仅 ~24% 的重复调用 gap ≤ 5 —— 说明多数重复是跨 turn 复读,反证机制实际在起作用;
# N=5 为保守默认(宁可漏报不误报),符合归因边界铁律。
DEFAULT_GAP_THRESHOLD = 5

# 置信度档位
HIGH_CONFIDENCE_FLOOR = 0.9   # 高置信档下界(证据强且无反证)
LOW_CONFIDENCE_CAP = 0.6      # 低置信档上界(存在强反证)
GAP_LARGE_CONFIDENCE = 0.5    # 间隔大 → 降到的确定性低值(< 0.6)


def _build_step_order(trace: Trace) -> dict[tuple[int, int], int]:
    """构建 (turn_id, step_id) → 全局序号 映射(沿用复合 key 约定)。"""
    order: dict[tuple[int, int], int] = {}
    idx = 0
    for turn in trace.turns:
        for st in turn.steps:
            order[(turn.turn_id, st.step_id)] = idx
            idx += 1
    return order


def _max_adjacent_gap(occ_indexes: list, trace: Trace) -> int | None:
    """计算相邻 occurrence 的最大间隔步数(全局序号差)。

    取舍(Pro 评审确认):以整条 finding 为分析单元取"最大相邻间隔",
    对 ≥3 occurrence 的混合聚类(紧邻簇 + 远端点)是整体近似——
    可能系统性低估紧邻重复簇的强度,属 spec 未定义的语义边界,记为 Open Question。

    无法定位的 occurrence 跳过;不足 2 个可定位位置时返回 None
    (证据不足:不触发反证,也不允许据此拔高置信度)。
    """
    if len(occ_indexes) < 2:
        return None
    order = _build_step_order(trace)
    positions = [order[occ] for occ in occ_indexes if occ in order]
    if len(positions) < 2:
        return None
    max_gap = 0
    for i in range(1, len(positions)):
        gap = positions[i] - positions[i - 1]
        if gap > max_gap:
            max_gap = gap
    return max_gap


def _raw_args_identical(finding: Finding, trace: Trace) -> bool:
    """判断 finding 所有 occurrence 的原始参数字符串是否完全一致(非归一化)。

    fingerprint 相同只保证归一化后等价;原始串可能不同(如 key 顺序不同)。
    这里比较原始 arguments 字符串,用于区分"参数完全一致"与"参数不完全一致"。
    无法定位任一 occurrence 时保守返回 False(视作不一致,走中间档)。
    """
    occ_indexes = finding.details.get("occurrence_indexes", [])
    if len(occ_indexes) < 2:
        # 证据不足:缺 details / occurrence_indexes 或仅 1 个 occurrence。
        # 保守原则(Pro 评审修正):保持原置信度,不拔高、不降级、无反证。
        return False
    steps = {
        (t.turn_id, st.step_id): st
        for t in trace.turns
        for st in t.steps
    }
    raw_args: list = []
    for occ in occ_indexes:
        st = steps.get(occ)
        if st is None:
            return False
        matched = None
        for tc in st.tool_calls:
            if call_fingerprint(tc.tool_name, tc.arguments) == finding.fingerprint:
                # 同一步内多个同指纹 call 时取第一个匹配(极罕见,见 canonical
                # trace 单步 ≤2 call 的约定);若需绝对精确可改用 occurrence 自身
                # 的 call_id 定位,超出当前范围。
                matched = tc.arguments
                break
        if matched is None:
            return False
        raw_args.append(matched)
    first = raw_args[0]
    return all(a == first for a in raw_args)


# --------------------------------------------------------------------------
# 规则函数:输入 (finding, trace, threshold_n) → (反证列表, 精化后置信度)
# --------------------------------------------------------------------------

def _tool_001(finding: Finding, trace: Trace, threshold_n: int) -> tuple[list[CounterEvidence], float]:
    """TOOL-001 反证 + 置信度完善(方向已由 Pro 评审纠正)。

    有状态工具 + 间隔 ≤ N + 参数完全一致 → 高置信(≥0.9)无反证;
    间隔 > N → 降置信(< 0.6) + 反证("间隔大,可能是有意操作");
    无状态工具 → 保持 0.55 + 反证("无状态工具,结果可能随时间变化");
    中间档(间隔 ≤ N 但参数不完全一致)→ 保持原值,无反证。
    """
    ces: list[CounterEvidence] = []
    conf = finding.confidence

    if finding.details.get("stateless", False):
        # stateless 短路(设计决策,Pro 评审确认):无状态工具优先命中,
        # 省略"间隔大"反证、不重复降置信;组合场景(无状态且间隔大)在
        # design D3 中未定义,此处取"信息最少但方向最稳"的一条。
        ces.append(
            CounterEvidence(
                direction="无状态工具,结果可能随时间变化,重复调用不必然浪费",
                source="rule",
                detail=f"tool={finding.details.get('tool_name', '?')}",
            )
        )
        return ces, conf  # 保持 detector 的 0.55

    occ_indexes = finding.details.get("occurrence_indexes", [])
    if len(occ_indexes) < 2:
        # 证据不足(无/仅 1 个 occurrence):保守保持原值,
        # 不拔高(修复 Pro 评审 Major#2:此前会 max(conf, 0.9) 错误抬升)。
        return [], conf
    gap = _max_adjacent_gap(occ_indexes, trace)
    if gap is None:
        # occurrence 无法在 trace 中定位(证据不足):同样保守保持原值。
        return [], conf
    args_identical = _raw_args_identical(finding, trace)

    if args_identical and gap <= threshold_n:
        # 高置信档(≥0.9),保持 detector 原值(0.98),无反证
        return ces, max(conf, HIGH_CONFIDENCE_FLOOR)

    if gap > threshold_n:
        ces.append(
            CounterEvidence(
                direction="两次调用间隔大,中间可能有状态变化,重复可能是有意操作",
                source="rule",
                detail=f"最大间隔 {gap} 步 > 阈值 {threshold_n}",
            )
        )
        return ces, GAP_LARGE_CONFIDENCE

    # 中间档:间隔 ≤ N 但参数不完全一致 → 保持原值,无额外反证
    return ces, conf


def _cmp_001(finding: Finding, trace: Trace, threshold_n: int) -> tuple[list[CounterEvidence], float]:
    return [
        CounterEvidence(
            direction="压缩可能是长上下文下的必要上下文管理,不构成浪费声明",
            source="rule",
        )
    ], finding.confidence


def _think_001(finding: Finding, trace: Trace, threshold_n: int) -> tuple[list[CounterEvidence], float]:
    return [
        CounterEvidence(
            direction="推理强度高不证明其不必要(统计标记,非缺陷)",
            source="rule",
        )
    ], finding.confidence


def _retry_001(finding: Finding, trace: Trace, threshold_n: int) -> tuple[list[CounterEvidence], float]:
    return [
        CounterEvidence(
            direction="重试可能是正确的容错行为,且失败尝试无 token 成本(usage=0)",
            source="rule",
        )
    ], finding.confidence


def _sub_001(finding: Finding, trace: Trace, threshold_n: int) -> tuple[list[CounterEvidence], float]:
    return [
        CounterEvidence(
            direction="委托可能是合理的并行/分工策略",
            source="rule",
        )
    ], finding.confidence


def _tool_004(finding: Finding, trace: Trace, threshold_n: int) -> tuple[list[CounterEvidence], float]:
    """TOOL-004 反证:adjacent_step 是"同类+相邻"推断,存在"成功调用其实是
    一次新的独立调用"的推翻方向;call_id 是 call 身份同一的直接证据,无反证。
    置信度保持 detector 原值(detector 已在三档中折价:0.85/0.70)。"""
    if finding.details.get("retry_evidence") == "adjacent_step":
        return [
            CounterEvidence(
                direction="相邻同类成功可能是新的独立调用而非重试(无 callId 关联,参数已修正)",
                source="rule",
                detail=f"tool={finding.details.get('tool_name', '?')}",
            )
        ], finding.confidence
    return [], finding.confidence  # call_id:身份同一,无反证


# rule_id → 纯函数
RULES: dict[str, object] = {
    "TOOL-001": _tool_001,
    "CMP-001": _cmp_001,
    "THINK-001": _think_001,
    "RETRY-001": _retry_001,
    "SUB-001": _sub_001,
    "TOOL-004": _tool_004,
}


def analyze_finding(
    finding: Finding,
    trace: Trace,
    threshold_n: int = DEFAULT_GAP_THRESHOLD,
) -> tuple[list[CounterEvidence], float]:
    """对单个 finding 计算反证列表 + 精化后置信度(纯函数)。

    无表项规则返回(空反证, 原置信度)。

    threshold_n 必须是正整数(间隔阈值);非法值(≤0 / 非 int / None)抛
    ValueError,防止静默进入"全量降级"或 TypeError 等不可预期路径。
    """
    if (
        not isinstance(threshold_n, int)
        or isinstance(threshold_n, bool)
        or threshold_n <= 0
    ):
        raise ValueError(
            f"threshold_n 必须为正整数,got {threshold_n!r}"
        )
    rule_fn = RULES.get(finding.rule_id)
    if rule_fn is None:
        return [], finding.confidence
    return rule_fn(finding, trace, threshold_n)  # type: ignore[operator]


def refine_findings(
    findings: list[Finding],
    trace: Trace,
    threshold_n: int = DEFAULT_GAP_THRESHOLD,
) -> None:
    """原地对 finding 列表应用反证 + 置信度完善(确定性,幂等)。

    每个 finding 的 counter_evidence 被整体替换(非累加),confid 被精化。
    分析层开启时由 pipeline Stage 3 调用;关闭时整体跳过。
    """
    for f in findings:
        ces, conf = analyze_finding(f, trace, threshold_n)
        f.counter_evidence = ces
        f.confidence = conf
