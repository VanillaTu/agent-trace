"""cross-session-lineage(A2)跨会话血缘观测测试。

覆盖 design.md 测试表 T1–T32:
1. 数据块默认值/空会话/单会话/边解析/descriptor
2. 两类子代区分(SBAGENT vs FORKED_SESSION)
3. 嵌套聚合防重复计数(T10/T11)
4. fork 不互斥(T12/T13)
5. 不可解析(T14/T15)、agent-start/senderSessionId 不作边(T16/T17)
6. 金钟罩(T18)、不进 registry(T19)、确定性(T20)
7. 防环(T21)、0-token(T22)、深度(T23)
8. 门控(T24/T25)、provider None(T26)、措辞(T27)、边界(T28)
9. parent_category(T29)、多子代计数(T30)、root_child_count(T31)、单 session_map(T32)
"""

from __future__ import annotations

import json
from pathlib import Path

from agenttrace.adapters.dsh_adapter import parse_dsh_jsonl
from agenttrace.analysis.session_lineage import SessionLineage, build_session_lineage
from agenttrace.attribution import ALL_ATTRIBUTION_ENGINES
from agenttrace.core.canonical_trace import (
    Step,
    ToolCall,
    Trace,
    TraceEvent,
    Turn,
    Usage,
)
from agenttrace.detectors import ALL_DETECTORS
from agenttrace.pipeline import diagnose
from agenttrace.report import render_report

GOLDEN_DIR = Path(__file__).parent / "golden"


# --------------------------------------------------------------------------
# 构造辅助
# --------------------------------------------------------------------------

def _trace(
    session_id,
    *,
    tokens_per_step=0,
    steps=0,
    tools=0,
    parent=None,
    origin=None,
    depth=0,
    events=None,
):
    """构造一个 Trace。

    - tokens_per_step * steps = own_tokens(usage.total_tokens() = input + 0)
    - own_steps = steps
    - own_tools = tools
    - parent / origin → trace.metadata
    """
    t = Trace(session_id=session_id)
    t.metadata = {
        "parentSession": parent,
        "origin": origin,
        "delegationDepth": depth,
    }
    if steps > 0:
        turn = Turn(turn_id=1)
        for i in range(steps):
            st = Step(step_id=i + 1, turn_id=1)
            st.usage = Usage(input_tokens=tokens_per_step, output_tokens=0)
            turn.steps.append(st)
        t.turns = [turn]
        for i in range(tools):
            turn.steps[0].tool_calls.append(
                ToolCall(call_id=f"c{i}", tool_name="t", arguments="{}")
            )
    t.events = events or []
    return t


def _descriptor(provider="spawn", mode="one-shot"):
    return TraceEvent(
        type="subagent/descriptor",
        data={"provider": provider, "mode": mode, "label": "x"},
    )


def _parse_jsonl(lines, tmp_path):
    p = tmp_path / "session.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return parse_dsh_jsonl(str(p))


# --------------------------------------------------------------------------
# T1–T3: 默认值 / 空会话 / 单会话
# --------------------------------------------------------------------------

def test_session_lineage_defaults_all_zero():
    sl = SessionLineage()
    assert sl.own_tokens == 0
    assert sl.own_steps == 0
    assert sl.own_tools == 0
    assert sl.lineage_descendant_tokens == 0
    assert sl.lineage_fork_descendant_tokens == 0
    assert sl.lineage_depth == 0
    assert sl.parent_session_id is None
    assert sl.parent_category == "none"
    assert sl.child_count == 0
    assert sl.provider is None
    assert sl.mode is None
    assert sl.unresolvable_edges == 0
    assert sl.is_resolvable_subgraph is True
    assert sl.root_child_count == 0
    assert sl.total_sessions_in_graph == 0


def test_empty_session_zero_block():
    t = Trace(session_id="empty")
    sl = build_session_lineage("empty", {"empty": t})
    assert sl.own_tokens == 0
    assert sl.own_steps == 0
    assert sl.own_tools == 0
    assert sl.lineage_descendant_tokens == 0
    assert sl.parent_category == "none"
    assert sl.is_resolvable_subgraph is True


def test_single_session_no_parent_no_child():
    t = _trace("solo", tokens_per_step=100, steps=2, tools=3)
    sl = build_session_lineage("solo", {"solo": t})
    assert sl.own_tokens == 200
    assert sl.own_steps == 2
    assert sl.own_tools == 3
    assert sl.lineage_descendant_tokens == 0
    assert sl.child_count == 0
    assert sl.parent_category == "none"
    assert sl.provider is None
    assert sl.mode is None


# --------------------------------------------------------------------------
# T4: adapter 解析 session 头 lineage 字段
# --------------------------------------------------------------------------

def test_parent_session_edge_parsed(tmp_path):
    line = json.dumps(
        {
            "type": "session",
            "id": "s1",
            "parentSession": "p1",
            "origin": "subagent",
            "delegationDepth": 1,
        }
    )
    t = _parse_jsonl([line], tmp_path)
    assert t.session_id == "s1"
    assert t.metadata["parentSession"] == "p1"
    assert t.metadata["origin"] == "subagent"
    assert t.metadata["delegationDepth"] == 1


# --------------------------------------------------------------------------
# T5: descriptor provider/mode
# --------------------------------------------------------------------------

def test_subagent_descriptor_provider():
    t = _trace("wk", steps=1, events=[_descriptor("fork", "one-shot")])
    sl = build_session_lineage("wk", {"wk": t})
    assert sl.provider == "fork"
    assert sl.mode == "one-shot"


# --------------------------------------------------------------------------
# T6–T8: 两类子代分类 + ROOT
# --------------------------------------------------------------------------

def test_subagent_child_classification():
    p = _trace("P", parent=None, origin=None)
    c = _trace("child", parent="P", origin="subagent", tokens_per_step=100, steps=2)
    sl = build_session_lineage("P", {"P": p, "child": c})
    assert sl.subagent_child_count == 1
    assert sl.fork_session_child_count == 0
    assert sl.lineage_descendant_tokens == 200
    assert sl.lineage_fork_descendant_tokens == 0


def test_forked_session_child_classification():
    p = _trace("P", parent=None, origin=None)
    c = _trace("fork", parent="P", origin=None, tokens_per_step=100, steps=2)
    sl = build_session_lineage("P", {"P": p, "fork": c})
    assert sl.fork_session_child_count == 1
    assert sl.subagent_child_count == 0
    assert sl.lineage_fork_descendant_tokens == 200
    assert sl.lineage_descendant_tokens == 0


def test_root_classification():
    t = _trace("R", parent=None, origin=None, tokens_per_step=50, steps=1)
    sl = build_session_lineage("R", {"R": t})
    assert sl.parent_category == "none"
    assert sl.parent_session_id is None
    assert sl.root_child_count == 1


# --------------------------------------------------------------------------
# T9–T11: 聚合(简单 / 嵌套 / 防重复)
# --------------------------------------------------------------------------

def test_simple_parent_child_aggregation():
    p = _trace("P", parent=None, origin=None, tokens_per_step=10, steps=1)
    c = _trace("child", parent="P", origin="subagent", tokens_per_step=100, steps=1)
    sl = build_session_lineage("P", {"P": p, "child": c})
    assert sl.lineage_descendant_tokens == 100
    assert sl.lineage_descendant_steps == 1


def test_nested_three_level_aggregation():
    root = _trace("root", parent=None, origin=None, tokens_per_step=10, steps=1)
    mid = _trace("mid", parent="root", origin="subagent", tokens_per_step=20, steps=1)
    leaf = _trace("leaf", parent="mid", origin="subagent", tokens_per_step=40, steps=1)
    sl = build_session_lineage("root", {"root": root, "mid": mid, "leaf": leaf})
    # root 的 lineage_descendant_tokens = mid.own(20) + leaf.own(40) = 60(各恰好 1 次)
    assert sl.lineage_descendant_tokens == 60
    assert sl.lineage_descendant_steps == 2


def test_anti_double_count_three_level():
    root = _trace("root", parent=None, origin=None, tokens_per_step=10, steps=1)
    mid = _trace("mid", parent="root", origin="subagent", tokens_per_step=20, steps=1)
    leaf = _trace("leaf", parent="mid", origin="subagent", tokens_per_step=40, steps=1)
    sl = build_session_lineage("root", {"root": root, "mid": mid, "leaf": leaf})
    # 验证 leaf.own(40) 在 root.lineage_descendant_tokens 中只出现 1 次
    assert sl.lineage_descendant_tokens == 20 + 40
    # 且子代自身的 descendant 各自独立存储
    mid_sl = build_session_lineage("mid", {"root": root, "mid": mid, "leaf": leaf})
    assert mid_sl.lineage_descendant_tokens == 40
    leaf_sl = build_session_lineage("leaf", {"root": root, "mid": mid, "leaf": leaf})
    assert leaf_sl.lineage_descendant_tokens == 0


# --------------------------------------------------------------------------
# T12–T13: fork 分离 / 不去重
# --------------------------------------------------------------------------

def test_fork_not_mixed_into_subagent_aggregation():
    p = _trace("P", parent=None, origin=None, tokens_per_step=10, steps=1)
    sub = _trace("sub", parent="P", origin="subagent", tokens_per_step=100, steps=1)
    fork = _trace("fork", parent="P", origin=None, tokens_per_step=200, steps=1)
    sl = build_session_lineage("P", {"P": p, "sub": sub, "fork": fork})
    # fork 的 token 不进入 subagent 聚合
    assert sl.lineage_descendant_tokens == 100
    assert sl.lineage_fork_descendant_tokens == 200
    assert sl.subagent_child_count == 1
    assert sl.fork_session_child_count == 1


def test_fork_no_token_dedup():
    p = _trace("P", parent=None, origin=None, tokens_per_step=1000, steps=1)
    fork = _trace("fork", parent="P", origin=None, tokens_per_step=500, steps=1)
    # fork 子代 token 与父独立(不做互斥/去重),各自 own_tokens 独立
    p_sl = build_session_lineage("P", {"P": p, "fork": fork})
    fork_sl = build_session_lineage("fork", {"P": p, "fork": fork})
    assert p_sl.own_tokens == 1000
    assert fork_sl.own_tokens == 500
    assert p_sl.lineage_fork_descendant_tokens == 500


# --------------------------------------------------------------------------
# T14–T15: 不可解析
# --------------------------------------------------------------------------

def test_unresolvable_parent_graceful():
    t = _trace("child", parent="ghost-session", origin="subagent", tokens_per_step=100, steps=1)
    sl = build_session_lineage("child", {"child": t})
    assert sl.unresolvable_edges == 1
    assert sl.is_resolvable_subgraph is False
    assert sl.parent_session_id == "ghost-session"
    assert sl.parent_category == "subagent"


def test_unresolvable_child_omitted():
    p = _trace("P", parent=None, origin=None, tokens_per_step=10, steps=1)
    sl = build_session_lineage("P", {"P": p})
    # 子会话不在 session_map → 不参与聚合,不计入 child_count
    assert sl.child_count == 0
    assert sl.lineage_descendant_tokens == 0


# --------------------------------------------------------------------------
# T16–T17: agent-start / senderSessionId 不作边
# --------------------------------------------------------------------------

def test_agent_start_not_lineage_edge():
    # P 有 agent-start(childId="C"),但 C 的 parentSession=None(不是 P 的子)
    p = _trace(
        "P", parent=None, origin=None, steps=1,
        events=[TraceEvent(type="tool-workflow/agent-start", data={"childId": "C"})],
    )
    c = _trace("C", parent=None, origin=None)
    sl = build_session_lineage("P", {"P": p, "C": c})
    # agent-start.childId 不产生 lineage 边
    assert sl.child_count == 0
    assert sl.subagent_child_count == 0


def test_sender_session_id_not_lineage_edge():
    p = _trace(
        "P", parent=None, origin=None, steps=1,
        events=[TraceEvent(type="user/message", data={"source": {"senderSessionId": "Q"}})],
    )
    q = _trace("Q", parent=None, origin=None)
    sl = build_session_lineage("P", {"P": p, "Q": q})
    # senderSessionId 不产生 lineage 边
    assert sl.child_count == 0


# --------------------------------------------------------------------------
# T18: 金钟罩(逐字节)
# --------------------------------------------------------------------------

def test_additive_golden_shield():
    from tests.golden.golden_report import build_comprehensive_trace
    t = build_comprehensive_trace()
    result = diagnose(t)  # enable_analysis 默认 False
    report = render_report(result.trace, result.findings, result.attributions)
    expected = (GOLDEN_DIR / "v05_baseline_report.md").read_text(encoding="utf-8")
    assert report == expected


# --------------------------------------------------------------------------
# T19: 不进 registry
# --------------------------------------------------------------------------

def test_not_in_all_detectors():
    assert all(d.rule_id != "session-lineage" for d in ALL_DETECTORS)
    assert "session-lineage" not in ALL_ATTRIBUTION_ENGINES
    assert isinstance(build_session_lineage("x", {"x": _trace("x")}), SessionLineage)


# --------------------------------------------------------------------------
# T20: 确定性
# --------------------------------------------------------------------------

def test_determinism():
    session_map = {
        "root": _trace("root", parent=None, origin=None, tokens_per_step=10, steps=1),
        "mid": _trace("mid", parent="root", origin="subagent", tokens_per_step=20, steps=1),
        "leaf": _trace("leaf", parent="mid", origin="subagent", tokens_per_step=40, steps=1),
    }
    sl1 = build_session_lineage("root", session_map)
    sl2 = build_session_lineage("root", session_map)
    assert sl1 == sl2


# --------------------------------------------------------------------------
# T21: 防环
# --------------------------------------------------------------------------

def test_cycle_detection():
    # A→B→A 环:不无限递归
    a = _trace("A", parent="B", origin="subagent", tokens_per_step=10, steps=1)
    b = _trace("B", parent="A", origin="subagent", tokens_per_step=20, steps=1)
    session_map = {"A": a, "B": b}
    sl = build_session_lineage("A", session_map)  # 不抛 RecursionError
    assert isinstance(sl, SessionLineage)


# --------------------------------------------------------------------------
# T22: 0-token 子代理
# --------------------------------------------------------------------------

def test_zero_token_subagent():
    p = _trace("P", parent=None, origin=None, tokens_per_step=100, steps=1)
    c = _trace("child", parent="P", origin="subagent", tokens_per_step=0, steps=1)
    sl = build_session_lineage("P", {"P": p, "child": c})
    assert sl.lineage_descendant_tokens == 0
    assert sl.subagent_child_count == 1
    c_sl = build_session_lineage("child", {"P": p, "child": c})
    assert c_sl.own_tokens == 0  # int 0,不是 None


# --------------------------------------------------------------------------
# T23: 深度(结构推导,不用 delegationDepth)
# --------------------------------------------------------------------------

def test_lineage_depth_structural():
    root = _trace("root", parent=None, origin=None, depth=0, steps=1)
    mid = _trace("mid", parent="root", origin="subagent", depth=1, steps=1)
    leaf = _trace("leaf", parent="mid", origin="subagent", depth=2, steps=1)
    session_map = {"root": root, "mid": mid, "leaf": leaf}
    assert build_session_lineage("root", session_map).lineage_depth == 0
    assert build_session_lineage("mid", session_map).lineage_depth == 1
    assert build_session_lineage("leaf", session_map).lineage_depth == 2


# --------------------------------------------------------------------------
# T24–T25: 门控
# --------------------------------------------------------------------------

def test_enable_analysis_gate():
    t = _trace("solo", tokens_per_step=100, steps=1)
    result = diagnose(t)  # enable_analysis=False
    assert result.session_lineage is None
    report = render_report(result.trace, result.findings, result.attributions)
    assert "跨会话 Lineage" not in report


def test_enable_analysis_without_session_map():
    t = _trace("solo", tokens_per_step=100, steps=1)
    result = diagnose(t, enable_analysis=True)  # session_map=None
    assert result.session_lineage is None
    # 不报错
    report = render_report(
        result.trace, result.findings, result.attributions,
        enable_analysis=True, profile=result.profile,
        context_health=result.context_health, token_invariant=result.token_invariant,
    )
    assert "跨会话 Lineage" not in report


# --------------------------------------------------------------------------
# T26: provider None 非 subagent
# --------------------------------------------------------------------------

def test_provider_none_for_non_subagent():
    t = _trace("R", parent=None, origin=None)  # 根,无 descriptor 事件
    sl = build_session_lineage("R", {"R": t})
    assert sl.provider is None
    assert sl.mode is None


# --------------------------------------------------------------------------
# T27–T28: 报告措辞 / 边界
# --------------------------------------------------------------------------

def test_report_lineage_block_wording():
    p = _trace("P", parent=None, origin=None, tokens_per_step=10, steps=1)
    sub = _trace("sub", parent="P", origin="subagent", tokens_per_step=100, steps=1)
    t = _trace("P", parent=None, origin=None, tokens_per_step=10, steps=1)
    session_map = {"P": p, "sub": sub}
    sl = build_session_lineage("P", session_map)
    report = render_report(t, [], [], enable_analysis=True, session_lineage=sl)
    block_start = report.index("跨会话 Lineage")
    block = report[block_start:]
    assert "token 规模观测,非成本" in block
    assert "浪费" not in block
    assert "Total wasted" not in block
    assert "cost" not in block.lower()
    assert "causal" not in block.lower()


def test_report_lineage_boundary_note():
    t = _trace("solo", parent=None, origin=None, tokens_per_step=10, steps=1)
    session_map = {"solo": t}
    sl = build_session_lineage("solo", session_map)
    report = render_report(t, [], [], enable_analysis=True, session_lineage=sl)
    assert "本机可解析子图内成立" in report
    assert "覆盖 1 会话" in report


# --------------------------------------------------------------------------
# T29: parent_category 取值
# --------------------------------------------------------------------------

def test_parent_category_values():
    # subagent 子
    sub = _trace("sub", parent="P", origin="subagent")
    assert build_session_lineage("sub", {"sub": sub}).parent_category == "subagent"
    # forked-session 子(origin None 但有 parent)
    fork = _trace("fork", parent="P", origin=None)
    assert build_session_lineage("fork", {"fork": fork}).parent_category == "forked_session"
    # 根
    root = _trace("R", parent=None, origin=None)
    assert build_session_lineage("R", {"R": root}).parent_category == "none"


# --------------------------------------------------------------------------
# T30: 多子代计数
# --------------------------------------------------------------------------

def test_multiple_children_counts():
    p = _trace("P", parent=None, origin=None, tokens_per_step=10, steps=1)
    session_map = {
        "P": p,
        "s1": _trace("s1", parent="P", origin="subagent", tokens_per_step=10, steps=1),
        "s2": _trace("s2", parent="P", origin="subagent", tokens_per_step=10, steps=1),
        "s3": _trace("s3", parent="P", origin="subagent", tokens_per_step=10, steps=1),
        "f1": _trace("f1", parent="P", origin=None, tokens_per_step=10, steps=1),
        "f2": _trace("f2", parent="P", origin=None, tokens_per_step=10, steps=1),
    }
    sl = build_session_lineage("P", session_map)
    assert sl.child_count == 5
    assert sl.subagent_child_count == 3
    assert sl.fork_session_child_count == 2


# --------------------------------------------------------------------------
# T31: root_child_count 语义
# --------------------------------------------------------------------------

def test_root_child_count_semantics():
    root = _trace("root", parent=None, origin=None)
    assert build_session_lineage("root", {"root": root}).root_child_count == 1
    sub = _trace("sub", parent="root", origin="subagent")
    session_map = {"root": root, "sub": sub}
    assert build_session_lineage("sub", session_map).root_child_count == 0
    assert build_session_lineage("root", session_map).root_child_count == 1


# --------------------------------------------------------------------------
# T32: 单 session_map 部分可观测(评审 C 方案2)
# --------------------------------------------------------------------------

def test_single_session_map_partial_observable():
    # 单元素 map:当前会话有父(不在 map 中)
    c = _trace("child", parent="parent-not-loaded", origin="subagent", tokens_per_step=100, steps=2)
    sl = build_session_lineage("child", {"child": c})
    # own_* 有值
    assert sl.own_tokens == 200
    # parent 据 header 可观测
    assert sl.parent_session_id == "parent-not-loaded"
    assert sl.parent_category == "subagent"
    # 子不可解析(不在 map)→ lineage 聚合为 0
    assert sl.lineage_descendant_tokens == 0
    assert sl.child_count == 0
    assert sl.total_sessions_in_graph == 1
