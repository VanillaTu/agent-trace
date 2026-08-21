"""RETRY-001 Model Retry Event / Reliability Observation 检测器。

定位(数据驱动):
- **不是 cost defect**,是 reliability observation
- 数据证据:失败的 retry attempt usage=0(无 token 消耗)→ 不产生虚构 cost
- outcome:recovered(重试后成功)/ failed(最终失败)/ unknown(无法重建)

Finding:
    retry_id / provider / mode / error_code / retry_count / outcome
    kind = observation

Lifecycle 重建(基于 Trace.events 的 seq 顺序):
    llm/finish/error → llm/retry → llm/retry-started → llm/finish/* ...

铁律:
- usage=0 是 attribution boundary,不是 missing implementation
- 不把 retry 次数 × 单价 算成 token 成本
"""

from __future__ import annotations

from ..core.canonical_trace import Trace
from .base import Detector, Evidence, EvidenceChain, Finding


class ModelRetryDetector:
    """RETRY-001:模型重试事件 / 可靠性观测。"""

    rule_id = "RETRY-001"
    version = "0.1.0"

    def detect(self, trace: Trace) -> list[Finding]:
        # 收集 retry 事件与 finish 事件,按 seq 排序重建 lifecycle
        retry_events = [
            e for e in trace.events if e.type in ("llm/retry", "llm/retry-started")
        ]
        finish_events = [
            e for e in trace.events if e.type.startswith("llm/finish/")
        ]

        # 按 seq 排序合并事件流
        all_events = sorted(trace.events, key=lambda e: e.seq)

        # 按 retryId 分组 retry 事件
        from collections import defaultdict
        retry_by_id: dict[str, list] = defaultdict(list)
        for e in retry_events:
            rid = e.data.get("retryId")
            if rid:
                retry_by_id[rid].append(e)

        findings: list[Finding] = []
        for rid, events in retry_by_id.items():
            # retry 事件(不含 started)
            retries = [e for e in events if e.type == "llm/retry"]
            started = [e for e in events if e.type == "llm/retry-started"]
            if not retries:
                continue

            first = retries[0]
            provider = first.data.get("provider", "unknown")
            mode = first.data.get("mode", "unknown")
            retry_count = len(started) or len(retries)
            turn = first.turn_id
            step = first.step_id

            # 重建 outcome:找该 retryId 范围内最后的 finish
            last_retry_seq = max(e.seq for e in events)
            first_retry_seq = min(e.seq for e in events)

            # 紧邻 retry 之前最近的同 (turn,step) finish error(首因)
            prior_errors = [
                e for e in finish_events
                if e.type == "llm/finish/error"
                and e.turn_id == turn and e.step_id == step
                and e.seq < first_retry_seq
            ]
            # 取 seq 最大(最近的)的一个
            prior_errors.sort(key=lambda e: e.seq)
            first_error = prior_errors[-1] if prior_errors else None

            # retry 之后同 (turn,step) 的 finish
            subsequent_finishes = [
                e for e in finish_events
                if e.seq > last_retry_seq and e.turn_id == turn and e.step_id == step
            ]

            # error code:优先取紧邻的首因
            error_code = None
            error_msg = None
            if first_error is not None:
                error_code = first_error.data.get("error_code")
                error_msg = first_error.data.get("error_message")
            elif subsequent_finishes:
                error_code = subsequent_finishes[-1].data.get("error_code")
                error_msg = subsequent_finishes[-1].data.get("error_message")

            # outcome:retry 后是否有成功 finish
            recovered = any(e.type == "llm/finish/success" for e in subsequent_finishes)
            failed_after = any(e.type == "llm/finish/error" for e in subsequent_finishes)
            if recovered:
                outcome = "recovered"
            elif failed_after:
                outcome = "failed"
            else:
                outcome = "unknown"

            # 保守:只有明确能证明才给 outcome,否则 unknown
            confidence = 0.95 if outcome != "unknown" else 0.6

            chain = EvidenceChain()
            chain.add(
                step_id=step or 0,
                turn_id=turn or 0,
                detail=(
                    f"retryId={rid[:12]} provider={provider} mode={mode} "
                    f"retry={retry_count} error={error_code} outcome={outcome}"
                ),
                observed_value=retry_count,
            )

            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    type="model_retry",
                    severity="info",
                    confidence=confidence,
                    occurrences=retry_count,
                    kind="reliability",  # 可靠性事件
                    evidence=chain.links,
                    details={
                        "retry_id": rid,
                        "provider": provider,
                        "mode": mode,
                        "error_code": error_code,
                        "error_message": error_msg,
                        "retry_count": retry_count,
                        "outcome": outcome,
                        "related_turn": turn,
                        "related_step": step,
                        "evidence_chain": chain,
                    },
                )
            )

        return findings
