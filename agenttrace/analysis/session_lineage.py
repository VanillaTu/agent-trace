"""跨会话 lineage 观测(analysis/session_lineage.py,A2)。

分析层会话级数据块,与 ContextHealth / TokenInvariant 同构,挂在
DiagnosisResult.session_lineage:
- 沿 header.parentSession 权威边构建跨会话 lineage 图,递归聚合子会话的
  token/工具/detector 信号回父会话(聚合观测值,非成本)。
- 区分两类子代:SUBAGENT(origin=="subagent")进主聚合 lineage_descendant_*;
  FORKED_SESSION(origin 为空但 parentSession 有值)进独立的 lineage_fork_descendant_*。
- 不做 Detector/Finding:不注册 ALL_DETECTORS / ALL_ATTRIBUTION_ENGINES,
  不进入 findings/attributions,不判因果、不做成本归因。
- 仅 enable_analysis=True 且传入 session_map 时由 pipeline Stage 3 调用;
  默认关闭 → 零影响。

确定性铁律:所有指标由 session_map 确定性计算;子代按 session_id 排序;
visited 防环;空/单会话 → 全零/None 块,不虚构数值。
记账口径:子会话 token 只归自己;祖辈只"看子树规模",禁止沿链再加总;
fork 不做 token 互斥/去重;不可解析 → 悬挂节点。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..core.canonical_trace import Trace


@dataclass
class SessionLineage:
    """跨会话 lineage 观测(分析层会话级数据块,非 Finding)。

    全部字段带默认值:空会话 / 单会话 / 无子代 → 全零/None,不虚构数值。
    None 语义 = not applicable(如无 subagent/descriptor 事件时 provider 为 None)。
    """

    # ── 自身指标(权威,可全局加总,不被任何祖先改写)──
    own_tokens: int = 0
    """该会话自身 token 合计(input+output,按 Step.usage.total_tokens())。"""

    own_steps: int = 0
    """该会话自身 step 数。"""

    own_tools: int = 0
    """该会话自身工具调用次数。"""

    # ── 子代聚合(SUBAGENT 后代递归,聚合观测值,非成本)──
    lineage_descendant_tokens: int = 0
    """SUBAGENT 后代的递归 token 合计(仅 origin=subagent 后代)。
    聚合观测值,非成本;不参与 wasted/cost 归因。"""

    lineage_descendant_steps: int = 0
    """SUBAGENT 后代的递归 step 合计。"""

    lineage_descendant_tools: int = 0
    """SUBAGENT 后代的递归工具调用合计。"""

    # ── FORKED_SESSION 子代聚合(独立层,默认不混入上面)──
    lineage_fork_descendant_tokens: int = 0
    """FORKED_SESSION 后代的递归 token 合计(可选展示,默认不与 subagent 混算)。"""

    lineage_fork_descendant_steps: int = 0
    """FORKED_SESSION 后代的递归 step 合计。"""

    lineage_fork_descendant_tools: int = 0
    """FORKED_SESSION 后代的递归工具调用合计。"""

    # ── 图拓扑 ──
    lineage_depth: int = 0
    """从本机根的图深度(结构化推导,沿 parentSession 链的最长路径)。"""

    parent_session_id: Optional[str] = None
    """该会话的父会话 id(来自 header.parentSession);None = 根。"""

    parent_category: str = "none"
    """父边类别:"subagent" | "forked_session" | "none"(无父)。"""

    # ── 子代计数 ──
    child_count: int = 0
    """直接子代总数(SUBAGENT + FORKED_SESSION)。"""

    subagent_child_count: int = 0
    """直接 SUBAGENT 子代数。"""

    fork_session_child_count: int = 0
    """直接 FORKED_SESSION 子代数。"""

    root_child_count: int = 0
    """子树中 ROOT 节点数(深度 0 节点);树结构下恒 1(自己根)或 0(非根)。"""

    # ── 形态标记(仅 SUBAGENT 有意义)──
    provider: Optional[str] = None
    """subagent/descriptor.provider:"fork" | "spawn" | None(非 subagent 或无 descriptor 事件)。"""

    mode: Optional[str] = None
    """subagent/descriptor.mode:"one-shot" | "continuable" | None。"""

    # ── 边界标记 ──
    unresolvable_edges: int = 0
    """不可解析的 parent→child 边数(父指向本机不存在的会话)。"""

    is_resolvable_subgraph: bool = True
    """本机可解析子图内成立;False 表示存在不可解析边。"""

    # ── 确定性元数据 ──
    total_sessions_in_graph: int = 0
    """参与图构建的会话总数(传入 session_map 的大小),供报告注明覆盖范围。"""


def _child_list(session_id: str, session_map: dict[str, Trace]) -> list[tuple[str, Trace]]:
    """找出 metadata["parentSession"] == session_id 的直接子代,按 session_id 排序。"""
    children = [
        (cid, ctrace)
        for cid, ctrace in session_map.items()
        if (ctrace.metadata or {}).get("parentSession") == session_id
    ]
    children.sort(key=lambda x: x[0])
    return children


def _child_category(ctrace: Trace) -> str:
    """子代类别:"subagent" | "forked_session" | "none"(无父)。"""
    cm = ctrace.metadata or {}
    origin = cm.get("origin")
    parent = cm.get("parentSession")
    if parent is None or parent == "":
        return "none"
    if origin == "subagent":
        return "subagent"
    # origin is None / 空串 / 未知值 → 保守按 forked_session
    return "forked_session"


def _lineage_depth(session_id: str, session_map: dict[str, Trace]) -> int:
    """沿 parentSession 链从根推导图深度(不用 delegationDepth)。"""
    depth = 0
    cur = session_id
    seen: set[str] = set()
    while cur is not None and cur not in seen:
        seen.add(cur)
        cur_trace = session_map.get(cur)
        if cur_trace is None:
            break
        cur_parent = (cur_trace.metadata or {}).get("parentSession")
        if cur_parent is None or cur_parent == "":
            break
        depth += 1
        cur = cur_parent
    return depth


def _descriptor_provider_mode(trace: Trace) -> tuple[Optional[str], Optional[str]]:
    """从 trace.events[] 取第一个 subagent/descriptor 的 provider / mode。"""
    for ev in trace.events:
        if ev.type == "subagent/descriptor":
            d = ev.data
            # provider 在真实数据为扁平字符串("fork"/"spawn");防御 dict 形式(设计曾言 provider.label)
            p = d.get("provider")
            if isinstance(p, dict):
                p = p.get("label")
            provider = p if p else None
            mode = d.get("mode")
            return provider, mode
    return None, None


def build_session_lineage(
    session_id: str,
    session_map: dict[str, Trace],
    visited: set[str] | None = None,
) -> SessionLineage:
    """从 session_id 出发,沿 header.parentSession 权威边聚合跨会话 lineage(纯函数,确定性)。

    - visited:递归防环用(树形安全网);顶层调用传 None(每次新 set)。
    - 子代按 session_id 排序保证确定性。
    - 每个节点自存子树和,禁止沿链再加总(后代仅计一次)。
    """
    trace = session_map.get(session_id)
    if trace is None:
        return SessionLineage(is_resolvable_subgraph=False)

    if visited is None:
        visited = set()
    if session_id in visited:
        return SessionLineage()  # 已访问过,不重复计数
    visited.add(session_id)

    metadata = trace.metadata or {}
    parent_session = metadata.get("parentSession") or None
    origin = metadata.get("origin")

    # 自身指标(权威,不被任何祖先改写)
    own_tokens = sum(s.usage.total_tokens() for s in trace.all_steps())
    own_steps = len(trace.all_steps())
    own_tools = len(trace.all_tool_calls())

    # 直接子代(按 session_id 排序)与分类
    children = _child_list(session_id, session_map)

    lineage_descendant_tokens = 0
    lineage_descendant_steps = 0
    lineage_descendant_tools = 0
    lineage_fork_descendant_tokens = 0
    lineage_fork_descendant_steps = 0
    lineage_fork_descendant_tools = 0

    for cid, ctrace in children:
        cat = _child_category(ctrace)
        child_sl = build_session_lineage(cid, session_map, visited)
        child_own_tokens = child_sl.own_tokens
        child_own_steps = child_sl.own_steps
        child_own_tools = child_sl.own_tools
        if cat == "subagent":
            # 递归累计到 SUBAGENT 聚合(子自身 + 子的后代)
            lineage_descendant_tokens += child_own_tokens + child_sl.lineage_descendant_tokens
            lineage_descendant_steps += child_own_steps + child_sl.lineage_descendant_steps
            lineage_descendant_tools += child_own_tools + child_sl.lineage_descendant_tools
        else:  # forked_session(含 UNKNOWN_ORIGIN 保守)
            lineage_fork_descendant_tokens += child_own_tokens + child_sl.lineage_fork_descendant_tokens
            lineage_fork_descendant_steps += child_own_steps + child_sl.lineage_fork_descendant_steps
            lineage_fork_descendant_tools += child_own_tools + child_sl.lineage_fork_descendant_tools

    # 形态标记(仅 subagent/descriptor 有值)
    provider, mode = _descriptor_provider_mode(trace)

    # 图深度(沿 parentSession 链,不用 delegationDepth)
    lineage_depth = _lineage_depth(session_id, session_map)

    # 父边类别
    if parent_session is None:
        parent_category = "none"
    elif origin == "subagent":
        parent_category = "subagent"
    else:
        parent_category = "forked_session"

    # 不可解析边:父指向本机不存在的会话
    unresolvable_edges = 1 if (parent_session is not None and parent_session not in session_map) else 0
    is_resolvable_subgraph = unresolvable_edges == 0

    # 计数
    child_count = len(children)
    subagent_child_count = sum(1 for cid, ctrace in children if _child_category(ctrace) == "subagent")
    fork_session_child_count = child_count - subagent_child_count
    root_child_count = 1 if parent_category == "none" else 0
    total_sessions_in_graph = len(session_map)

    return SessionLineage(
        own_tokens=own_tokens,
        own_steps=own_steps,
        own_tools=own_tools,
        lineage_descendant_tokens=lineage_descendant_tokens,
        lineage_descendant_steps=lineage_descendant_steps,
        lineage_descendant_tools=lineage_descendant_tools,
        lineage_fork_descendant_tokens=lineage_fork_descendant_tokens,
        lineage_fork_descendant_steps=lineage_fork_descendant_steps,
        lineage_fork_descendant_tools=lineage_fork_descendant_tools,
        lineage_depth=lineage_depth,
        parent_session_id=parent_session,
        parent_category=parent_category,
        child_count=child_count,
        subagent_child_count=subagent_child_count,
        fork_session_child_count=fork_session_child_count,
        root_child_count=root_child_count,
        provider=provider,
        mode=mode,
        unresolvable_edges=unresolvable_edges,
        is_resolvable_subgraph=is_resolvable_subgraph,
        total_sessions_in_graph=total_sessions_in_graph,
    )
