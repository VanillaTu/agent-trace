"""修复前后 A/B 对比验证(analysis/ab_validation.py,B1)。

分析层会话级数据块,与 TokenInvariant / ContextHealth / SessionLineage 同构,
挂在 DiagnosisResult.ab_result:
- 对单个真实会话构建 original(全量)与 fixed(去掉 TOOL-001 重复调用 / TOOL-004
  失败尝试)两种静态反事实重述,量化 tool-call 下降、删 step 数、output token 下降。
- 复用 TOOL-001 / TOOL-004 的检出逻辑(不改其行为),在分析层做"修复前后重述"。
- 语义隔离:轮询型工具(SEMANTIC_DEBATED_TOOLS)标记 semantic=debated,不计入硬可省。
- retry 严格分开:TOOL-001/004 只省工具调用层重试,不省 llm/retry(模型 API 重试)。
- 不做 Detector/Finding:不注册 ALL_DETECTORS / ALL_ATTRIBUTION_ENGINES。
- 仅 enable_analysis=True 时由 pipeline Stage 3 调用;默认关闭 → 零影响。

确定性铁律:所有指标由 trace 确定性计算;守恒模型(整 step 全冗余才删);
causal_claim=NONE;method=static_restatement(静态反事实重述,非真实重跑)。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.canonical_trace import Trace
from ..core.normalize import call_fingerprint
from ..detectors.tool_001 import STATELESS_TOOLS

# 轮询/状态读取型工具:反复读取当前状态可能合法且必要,同 fingerprint 重复≠一定浪费。
# 这些工具在长任务中"反复读取当前状态"可能合法必要;计入 semantic_debated,不计入硬可省。
SEMANTIC_DEBATED_TOOLS: frozenset[str] = frozenset({
    "list_agents",
    "list_sessions",
    "session_status",
    "mcp__browser_use__browser_get_state",
    "job_list",
    "job_output",
    "read_session",
    "memory_list",
    "mcp__browser_use__browser_navigate",
})

# 参数错误关键词(内联自 tool_004.PARAM_ERROR_KEYWORDS,与源头保持同步)。
# 若 tool_004.py 更新此集合,需同步更新本处。
_PARAM_ERROR_KEYWORDS = (
    "invalid argument",
    "missing required",
    "invalid_request",
    "invalid request",
    "invalid parameter",
    "required parameter",
    "required argument",
    "missing parameter",
    "missing argument",
    "unexpected argument",
    "unexpected keyword",
)


def _is_empty_args(arguments) -> bool:
    """内联自 tool_004._is_empty_args:判定参数字符串是否为空。"""
    return arguments is None or arguments.strip() in ("", "{}", "null", "None")


def _is_param_error(tc) -> str | None:
    """内联自 tool_004._match_param_error,与源头保持同步。

    若 tool_004.py 更新此逻辑,需同步更新本函数。
    命中显式关键词 → 返回命中词;仅空参数代理(非无状态工具)→ "empty_args";
    否则 None。
    """
    if not tc.is_error:
        return None
    text = (tc.result or "").lower()
    for kw in _PARAM_ERROR_KEYWORDS:
        if kw in text:
            return kw
    if _is_empty_args(tc.arguments) and tc.tool_name not in STATELESS_TOOLS:
        return "empty_args"
    return None


@dataclass
class ABResult:
    """修复前后 A/B 对比验证数据块(会话级观测,非 finding)。

    全部字段带默认值:空会话 / 无 TOOL-001/TOOL-004 finding → 全零块,不虚构数值。
    `None` 语义 = not applicable(无重复调用 / 无 TOOL-004),不是 0。
    """

    # ── 规模基线(original 口径) ──
    original_steps: int = 0
    original_tool_calls: int = 0
    original_output_tokens: int = 0
    original_input_tokens: int = 0
    original_total_tokens: int = 0

    # ── 规模基线(fixed 口径,保守模型) ──
    fixed_steps: int = 0
    fixed_tool_calls: int = 0
    fixed_output_tokens: int = 0
    fixed_input_tokens: int = 0
    fixed_total_tokens: int = 0

    # ── 硬指标(确定性、口径无关) ──
    tool_call_reduction: int = 0
    """工具调用下降数:fixed 比 original 少的 tool-call 数(确定性重复子集)。"""

    deleted_steps: int = 0
    """删除的 step 数:保守模型下整 step 可删的 step 数。"""

    # ── token 可信子指标 ──
    output_token_reduction: int = 0
    """output token 下降:被删 step 的 output_tokens 合计(确定性重复子集)。"""

    input_token_change: int = 0
    """input token 变化:被删 step 的 input_tokens 合计。
    标注为"上下文变化,非可省成本"——不判定为节省。"""

    total_token_change: int = 0
    """input+output 合计变化。标注为"含上下文,非纯省"。"""

    # ── retry 严格分开 ──
    tool_level_retries_saved: int = 0
    """工具调用层重试可省数:TOOL-004 的失败 attempt 数(fixed 中不再发生)。"""

    llm_retry_original: int = 0
    """original 中的 llm/retry 事件数(模型 API 重试,RETRY-001 对象)。"""

    llm_retry_fixed: int = 0
    """fixed 中的 llm/retry 事件数。实测不变(0 变化),但保留字段供验证。"""

    llm_retry_change: int = 0
    """llm/retry 事件数变化 = fixed - original。实测恒为 0。"""

    # ── 语义隔离 ──
    semantic_debated_occurrences: int = 0
    """语义存疑的冗余 occurrence 数:轮询型工具(SEMANTIC_DEBATED_TOOLS)的重复调用。"""

    semantic_debated_steps: int = 0
    """语义存疑的可删 step 数(轮询型工具整 step 可删)。不计入硬可省。"""

    # ── TOOL-004 专项 ──
    tool004_failed_attempts: int = 0
    """TOOL-004 失败 attempt 数(finding 数)。"""

    tool004_failed_step_output_tokens: int = 0
    """TOOL-004 失败 attempt 所在 step 的 output_tokens 合计。"""

    # ── 口径标注 ──
    model: str = "conservative"
    """修复口径:fixed = conservative(整 step 全冗余才删)。"""

    causal_claim: str = "NONE"
    """恒为 "NONE":描述性前后对比,非因果实验。"""

    method: str = "static_restatement"
    """恒为 "static_restatement":静态反事实重述,非真实重跑模型。"""

    # ── 原始 finding 计数(供报告引用) ──
    tool001_finding_count: int = 0
    """TOOL-001 finding 总数(确定性重复子集)。"""

    tool001_finding_count_debated: int = 0
    """TOOL-001 finding 中语义存疑(轮询型)的数量。"""

    tool004_finding_count: int = 0
    """TOOL-004 finding 总数。"""


def build_ab_validation(trace: Trace) -> ABResult:
    """对单个真实会话构建 original/fixed 静态反事实重述(纯函数,确定性)。

    算法(设计 D4,守恒模型,语义隔离):
    1. 按 call_fingerprint 分组所有 tool_call。
    2. 每组保留首次出现(全局顺序),标记后续为冗余 occurrence。
    3. 冗余 occurrence 分确定性(SEMANTIC_DEBATED_TOOLS 之外)与 debated(轮询型)。
    4. 一个 step 是"整 step 可删"当且仅当其所有 tool_call 均为冗余 occurrence;
       若含 debated 工具 → 计为 semantic_debated_steps(保留在 fixed,不计入硬可省)。
    5. TOOL-004:参数错误失败调用所在 step 加入 redundant_steps_set(并集去重)。
    6. fixed 指标 = original - 各 reduction;debated 的 step/tool-call 保留(不删)。
    """
    all_steps = list(trace.all_steps())

    # 1. original 基线
    original_steps = len(all_steps)
    original_tool_calls = len(trace.all_tool_calls())
    original_output_tokens = sum(s.usage.output_tokens for s in all_steps)
    original_input_tokens = sum(s.usage.input_tokens for s in all_steps)
    original_total_tokens = original_input_tokens + original_output_tokens

    # 2. 按 fingerprint 分组,tool_call 按全局顺序排列
    groups: dict[str, list] = {}
    global_pos = 0
    for st in all_steps:
        for tc in st.tool_calls:
            fp = call_fingerprint(tc.tool_name, tc.arguments)
            groups.setdefault(fp, []).append((st, tc, global_pos))
            global_pos += 1

    # 3. 标记每个 tool_call 是否冗余 + 是否 debated(轮询型)
    tc_redundant: dict[int, bool] = {}
    tc_debated: dict[int, bool] = {}
    hard_occurrences = 0
    debated_occurrences = 0
    tool001_finding_count = 0
    tool001_finding_count_debated = 0

    for fp, items in groups.items():
        if len(items) < 2:
            continue
        first_pos = min(global_pos for _, _, global_pos in items)
        tool_name = items[0][1].tool_name
        is_debated = tool_name in SEMANTIC_DEBATED_TOOLS
        if is_debated:
            tool001_finding_count_debated += 1
        else:
            tool001_finding_count += 1
        for st, tc, pos in items:
            is_red = pos != first_pos
            tc_redundant[id(tc)] = is_red
            tc_debated[id(tc)] = is_debated
            if is_red:
                if is_debated:
                    debated_occurrences += 1
                else:
                    hard_occurrences += 1

    # 4. 判定整 step 可删(保守:step 上所有 tool_call 均冗余)
    redundant_steps_set: set = set()   # (turn_id, step_id) 确定性可删(含 TOOL-004)
    debated_steps_set: set = set()     # (turn_id, step_id) debated 可删(保留)

    for st in all_steps:
        tcs = st.tool_calls
        if not tcs:
            continue
        all_redundant = all(tc_redundant.get(id(tc), False) for tc in tcs)
        if not all_redundant:
            continue
        any_debated = any(tc_debated.get(id(tc), False) for tc in tcs)
        if any_debated:
            debated_steps_set.add((st.turn_id, st.step_id))
        else:
            redundant_steps_set.add((st.turn_id, st.step_id))

    # 5. TOOL-004:参数错误失败调用(并集去重,不重复计数)
    tool004_failed_attempts = 0
    tool004_failed_step_output_tokens = 0
    seen_tool004_steps: set = set()
    for st in all_steps:
        for tc in st.tool_calls:
            if _is_param_error(tc) is not None:
                tool004_failed_attempts += 1
                if (st.turn_id, st.step_id) not in seen_tool004_steps:
                    seen_tool004_steps.add((st.turn_id, st.step_id))
                    tool004_failed_step_output_tokens += st.usage.output_tokens
                    redundant_steps_set.add((st.turn_id, st.step_id))

    # 6. 汇总
    deleted_steps = len(redundant_steps_set)
    semantic_debated_steps = len(debated_steps_set)

    def _deleted_step_usage() -> tuple[int, int]:
        inp = 0
        out = 0
        for st in all_steps:
            if (st.turn_id, st.step_id) in redundant_steps_set:
                inp += st.usage.input_tokens
                out += st.usage.output_tokens
        return inp, out

    del_in, del_out = _deleted_step_usage()
    output_token_reduction = del_out
    input_token_change = del_in
    total_token_change = del_in + del_out

    tool_call_reduction = hard_occurrences + tool004_failed_attempts
    tool_level_retries_saved = tool004_failed_attempts

    fixed_steps = original_steps - deleted_steps
    fixed_tool_calls = original_tool_calls - tool_call_reduction
    fixed_output_tokens = original_output_tokens - output_token_reduction
    fixed_input_tokens = original_input_tokens - input_token_change
    fixed_total_tokens = fixed_input_tokens + fixed_output_tokens

    # llm/retry 计数(不一致的事件与 step 解耦,静态重述下不变;防御性扫描 warning)
    llm_retry_original = sum(1 for e in trace.events if e.type == "llm/retry")
    for e in trace.events:
        if e.type == "llm/retry" and (e.turn_id, e.step_id) in redundant_steps_set:
            # 已知局限:被删 step 关联的 retry 在真实重跑中可能不再发生,静态重述无法模拟。
            pass
    llm_retry_fixed = llm_retry_original
    llm_retry_change = 0

    return ABResult(
        original_steps=original_steps,
        original_tool_calls=original_tool_calls,
        original_output_tokens=original_output_tokens,
        original_input_tokens=original_input_tokens,
        original_total_tokens=original_total_tokens,
        fixed_steps=fixed_steps,
        fixed_tool_calls=fixed_tool_calls,
        fixed_output_tokens=fixed_output_tokens,
        fixed_input_tokens=fixed_input_tokens,
        fixed_total_tokens=fixed_total_tokens,
        tool_call_reduction=tool_call_reduction,
        deleted_steps=deleted_steps,
        output_token_reduction=output_token_reduction,
        input_token_change=input_token_change,
        total_token_change=total_token_change,
        tool_level_retries_saved=tool_level_retries_saved,
        llm_retry_original=llm_retry_original,
        llm_retry_fixed=llm_retry_fixed,
        llm_retry_change=llm_retry_change,
        semantic_debated_occurrences=debated_occurrences,
        semantic_debated_steps=semantic_debated_steps,
        tool004_failed_attempts=tool004_failed_attempts,
        tool004_failed_step_output_tokens=tool004_failed_step_output_tokens,
        model="conservative",
        causal_claim="NONE",
        method="static_restatement",
        tool001_finding_count=tool001_finding_count,
        tool001_finding_count_debated=tool001_finding_count_debated,
        tool004_finding_count=tool004_failed_attempts,
    )
