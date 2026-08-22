"""DSH JSONL → Canonical Trace 适配器。

从真实 DSH 会话日志(解压后的 JSONL)解析出 Canonical Trace。

已用真实数据验证的 schema 映射:
- session 头 → Trace.session_id / model / metadata
- turn/start + turn/end → Turn 边界
- step/start + step/end → Step 边界
- assistant/chunk type=usage → Step.usage(per-step 精确 token 计量)⭐
- assistant/message content[type=tool-call] → Step.tool_calls(可多个)
- tool/result(按 callId 匹配)→ ToolCall.result / is_error / truncated
- reasoning-chunks / assistant/message content[type=reasoning] → Step.reasoning
- text-chunks / assistant/message content[type=text] → Step.text

注意:
- 原始 JSONL 可能有中文乱码(编码问题),逐行解析需容错
- 事件按 seq 排序(日志本身有序)
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Optional

from ..core.canonical_trace import Step, ToolCall, Trace, TraceEvent, Turn, Usage

# 独立事件类型(不塞进 step,进入 Trace.events[])
# 源码 KNOWN_SESSION_EVENT_TYPES 中,不属于 step/turn 边界的非 surface 事件
STANDALONE_EVENT_TYPES = {
    "compaction/start",
    "compaction/end",
    "compaction/summary",
    "compaction/prune",
    "llm/retry",
    "llm/retry-started",
    "tool-workflow/agent-start",
    "tool-workflow/agent-end",
    "tool-workflow/run-start",
    "tool-workflow/run-end",
    "subagent/descriptor",
    "hook/invoked",
    "hook/result",
    "approval/asked",
    "approval/decided",
    "plan/mode",
    "schedule/change",
    "goal/change",
    "feedback/record",
    "session/title-llm-request",
    "todo/write",
}

# finish 事件(从 assistant/chunk 提取,type=finish):模型调用终态
# kind: error(失败)/ success(成功)/ max-tokens / aborted
# 用于重建 retry lifecycle 与可靠性观测
FINISH_EVENT_PREFIX = "llm/finish/"


class DSHParseError(Exception):
    pass


def _usage_equal(u1: dict, u2: dict) -> bool:
    """比较两份 usage dict 的关键字段是否数值一致(all-pairs 判定用)。

    已知局限:不比较 cacheWriteTokens(当前 Defined+Unobserved,真实样本未见)。
    若 DSH 未来开始上报该字段,需将其加入 keys 元组。
    """
    keys = ("inputTokens", "outputTokens", "cacheReadTokens", "reasoningTokens")
    return all(u1.get(k) == u2.get(k) for k in keys)


def parse_dsh_jsonl(file_path: str) -> Trace:
    """解析 DSH 会话 JSONL 为 Canonical Trace。file_path 为已解压的 JSONL。"""
    trace = Trace(session_id="")
    turns: dict[int, Turn] = {}
    steps: dict[tuple[int, int], Step] = {}  # (turn, step) -> Step
    call_to_step: dict[str, tuple[int, int]] = {}  # callId -> (turn, step)
    model = ""
    session_id = ""
    # token 双写观测:按 (turn, step) 记录 [(source, usage_dict)]
    # source = "chunk"(assistant/chunk type=usage)/ "message"(assistant/message 的 data.usage)
    _usage_sources: dict[tuple[int, int], list[tuple[str, dict]]] = defaultdict(list)

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue  # 容错:跳过损坏行

            etype = ev.get("type")
            data = ev.get("data", {})
            seq = ev.get("seq", 0)

            # 独立事件层:不塞进 step,直接进 Trace.events
            if etype in STANDALONE_EVENT_TYPES:
                trace.events.append(
                    TraceEvent(
                        type=etype,
                        time=ev.get("time", 0),
                        seq=seq,
                        turn_id=data.get("turn"),
                        step_id=data.get("step"),
                        data=data,
                    )
                )

            if etype == "session":
                session_id = ev.get("id", "")
                trace.metadata = {"cwd": data.get("cwd"), "agentPreset": data.get("agentPreset")}
                trace.session_id = session_id

            elif etype == "turn/start":
                tid = data.get("turn")
                turns.setdefault(tid, Turn(turn_id=tid, start_time=ev.get("time", 0)))

            elif etype == "turn/end":
                tid = data.get("turn")
                if tid in turns:
                    turns[tid].end_time = ev.get("time", 0)

            elif etype == "step/start":
                tid, sid = data.get("turn"), data.get("step")
                turn = turns.setdefault(tid, Turn(turn_id=tid, start_time=ev.get("time", 0)))
                steps[(tid, sid)] = Step(
                    step_id=sid, turn_id=tid, start_time=ev.get("time", 0)
                )
                # 确保 step 挂到 turn
                if not any(s.step_id == sid for s in turn.steps):
                    turn.steps.append(steps[(tid, sid)])

            elif etype == "step/end":
                tid, sid = data.get("turn"), data.get("step")
                if (tid, sid) in steps:
                    steps[(tid, sid)].end_time = ev.get("time", 0)

            elif etype == "assistant/chunk":
                chunk = data.get("chunk", {})
                ctype = chunk.get("type")
                tid, sid = data.get("turn"), data.get("step")
                key = (tid, sid)
                if key not in steps:
                    steps[key] = Step(step_id=sid, turn_id=tid)
                    turns.setdefault(tid, Turn(turn_id=tid)).steps.append(steps[key])
                st = steps[key]
                if ctype == "usage":
                    u = chunk.get("usage", {})
                    # 5 字段:前 3 Observed,后 2 Defined+Unobserved(缺失用 None,missing≠0)
                    st.usage = Usage(
                        input_tokens=u.get("inputTokens", 0),
                        output_tokens=u.get("outputTokens", 0),
                        cache_read_tokens=u.get("cacheReadTokens", 0),
                        cache_write_tokens=u.get("cacheWriteTokens"),  # None if absent
                        reasoning_tokens=u.get("reasoningTokens"),  # None if absent
                    )
                    # 双写观测:记录 chunk 来源 usage
                    _usage_sources[(tid, sid)].append(("chunk", u))
                elif ctype == "reasoning":
                    st.reasoning += chunk.get("reasoning", "")
                elif ctype == "text":
                    st.text += chunk.get("text", "")
                elif ctype == "finish":
                    # 模型调用终态:重建 retry lifecycle 用
                    reason = chunk.get("reason", {})
                    kind = reason.get("kind", "unknown") if isinstance(reason, dict) else "unknown"
                    failure = reason.get("failure", {}) if isinstance(reason, dict) else {}
                    trace.events.append(
                        TraceEvent(
                            type=f"{FINISH_EVENT_PREFIX}{kind}",
                            time=ev.get("time", 0),
                            seq=seq,
                            turn_id=tid,
                            step_id=sid,
                            data={
                                "reason": reason,
                                "error_code": failure.get("code") if isinstance(failure, dict) else None,
                                "error_message": (
                                    failure.get("message") if isinstance(failure, dict) else None
                                ),
                            },
                        )
                    )

            elif etype == "reasoning-chunks":
                tid, sid = data.get("turn"), data.get("step")
                key = (tid, sid)
                if key in steps:
                    steps[key].reasoning += data.get("reasoning", "")

            elif etype == "assistant/message":
                msg = data.get("message", {})
                source = msg.get("source", {})
                if source.get("kind") == "model":
                    model = source.get("model", model)
                tid, sid = data.get("turn"), data.get("step")
                key = (tid, sid)
                if key not in steps:
                    steps[key] = Step(step_id=sid, turn_id=tid)
                    turns.setdefault(tid, Turn(turn_id=tid)).steps.append(steps[key])
                st = steps[key]
                # 双写观测:assistant/message 的 usage 在 data.usage(与 message 平级,
                # 非 message.usage)。真实数据核验(105 会话)确认该路径。
                d_usage = data.get("usage")
                if isinstance(d_usage, dict):
                    _usage_sources[(tid, sid)].append(("message", d_usage))
                for c in msg.get("content", []):
                    ctype = c.get("type")
                    if ctype == "tool-call":
                        tc = ToolCall(
                            call_id=c.get("id", ""),
                            tool_name=c.get("name", ""),
                            arguments=c.get("arguments", ""),
                        )
                        st.tool_calls.append(tc)
                        call_to_step[tc.call_id] = (tid, sid)
                    elif ctype == "reasoning":
                        st.reasoning += c.get("text", "")
                    elif ctype == "text":
                        st.text += c.get("text", "")

            elif etype == "tool/result":
                msg = data.get("message", {})
                source = msg.get("source", {})
                content = msg.get("content", [])
                call_id = None
                result_text = ""
                is_error = False
                truncated = False
                meta = data.get("meta", {})
                truncated = bool(meta.get("truncated", False))
                for c in content:
                    if c.get("type") == "tool-result":
                        call_id = c.get("toolCallId")
                        is_error = bool(c.get("isError"))
                        for cc in c.get("content", []):
                            if cc.get("type") == "text":
                                result_text += cc.get("text", "")
                if call_id and call_id in call_to_step:
                    tid, sid = call_to_step[call_id]
                    for tc in steps[(tid, sid)].tool_calls:
                        if tc.call_id == call_id:
                            tc.result = result_text
                            tc.is_error = is_error
                            tc.truncated = truncated
                            break

    # 双写观测事件生成:对 ≥2 来源的 (turn,step),all-pairs 数值一致 → duplicate,
    # 否则 → inconsistent。只追加观测事件,不改 Step.usage 获胜/遍历/去重顺序。
    for (tid, sid), sources in _usage_sources.items():
        if len(sources) < 2:
            continue  # 单来源:不报
        usages = [u for _, u in sources]
        if all(_usage_equal(usages[0], u) for u in usages[1:]):
            total = usages[0].get("inputTokens", 0) + usages[0].get("outputTokens", 0)
            trace.events.append(
                TraceEvent(
                    type="token/usage-duplicate",
                    turn_id=tid,
                    step_id=sid,
                    data={
                        "source_count": len(sources),
                        "total_tokens": total,
                        "sources": [s for s, _ in sources],
                    },
                )
            )
        else:
            trace.events.append(
                TraceEvent(
                    type="token/usage-inconsistent",
                    turn_id=tid,
                    step_id=sid,
                    data={
                        "source_count": len(sources),
                        "sources": [s for s, _ in sources],
                    },
                )
            )

    trace.model = model
    # 按 turn_id 排序 turns,按 step_id 排序 steps
    trace.turns = [turns[k] for k in sorted(turns.keys())]
    for t in trace.turns:
        t.steps.sort(key=lambda s: s.step_id)
    return trace


def _decompress_zstd(zstd_path: Path) -> str:
    """解压 zstd 到临时 jsonl,返回临时文件路径。

    优先走 Python 内置 zstandard 库(无外部二进制依赖,DSH 换机可用);
    库不可用时回退到系统 zstd 命令行(兼容旧环境)。
    """
    import tempfile

    tmp = tempfile.mktemp(suffix=".jsonl")
    try:
        try:
            import zstandard  # type: ignore

            with zstd_path.open("rb") as f:
                dctx = zstandard.ZstdDecompressor()
                with dctx.stream_reader(f) as reader, open(tmp, "wb") as out:
                    out.write(reader.read())
        except ImportError:
            import shutil
            import subprocess

            zstd = shutil.which("zstd")
            if not zstd:
                raise DSHParseError(
                    "zstandard 库不可用且系统 zstd 命令未安装,无法解压会话日志"
                )
            subprocess.run(
                [zstd, "-d", "-f", str(zstd_path), "-o", tmp],
                capture_output=True,
                check=True,
            )
        return tmp
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_dsh_session(session_dir: str) -> Trace:
    """从 DSH 会话目录加载:自动解压 zstd 并解析。

    session_dir 指向包含 session.jsonl.zstd 的目录。
    """
    zstd_path = Path(session_dir) / "session.jsonl.zstd"
    if not zstd_path.exists():
        raise DSHParseError(f"not found: {zstd_path}")

    tmp = _decompress_zstd(zstd_path)
    try:
        return parse_dsh_jsonl(tmp)
    finally:
        Path(tmp).unlink(missing_ok=True)


# ---- 会话发现(DSH 目录布局探测)----
# 脆弱点 B:早期把 ~/.dsh/sessions/<转义 cwd>/<session-id>/session.jsonl.zstd 硬编码。
# 这里抽成可配置 + 可探测:DSH 改布局时只需改这里,调用方(CLI/脚本)不用动。


def _default_sessions_root() -> Path:
    """DSH 会话根目录。优先级:DSH_SESSIONS_DIR 环境变量 > ~/.dsh/sessions。"""
    env = os.environ.get("DSH_SESSIONS_DIR")
    if env:
        return Path(env)
    return Path.home() / ".dsh" / "sessions"


def discover_sessions(root: str | None = None) -> list[dict]:
    """扫描 DSH 会话根目录,返回可用会话元数据列表。

    每个元素:{"session_id", "session_dir", "has_zstd"}。
    root 默认用 _default_sessions_root;不存在则返回空列表。
    """
    base = Path(root) if root else _default_sessions_root()
    if not base.exists():
        return []
    sessions: list[dict] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        # 布局:根/<cwd 组>/<session-id>/session.jsonl.zstd
        # 兼容:session.jsonl.zstd 直接放在根下(无 cwd 组)的情形
        for cand in (child / "session.jsonl.zstd",):
            if cand.exists():
                sessions.append(
                    {
                        "session_id": child.name,
                        "session_dir": str(child),
                        "has_zstd": True,
                    }
                )
                break
        else:
            for sub in sorted(child.iterdir()):
                if sub.is_dir() and (sub / "session.jsonl.zstd").exists():
                    sessions.append(
                        {
                            "session_id": sub.name,
                            "session_dir": str(sub),
                            "has_zstd": True,
                        }
                    )
    return sessions
