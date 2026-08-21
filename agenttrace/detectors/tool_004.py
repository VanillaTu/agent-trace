"""TOOL-004 Invalid-Param Retry 检测器(无效参数重试)。

定义(design detector-tool-004 D1/D2):
    工具调用因参数错误(invalid arguments / missing required / invalid_request,
    或"缺必需参数"的空参数代理)失败后,Agent 以同类调用(同 tool_name 或同
    call_id)重试并成功。这类失败 attempt 是"可避免的失败尝试",标记为
    kind=flag 的候选缺陷模式;失败 attempt 无 usage → **不估算成本**
    (tokens=not applicable,归因边界铁律)。

触发规则(D1,确定性,规则层):
    一个 ToolCall 是"参数错误失败 attempt"当且仅当:
    1. tc.is_error 为真,且 result 文本(小写)命中 PARAM_ERROR_KEYWORDS;或
    2. tc.is_error 为真,且 arguments 为空(空串 / "{}" / "null"),
       即"缺必需参数"的确定性代理;仅对"非无状态"工具生效
       (无状态工具合法恒为空参,若因网络/配额等非参数原因报错不应误判)。

重试配对(D2):
    对每个失败 attempt E,在**后续**调用中找成功重试 S(is_error=False):
    - call_id 层:S.call_id == E.call_id(call 身份同一)→ confidence 0.95
    - adjacent_step 层:S.tool_name == E.tool_name 且同 turn,且全局序号差
      1 ≤ diff ≤ RETRY_STEP_WINDOW(默认 1)→ 显式关键词 0.85 / empty_args 0.70
    硬约束:
    - 不配对同 step 内"靠后调用"(同一 assistant 消息多 tool-call 是并行,M2)
    - 不配对跨 turn(M5:turn 末失败与下一 turn 首成功中间隔着用户新消息)
    - 不要求参数一致(缺参→补参,参数本就不一致——TOOL-001 漏检的根因)

全部纯确定性:无随机、无时间、无外部服务。
"""

from __future__ import annotations

from ..core.canonical_trace import Trace
from .base import Detector, EvidenceChain, Finding
from .tool_001 import STATELESS_TOOLS  # 复用:空参代理仅对"非无状态"工具生效

# 参数错误关键词(design D1 冻结,按原顺序;大小写不敏感子串匹配)。
# 真实样本锚点:BL-001 证据链的 `Error: invalid arguments: missing required
# property "text"` → "invalid arguments" / "missing required" 为已核验词;
# 其余为 proposal 同族的最小扩展。扩展前需先用真实 tool/result 错误文本核验。
PARAM_ERROR_KEYWORDS = (
    "invalid argument",      # proposal 显式:invalid arguments
    "missing required",      # proposal 显式:missing required (argument/parameter/field)
    "invalid_request",       # proposal 显式:invalid_request / invalid_request_error
    "invalid request",       # 同族:invalid request error
    "invalid parameter",
    "required parameter",
    "required argument",
    "missing parameter",
    "missing argument",
    "unexpected argument",
    "unexpected keyword",
)

# "相邻 step" 的确定性落点(可配置;待真实 BL-001 证据链 step-gap 分布校准)
RETRY_STEP_WINDOW = 1

# 置信度三档(D3,纯由证据类型推导):
# call_id 身份同一 = 0.95;adjacent_step + 显式关键词 = 0.85;
# adjacent_step + 仅空参数代理 = 0.70(可能被误读为其它错误)
CALL_ID_CONFIDENCE = 0.95
ADJACENT_KEYWORD_CONFIDENCE = 0.85
ADJACENT_EMPTY_ARGS_CONFIDENCE = 0.70


def _is_empty_args(arguments) -> bool:
    """判定参数字符串是否"为空"(缺必需参数的确定性代理)。"""
    return arguments is None or arguments.strip() in ("", "{}", "null", "None")


def _match_param_error(tc) -> str | None:
    """判定一个 ToolCall 是否为"参数错误失败 attempt",返回 error_pattern。

    命中显式关键词 → 返回命中词;仅空参数代理(非无状态工具)→ "empty_args";
    否则 None。
    """
    if not tc.is_error:
        return None
    text = (tc.result or "").lower()
    for kw in PARAM_ERROR_KEYWORDS:
        if kw in text:
            return kw
    # 空参代理:仅当工具"非无状态"时作为参数错误候选。
    # 无状态工具(get_current_time/web_search 等)合法恒为空参,若因网络/配额
    # 等非参数原因报 is_error,不应误判为"无效参数"。
    if _is_empty_args(tc.arguments) and tc.tool_name not in STATELESS_TOOLS:
        return "empty_args"
    return None


def _build_step_order(trace: Trace) -> dict[tuple[int, int], int]:
    """构建 (turn_id, step_id) → 全局序号 映射(与 analysis/counter_evidence.py
    同约定;在本文件本地实现,勿 import analysis 层,避免循环 import)。"""
    order: dict[tuple[int, int], int] = {}
    idx = 0
    for turn in trace.turns:
        for st in turn.steps:
            order[(turn.turn_id, st.step_id)] = idx
            idx += 1
    return order


class InvalidParamRetryDetector:
    """TOOL-004:无效参数重试(可避免失败尝试标记)。"""

    rule_id = "TOOL-004"
    version = "0.1.0"

    def detect(self, trace: Trace) -> list[Finding]:
        # 全部调用按全局顺序排列:(global_pos, turn_id, step_id, tc)
        order = _build_step_order(trace)
        ordered_calls: list[tuple[int, int, int, object]] = []
        for turn in trace.turns:
            for st in turn.steps:
                pos = order[(turn.turn_id, st.step_id)]
                for tc in st.tool_calls:
                    ordered_calls.append((pos, turn.turn_id, st.step_id, tc))

        # 收集参数错误失败 attempts(按全局顺序)
        failed: list[dict] = []
        for pos, turn_id, step_id, tc in ordered_calls:
            pattern = _match_param_error(tc)
            if pattern is not None:
                failed.append(
                    {
                        "pos": pos,
                        "turn_id": turn_id,
                        "step_id": step_id,
                        "tc": tc,
                        "pattern": pattern,
                    }
                )

        findings: list[Finding] = []
        for e in failed:
            retry = self._find_retry(e, ordered_calls)
            if retry is None:
                continue  # 无成功重试的 E 不输出
            s_tc, s_turn, s_step, retry_evidence, conf = retry

            # 证据链:失败 attempt + 成功重试 两个 link(observed_value 均 None)
            chain = EvidenceChain()
            chain.add(
                step_id=e["step_id"],
                turn_id=e["turn_id"],
                detail=(
                    f"{e['tc'].tool_name} 参数错误 {e['pattern']}"
                    f"(args={e['tc'].arguments[:200]})"
                ),
                observed_value=None,
            )
            chain.add(
                step_id=s_step,
                turn_id=s_turn,
                detail=f"同类重试成功(retry_evidence={retry_evidence})",
                observed_value=None,
            )

            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    type="invalid_param_retry",
                    severity="low",  # 单个失败 attempt,无成本声明
                    confidence=conf,
                    occurrences=1,  # 每个失败 attempt 至多一条 finding
                    kind="flag",  # 候选缺陷模式标记(非 cost/observation/reliability)
                    evidence=chain.links,
                    fingerprint="",  # 无稳定指纹(参数不一致,不伪造)
                    details={
                        "tool_name": e["tc"].tool_name,
                        "error_pattern": e["pattern"],  # 命中关键词 或 "empty_args"
                        "error_message": (e["tc"].result or "")[:200],
                        "failed_arguments": e["tc"].arguments[:200],
                        "retry_arguments": s_tc.arguments[:200],
                        "retry_evidence": retry_evidence,  # "call_id" | "adjacent_step"
                        "failed_call_id": e["tc"].call_id,
                        "retry_call_id": s_tc.call_id,
                        # (turn_id, step_id) 复合键:step_id 每 turn 重新编号
                        "failed_index": (e["turn_id"], e["step_id"]),
                        "retry_index": (s_turn, s_step),
                        "retry_step_window": RETRY_STEP_WINDOW,
                        "evidence_chain": chain,
                    },
                )
            )

        return findings

    def _find_retry(self, e: dict, ordered_calls: list) -> tuple | None:
        """在后续调用中找成功重试 S。

        优先 call_id 层(身份同一,证据最硬);否则 adjacent_step 层取最近的 S。
        """
        # call_id 层:身份同一,无需 proximity 推断
        for pos, _turn_id, _step_id, tc in ordered_calls:
            if pos <= e["pos"]:
                continue
            if tc.is_error:
                continue
            if tc.call_id == e["tc"].call_id:
                return tc, _turn_id, _step_id, "call_id", CALL_ID_CONFIDENCE

        # adjacent_step 层:同 tool_name + 强制同 turn + 1 ≤ diff ≤ window
        best = None
        for pos, turn_id, _step_id, tc in ordered_calls:
            if pos <= e["pos"]:
                continue
            if tc.is_error:
                continue
            if tc.tool_name != e["tc"].tool_name:
                continue
            if turn_id != e["turn_id"]:
                continue  # 跨 turn 不配对(M5)
            diff = pos - e["pos"]
            if not (1 <= diff <= RETRY_STEP_WINDOW):
                continue  # 同 step(并行,M2)与超窗均不配对
            conf = (
                ADJACENT_KEYWORD_CONFIDENCE
                if e["pattern"] != "empty_args"
                else ADJACENT_EMPTY_ARGS_CONFIDENCE
            )
            if best is None or diff < best[0]:
                best = (diff, tc, turn_id, _step_id, conf)

        if best is None:
            return None
        _diff, tc, turn_id, step_id, conf = best
        return tc, turn_id, step_id, "adjacent_step", conf
