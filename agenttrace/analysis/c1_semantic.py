"""LLM 语义判断候选清单层(analysis/c1_semantic.py,C1)。

关键架构(用户两次澄清):**LLM 语义层在调用工具的 agent 身上,不在 AgentTrace 进程内**。
AgentTrace 是确定性工具,只产出候选清单 JSON + 上下文供 agent 判定;agent 用自身 LLM 回填
verdict(verdict + confidence + reason)。**AgentTrace 不内置 LLM 调用、不做 DSH 插件**。

- `SemanticCandidate`:候选重复(TOOL-001/TOOL-004),含定位/工具参数/是否 debated/判断上下文 +
  回填 verdict(评审 G:verdict 并入 SemanticCandidate,无独立 SemanticVerdict)。
- `JudgmentContext` / `InterveningAction`:纯确定性判断上下文(前后 step、干预动作、工具结果变化)。
- `build_semantic_candidates(trace, findings)`:从 TOOL-001/004 finding 生成候选,复用
  `SEMANTIC_DEBATED_TOOLS` 判 is_debated,排序(debated 优先 + 高倍率降序)。
- `build_judgment_context(...)`:前缀比较判 tool_result_changed(评审 B 修复),WRITE_ACTIONS
  含 todo_write/ask_user_question(17 个)。
- `merge_semantic_verdicts`:读 agent 回填 JSON,按 (rule_id, fingerprint, turn_id, step_id)
  原地填充 candidate 的 verdict 字段。
- `serialize_candidates_to_json`:候选清单序列化,JSON 顶层含 instructions(评审 A 阻塞修复)。

仅 `enable_analysis=True`(或显式 `--semantic`)时由 pipeline Stage 3 调用;默认关闭 → 零影响。
causal_claim=NONE;verdict 是语义建议非硬断言;不改变硬可省数字(B1 的 tool_call_reduction/
semantic_debated_occurrences 不变);未回填时 verdict=not_applicable,不猜。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..core.canonical_trace import Trace
from ..core.normalize import call_fingerprint
from .ab_validation import SEMANTIC_DEBATED_TOOLS

# 结果摘要截断长度
RESULT_SNIPPET_LEN = 500

# 写入/状态变更工具(确定性硬编码,评审 B 补充 todo_write/ask_user_question,现 17 个)。
# 与 DSH 工具集同步维护;MCP 插件写入工具按命名空间 pattern(mcp__*__send_*/create_*/delete_*/update_*)
# 无法穷举,保留注释供扩展。
WRITE_ACTIONS: frozenset[str] = frozenset({
    "send_message",
    "send_session_message",
    "write",
    "edit",
    "create_goal",
    "update_goal",
    "memory_save",
    "memory_update",
    "memory_delete",
    "memory_forget",
    "job_kill",
    "interrupt_agent",
    "session_recover",
    "subagent",
    "subagent_fork",
    "todo_write",          # 评审 B:修改 todo 列表(状态变更)
    "ask_user_question",   # 评审 B:向用户提问(交互状态变更)
})


@dataclass
class InterveningAction:
    """两次相同调用之间的一个干预动作。"""

    turn_id: int = 0
    step_id: int = 0
    tool_name: str = ""
    description: str = ""
    is_write: bool = False
    """是否为写入/状态变更操作(send_message / write / create / delete / kill 等)。"""


@dataclass
class JudgmentContext:
    """判断上下文:为 agent 判定"真冗余/合法"提供确定性信息。

    纯确定性构造;不包含任何 LLM 推断或猜测。
    """

    previous_turn_id: int = 0
    previous_step_id: int = 0
    previous_global_step: int = 0
    previous_result_snippet: str = ""
    """前一次调用结果的截断摘要(前 500 字符)。"""

    current_turn_id: int = 0
    current_step_id: int = 0
    current_global_step: int = 0
    current_result_snippet: str = ""
    """本次调用结果的截断摘要(前 500 字符)。"""

    gap_steps: int = 0
    """两次出现之间的 step 数(全局序号差 - 1)。"""

    intervening_actions: list[InterveningAction] = field(default_factory=list)
    """两次调用之间发生的、可能改变 agent 状态的动作列表。"""

    tool_result_changed: bool | None = None
    """两次调用的工具结果是否发生了变化。
    True = 结果不同(变化了),False = 结果相同(未变化),None = 无法判断(结果截断/不可比)。
    """


@dataclass
class SemanticCandidate:
    """语义判断候选:一个需要 agent 的 LLM 判定的重复调用实例。

    全部字段带默认值;空 trace 或无 TOOL-001 finding → 空列表。
    """

    # ── 定位 ──
    rule_id: str = "TOOL-001"
    turn_id: int = 0
    step_id: int = 0
    fingerprint: str = ""

    # ── 工具与参数 ──
    tool_name: str = ""
    arguments: str = ""

    # ── 是否 debated(轮询型工具) ──
    is_debated: bool = False
    """True = 轮询型工具(SEMANTIC_DEBATED_TOOLS),规则层无法区分冗余/合法。"""

    # ── 出现序号 ──
    occurrence_index: int = 1
    total_occurrences: int = 1

    # ── 判断上下文 ──
    context: Optional[JudgmentContext] = None
    """附带的判断上下文;构造失败时保持 None(保守:不虚构上下文)。"""

    # ── 回填 verdict(评审 G:合并进 SemanticCandidate) ──
    verdict: str = "not_applicable"
    """agent 的 LLM 判定:"true_redundant" | "legitimate" | "uncertain" | "not_applicable"(默认未判定)。"""
    confidence: float = 0.0
    """置信度 0.0-1.0;未判定时保持 0.0。"""
    reason: str = ""
    source: str = "semantic"
    causal_claim: str = "NONE"


def _build_step_maps(trace: Trace) -> tuple[dict, dict]:
    """构建 (turn_id, step_id) → Step 与 (turn_id, step_id) → 全局序号 的映射。"""
    steps_by_key: dict = {}
    step_order: dict = {}
    idx = 0
    for turn in trace.turns:
        for st in turn.steps:
            steps_by_key[(turn.turn_id, st.step_id)] = st
            step_order[(turn.turn_id, st.step_id)] = idx
            idx += 1
    return steps_by_key, step_order


def _find_tc(step, fingerprint: str, tool_name: str | None = None):
    """在 step 中找 tool_call:精确匹配 fingerprint;未命中则回退匹配 tool_name。

    fingerprint 优先(TOOL-001 同指纹);回退 tool_name 处理 TOOL-004(失败/重试参数不同)。
    """
    if step is None:
        return None
    if fingerprint:
        for tc in step.tool_calls:
            if call_fingerprint(tc.tool_name, tc.arguments) == fingerprint:
                return tc
    if tool_name:
        for tc in step.tool_calls:
            if tc.tool_name == tool_name:
                return tc
    return None


def _snippet(result: str) -> str:
    """截断结果摘要到 RESULT_SNIPPET_LEN 字符。"""
    return (result or "")[:RESULT_SNIPPET_LEN]


def _tool_result_changed(prev_result: str, curr_result: str) -> bool | None:
    """评审 B 修复:前缀比较判工具结果是否变化。

    - 任一为 None → None(无法比较)
    - 两者长度都 < 500 → 精确比较
    - 两者前 500 字符不同 → True(前缀已不同,确定性结论)
    - 否则(前缀相同且可能截断点之后不同)→ None(不确定)
    """
    if prev_result is None or curr_result is None:
        return None
    if len(prev_result) < RESULT_SNIPPET_LEN and len(curr_result) < RESULT_SNIPPET_LEN:
        return prev_result != curr_result
    if prev_result[:RESULT_SNIPPET_LEN] != curr_result[:RESULT_SNIPPET_LEN]:
        return True
    return None


def build_judgment_context(
    trace: Trace,
    prev_turn_id: int,
    prev_step_id: int,
    curr_turn_id: int,
    curr_step_id: int,
    fingerprint: str,
    tool_name: str | None = None,
) -> JudgmentContext:
    """为一次重复调用构造判断上下文(纯函数,确定性)。

    前次/本次调用用 fingerprint 定位具体 tool_call(TOOL-001 同指纹;TOOL-004 失败/重试
    参数不同则回退按 tool_name 定位)。构造失败时返回全默认值(保守:不虚构上下文)。
    """
    steps_by_key, step_order = _build_step_maps(trace)
    prev_key = (prev_turn_id, prev_step_id)
    curr_key = (curr_turn_id, curr_step_id)
    if prev_key not in steps_by_key or curr_key not in steps_by_key:
        return JudgmentContext()
    prev_step = steps_by_key[prev_key]
    curr_step = steps_by_key[curr_key]
    prev_tc = _find_tc(prev_step, fingerprint, tool_name)
    curr_tc = _find_tc(curr_step, fingerprint, tool_name)
    if prev_tc is None or curr_tc is None:
        return JudgmentContext()

    prev_result = prev_tc.result or ""
    curr_result = curr_tc.result or ""
    tc_changed = _tool_result_changed(prev_result, curr_result)

    # 干预动作:prev 与 curr 之间的所有 step
    intervening: list[InterveningAction] = []
    prev_global = step_order[prev_key]
    curr_global = step_order[curr_key]
    gap = curr_global - prev_global - 1
    for st in trace.all_steps():
        loc = (st.turn_id, st.step_id)
        if st.turn_id in (prev_turn_id, curr_turn_id) and loc in (prev_key, curr_key):
            continue  # 排除 prev 与 curr 自身
        pos = step_order.get(loc)
        if pos is None or not (prev_global < pos < curr_global):
            continue
        for tc in st.tool_calls:
            is_write = tc.tool_name in WRITE_ACTIONS
            intervening.append(
                InterveningAction(
                    turn_id=st.turn_id,
                    step_id=st.step_id,
                    tool_name=tc.tool_name,
                    description=tc.tool_name,
                    is_write=is_write,
                )
            )

    return JudgmentContext(
        previous_turn_id=prev_turn_id,
        previous_step_id=prev_step_id,
        previous_global_step=prev_global,
        previous_result_snippet=_snippet(prev_result),
        current_turn_id=curr_turn_id,
        current_step_id=curr_step_id,
        current_global_step=curr_global,
        current_result_snippet=_snippet(curr_result),
        gap_steps=gap,
        intervening_actions=intervening,
        tool_result_changed=tc_changed,
    )


def build_semantic_candidates(trace: Trace, findings) -> list[SemanticCandidate]:
    """从 TOOL-001/TOOL-004 finding 生成语义判断候选清单(纯函数,确定性)。

    排序:is_debated 优先 + total_occurrences 降序 + (turn_id, step_id) 稳定排序。
    无 TOOL-001/TOOL-004 finding 时返回空列表。
    """
    candidates: list[SemanticCandidate] = []
    for f in findings:
        if f.rule_id == "TOOL-001":
            occ_indexes = f.details.get("occurrence_indexes", [])
            if len(occ_indexes) < 2:
                continue
            tool_name = f.details.get("tool_name", "")
            fingerprint = f.fingerprint
            is_debated = tool_name in SEMANTIC_DEBATED_TOOLS
            total = len(occ_indexes)
            for i in range(1, total):
                prev_t, prev_s = occ_indexes[i - 1]
                curr_t, curr_s = occ_indexes[i]
                context = build_judgment_context(
                    trace, prev_t, prev_s, curr_t, curr_s, fingerprint
                )
                candidates.append(
                    SemanticCandidate(
                        rule_id="TOOL-001",
                        turn_id=curr_t,
                        step_id=curr_s,
                        fingerprint=fingerprint,
                        tool_name=tool_name,
                        arguments=f.details.get("arguments", ""),
                        is_debated=is_debated,
                        occurrence_index=i + 1,
                        total_occurrences=total,
                        context=context,
                    )
                )
        elif f.rule_id == "TOOL-004":
            tool_name = f.details.get("tool_name", "")
            failed_index = f.details.get("failed_index")
            retry_index = f.details.get("retry_index")
            failed_args = f.details.get("failed_arguments", "")
            if failed_index is None:
                continue
            turn_id, step_id = failed_index
            fp = call_fingerprint(tool_name, failed_args)
            context = None
            if retry_index is not None:
                r_turn, r_step = retry_index
                context = build_judgment_context(
                    trace, turn_id, step_id, r_turn, r_step, fp, tool_name=tool_name
                )
            candidates.append(
                SemanticCandidate(
                    rule_id="TOOL-004",
                    turn_id=turn_id,
                    step_id=step_id,
                    fingerprint=fp,
                    tool_name=tool_name,
                    arguments=failed_args,
                    is_debated=False,
                    occurrence_index=1,
                    total_occurrences=1,
                    context=context,
                )
            )

    # 排序:debated 优先 → total_occurrences 降序 → (turn_id, step_id) 稳定
    candidates.sort(
        key=lambda c: (
            not c.is_debated,
            -c.total_occurrences,
            c.turn_id,
            c.step_id,
        )
    )
    return candidates


def merge_semantic_verdicts(candidates: list[SemanticCandidate], verdicts_path: str | Path):
    """合并 agent 回填的 verdict 到候选清单(纯函数)。

    Args:
        candidates: build_semantic_candidates 的产出(原地填充 verdict 字段)。
        verdicts_path: agent 回填的 verdict JSON 文件路径。

    Returns:
        (候选列表, verdicts_map):verdicts_map 为 {(rule_id, fingerprint, turn_id, step_id): verdict_str}。
        格式错误的 JSON → 返回 (candidates, {})(空合并,不崩溃)。
    """
    path = Path(verdicts_path)
    if not path.exists():
        return candidates, {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return candidates, {}  # malformed → 空合并

    if not isinstance(data, dict):
        return candidates, {}
    verdicts = data.get("verdicts", [])
    if not isinstance(verdicts, list):
        return candidates, {}

    verdicts_map: dict = {}
    for entry in verdicts:
        if not isinstance(entry, dict):
            continue
        key = (
            entry.get("rule_id"),
            entry.get("fingerprint"),
            entry.get("turn_id"),
            entry.get("step_id"),
        )
        verdicts_map[key] = entry.get("verdict", "not_applicable")
        for c in candidates:
            if (c.rule_id, c.fingerprint, c.turn_id, c.step_id) == key:
                c.verdict = entry.get("verdict", "not_applicable")
                c.confidence = float(entry.get("confidence", 0.0))
                c.reason = entry.get("reason", "")
                c.source = "semantic"
                c.causal_claim = "NONE"
    return candidates, verdicts_map


def _context_to_dict(context: JudgmentContext | None) -> dict | None:
    if context is None:
        return None
    return {
        "previous_turn_id": context.previous_turn_id,
        "previous_step_id": context.previous_step_id,
        "previous_global_step": context.previous_global_step,
        "previous_result_snippet": context.previous_result_snippet,
        "current_turn_id": context.current_turn_id,
        "current_step_id": context.current_step_id,
        "current_global_step": context.current_global_step,
        "current_result_snippet": context.current_result_snippet,
        "gap_steps": context.gap_steps,
        "intervening_actions": [
            {
                "turn_id": a.turn_id,
                "step_id": a.step_id,
                "tool_name": a.tool_name,
                "description": a.description,
                "is_write": a.is_write,
            }
            for a in context.intervening_actions
        ],
        "tool_result_changed": context.tool_result_changed,
    }


def serialize_candidates_to_json(
    candidates: list[SemanticCandidate],
    session_id: str,
    model: str,
) -> str:
    """将候选清单序列化为 JSON 字符串(供 agent 消费)。

    JSON 顶层含 instructions(评审 A:任务/输出格式/判定准则/示例),
    告知 agent 用自身 LLM 判定每个候选并回填 verdict。
    """
    debated_count = sum(1 for c in candidates if c.is_debated)
    deterministic_count = len(candidates) - debated_count
    payload = {
        "session_id": session_id,
        "model": model,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_candidates": len(candidates),
        "debated_count": debated_count,
        "deterministic_count": deterministic_count,
        "instructions": {
            "task": (
                "You are reviewing AgentTrace's duplicate tool-call / invalid-param-retry "
                "candidates. For each candidate, judge whether the repeated call was truly "
                "redundant (true_redundant) or a legitimate polling/state-check (legitimate), "
                "or uncertain."
            ),
            "output_format": (
                'Write your verdicts to a JSON file with schema: { "verdicts": ['
                '{ "rule_id": ..., "fingerprint": ..., "turn_id": ..., "step_id": ..., '
                '"verdict": "true_redundant"|"legitimate"|"uncertain", '
                '"confidence": 0.0-1.0, "reason": "..." }] }'
            ),
            "criteria": [
                "If tool_result_changed=false and no intervening writes → likely true_redundant",
                "If tool_result_changed=true or intervening writes exist → likely legitimate",
                "If tool_result_changed=null (truncated/indeterminate) → use other signals; "
                "mark uncertain if ambiguous",
                "verdict is a semantic suggestion, NOT a hard assertion; causal_claim=NONE; "
                "do not change any savings numbers",
            ],
            "example_verdict": {
                "verdict": "true_redundant",
                "confidence": 0.85,
                "reason": "两次 list_sessions 调用间无写入且结果相同,确为冗余",
            },
        },
        "candidates": [
            {
                "rule_id": c.rule_id,
                "turn_id": c.turn_id,
                "step_id": c.step_id,
                "fingerprint": c.fingerprint,
                "tool_name": c.tool_name,
                "arguments": c.arguments,
                "is_debated": c.is_debated,
                "occurrence_index": c.occurrence_index,
                "total_occurrences": c.total_occurrences,
                "context": _context_to_dict(c.context),
            }
            for c in candidates
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
