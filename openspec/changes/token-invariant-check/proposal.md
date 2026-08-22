# Proposal — token-invariant-check (A1)

## Why

项目核心定位已从"省 token"转向**测 harness 架构/工具性**(token 为切入点,省 token 是副产物)。真实数据核验(2026-08):**每个 (turn,step) 的 usage 在 `assistant/chunk` 与 `assistant/message` 各写一次、数字完全相同**——即 usage 被「双写」。DSH 官方 token-meter 与本项目 adapter 已按 (turn,step) 去重 → harness 没 2×。

这暴露一个**架构级不变量风险**:任何**不按 (turn,step) 去重**的 consumer(朴素 chunk+message 求和)会**精确 2× 高估** usage。这正是 lizhuojunx86 4 陷阱的"双写"陷阱,也是 harness 记账正确性的一个可测不变量。

## Goal

新增分析层**会话级数据块** `TokenInvariant`,统计会话内 usage 双写范围与"非去重消费方的假设性溢出上界",并给出 hedged 去重建议。作为**测 harness 架构/工具性**的第一个落地块。

## Non-Goal

不做 Detector/Finding;不判 harness bug;不混算 wasted;不改变哪份 usage 获胜;默认输出逐字节不变。

## Testing

`tests/test_token_invariant.py` + 全量回归 + `check_facts`;金钟罩逐字节保持绿。
