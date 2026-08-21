# Tasks — detector-ctx-001(上下文健康度)

> 依据:已定稿并通过实现前评审(含 B1 虚构窗口 / M1 cache_read 口径 / M2 阈值校准 / M3 信号稀释 / M7 tie-break 修复)的 `design.md`。目标:新增**分析层数据块** `ContextHealth`(会话级观测,不做 Detector/Finding),挂 `DiagnosisResult.context_health`,报告 `enable_analysis=True` 时渲染"上下文健康度"块,全部确定性、纯 additive。
>
> **additive 铁律**:不新增/修改任何 detector,不注册 `ALL_DETECTORS` / `ALL_ATTRIBUTION_ENGINES`(两者保持 5 个),不改变现有 detector 检测行为,不改默认报告输出(golden 基线逐字节一致)。**必改既有测试:0 处** —— `tests/test_checkpoint.py::test_registry_has_five_detectors` 与 `test_pipeline_no_rule_specific_branch` 均不受影响。唯一"命中面更新"是文档事实同步(测试总数 114 → 114+N,由 `scripts/check_facts.py` 门强制)。
>
> 验证命令(收尾统一跑):`python -m pytest tests -q` / `python scripts/check_facts.py` / `openspec validate detector-ctx-001 --strict`。

## 1. 新增分析层数据块 `agenttrace/analysis/context_health.py`(D1/D2/D3/D4/D5)

- [x] 1.1 新建 `agenttrace/analysis/context_health.py`,写模块 docstring(说明:分析层会话级"上下文健康度"观测,不做 Detector/Finding、不判因果、不归因;仅 `enable_analysis=True` 时由 pipeline Stage 3 调用),并加 import:
  - `from __future__ import annotations`
  - `from dataclasses import dataclass, field`
  - `from typing import Optional`
  - `from ..core.canonical_trace import Trace`
  - `from ..core.normalize import call_fingerprint`
  - 常量 `WINDOW_METADATA_KEY = "context_window"`、`OCCUPANCY_HIGH_WATERMARK = 0.70`(后者注释标注"占位待校准常量,仅窗口真实已知时参与判定")
- [x] 1.2 定义 `ContextHealth` dataclass(**全部字段带默认值**,空会话直接 `ContextHealth()` 即全 not-applicable):
  - `current_context_tokens: int = 0`
  - `peak_context_tokens: int = 0`
  - `turn_count: int = 0`
  - `total_tool_calls: int = 0`
  - `repeated_tool_calls: int = 0`
  - `repeat_rate: Optional[float] = None`(None = not applicable,非 0)
  - `window_tokens: Optional[int] = None`(None = 无真实窗口字段)
  - `window_source: str = "unknown"`(取值 `"metadata"` / `"unknown"`)
  - `occupancy_ratio: Optional[float] = None`(None = 窗口未知,不虚构)
  - `pressure_high: bool = False`
  - `stats_repeated_groups: list = field(default_factory=list)`(元素为三元组 `(fingerprint, 重复数, tool_name)`)
- [x] 1.3 实现 `_resolve_window(trace) -> tuple[Optional[int], str]`(B1 修复):
  - `m = (trace.metadata or {}).get(WINDOW_METADATA_KEY)`
  - `isinstance(m, int) and m > 0` → 返回 `(m, "metadata")`;否则返回 `(None, "unknown")`(**不兜底、不虚构窗口**)
- [x] 1.4 实现 `build_context_health(trace: Trace) -> ContextHealth` 主函数(D2 + M1 口径):
  - `steps = trace.all_steps()`;`if not steps: return ContextHealth()`(空会话全 not-applicable,不虚构数值)
  - 上下文口径辅助:`ctx(s) = s.usage.input_tokens + (s.usage.cache_read_tokens or 0)`(M1:含 cache_read,排除 cache_write)
  - `current = ctx(steps[-1])`(末 step);`peak = max(ctx(s) for s in steps)`
  - `turn_count = len(trace.turns)`
  - 分组:`groups: dict[str, list]`,遍历 `trace.all_tool_calls()`,`groups.setdefault(call_fingerprint(tc.tool_name, tc.arguments), []).append(tc)`(复用 `normalize.call_fingerprint`,不依赖 TOOL-001 finding)
  - `total = len(calls)`;`repeated = sum(len(g) - 1 for g in groups.values() if len(g) > 1)`;`repeat_rate = repeated / total if total > 0 else None`
- [x] 1.5 重复组确定性排序(D4/M7):
  - `stats_repeated_groups = sorted([(fp, len(g), g[0].tool_name) for fp, g in groups.items() if len(g) > 1], key=lambda t: (-t[1], t[2], t[0]))`(重复数降序 → tool_name 升序 → fingerprint 升序,逐字段确定)
- [x] 1.6 占用率 + 压力判定(D3/D5):
  - `window, source = _resolve_window(trace)`
  - `occupancy = (current / window) if window else None`(window 为 None → occupancy None)
  - `pressure = occupancy is not None and occupancy > OCCUPANCY_HIGH_WATERMARK`(严格 `>`;窗口未知时恒 False)
  - 以关键字参数组装并 `return ContextHealth(...)`(字段名与 dataclass 一致)
- [x] 1.7 `agenttrace/analysis/__init__.py` 导出:`from .context_health import ContextHealth, build_context_health`,并把 `"ContextHealth"`、`"build_context_health"` 加入 `__all__`(与 profile 并列)

## 2. `agenttrace/pipeline.py` 挂载(D1)

- [x] 2.1 `DiagnosisResult` 加字段 `context_health: Optional[object] = None`(紧跟 `profile` 之后,注释"Stage 3 产物,ContextHealth 实例;关闭时为 None";用 `Optional[object]` 与 profile 一致,避免顶层 import)
- [x] 2.2 Stage 3(`if enable_analysis:` 分支)内加 `from .analysis.context_health import build_context_health`,并按顺序执行:`refine_findings(result.findings, trace)` → `result.context_health = build_context_health(trace)` → `result.profile = build_profile(result.findings, result.attributions)`(不进 findings/attributions)

## 3. `agenttrace/report.py` 集成(D6)

- [x] 3.1 新增 `_render_context_health_block(ch) -> list[str]`(纯函数,确定性,镜像 `_render_profile_block` 的 `["", "### ..."]` 起始),输出格式:
  ```
  ### 上下文健康度(CTX-001)
  - 当前上下文: {current_context_tokens} tokens(input + cache_read)
  - 峰值上下文: {peak_context_tokens} tokens
  - turn 数: {turn_count}
  - 重复工具调用操作率: {rate}(重复 {repeated_tool_calls}/{total_tool_calls})
  - 上下文窗口: {window_tokens} tokens(来源 {window_source})
  - 占用率: {occupancy_ratio}
  ⚠ 上下文压力高,建议压缩(占用 {occupancy_ratio} > 阈值 70%)   # 仅 pressure_high=True
  ```
  - `repeat_rate is None` → 该行显示 `无工具调用`(非 "0%");否则 `f"{ch.repeat_rate:.1%}"`
  - `window_tokens is None` → 该行显示 `not applicable`
  - `occupancy_ratio is None` → 该行显示 `not applicable`;否则 `f"{ch.occupancy_ratio:.1%}"`
  - 压力行仅在 `ch.pressure_high` 为 True 时输出(阈值文本 "70%" 与 `OCCUPANCY_HIGH_WATERMARK=0.70` 一致)
  - **语义边界**:块内不得出现 token 成本数字或因果断言("浪费" / "导致" / "Total wasted");只陈述占用高关联退化风险
- [x] 3.2 `render_report` 签名加 `context_health=None`;在 `enable_analysis` 分支、`context_health is None` 时惰性构建 `from .analysis.context_health import build_context_health; context_health = build_context_health(trace)`(镜像 profile 惰性构建);并在 `_render_profile_block(profile)` **之后**追加 `lines.extend(_render_context_health_block(context_health))`。**两个渲染点都要加**:空 findings 早返回分支(`if not findings:` 内)与主分支(Summary 后)

## 4. `agenttrace/cli.py` 传参(集成点)

- [x] 4.1 `cmd_analyze` 的 `render_report(...)` 调用加 `context_health=result.context_health`(镜像现有 `profile=result.profile` 传参,避免二次构建)

## 5. 新增测试 `tests/test_context_health.py`(D7)

- [x] 5.1 空会话:无 step 的 trace → `build_context_health` 返回全 not-applicable 块(`current/peak/turn=0`、`repeat_rate=None`、`window_tokens=None`、`window_source="unknown"`、`occupancy_ratio=None`、`pressure_high=False`、`stats_repeated_groups=[]`)
- [x] 5.2 指标精确值(M1):构造含 cache_read 的 step(如 `input_tokens=1000, cache_read_tokens=5000`)→ `current_context_tokens == 6000`;多 step → `peak_context_tokens` 为各 step `input+cache_read` 最大值;`turn_count == len(trace.turns)`;含同 fingerprint 重复与跨 turn 重复 → `total_tool_calls` / `repeated_tool_calls` / `repeat_rate` 精确;有 step 但无任何 tool_calls → `repeat_rate is None`(非 0)
- [x] 5.3 窗口解析(B1):`metadata={"context_window": 128000}` → `window_tokens==128000`、`window_source=="metadata"`、`occupancy_ratio == current/128000`;metadata 不含该字段 → `window_source=="unknown"`、`window_tokens is None`、`occupancy_ratio is None`
- [x] 5.4 压力标记:metadata 真窗口且 `occupancy_ratio > 0.70` → `pressure_high=True`;窗口 unknown → 恒 `False`(即使 `current_context_tokens` 很大);真窗口且 `occupancy_ratio <= 0.70` → `False`
- [x] 5.5 确定性:同一 trace 两次 `build_context_health` 逐字段相等(含 `stats_repeated_groups` 顺序);两次 `diagnose(trace, enable_analysis=True)` 的 `result.context_health` 逐字段相等;构造两个"重复数相等"的组断言 tie-break(按 tool_name 升序,再 fingerprint 升序)
- [x] 5.6 归因边界:`"CTX-001" not in ALL_ATTRIBUTION_ENGINES`;`diagnose(enable_analysis=True)` 的 `result.attributions` 中无任何 CTX-001 条目;`ALL_DETECTORS` / `ALL_ATTRIBUTION_ENGINES` 仍为 5 个;健康度块文本不含 token 成本数字与因果断言词("浪费" / "导致")
- [x] 5.7 additive(关键):在 `tests/golden/golden_report.py::build_comprehensive_trace()` 上,默认路径 `diagnose(t)` 的 `result.context_health is None` 且 `render_report(...)`(默认 `enable_analysis=False`)输出与 `tests/golden/v05_baseline_report.md` 逐字节一致;`test_registry_has_five_detectors` 的 registry 断言仍成立
- [x] 5.8 报告集成:`diagnose(enable_analysis=True)` 后 `render_report(..., enable_analysis=True, profile=result.profile, context_health=result.context_health)` → 报告含 `上下文健康度` 块;`report.count("**Confidence:**") == len(findings)` 仍成立;window unknown 时该块占用显示 `not applicable`;再断言"不传 context_health"时报告惰性构建仍能渲染该块(镜像 profile 惰性测试)

## 6. 文档事实同步(Migration Plan + check_facts 门)

- [x] 6.1 运行 `python -m pytest tests --collect-only -q`,记录新测试总数 `T = 114 + N`(`N` 为 `test_context_health.py` 用例数),作为后续文档同步的真值
- [x] 6.2 同步 `FACTS.md` Auto 表:`测试总数` `114` → `T`(`detector 数` 保持 `5 个 detector` 不变,CTX-001 不是 detector)
- [x] 6.3 同步其余当前状态文档的**测试总数**(把 `114` 类声明改为 `T`):
  - `agenttrace/README.md`:line 5 `114 tests` 与 line 36 `114 个全绿` → `T`
  - `agenttrace/ARCHITECTURE.md`:line 148 `114 个 pytest 全过(...)` → `T`;并在"分析层"描述(架构图注释 + section 六"分析层三件事")补充 `context-health(上下文健康度)` 为第 4 件事
  - `PROJECT_STATE.md`:line 31 `114/114 全绿` 与 line 55 `114 个 pytest` → `T`;代码结构 `analysis/` 下补 `context_health.py`
  - `08-团队组织设计.md`:line 44 `114/114 全绿` → `T`
  - `09-最初目标对照标注.md`:line 58 `114 全绿` → `T`;Taxonomy 现状可补一句"CTX-001 上下文健康度已以分析层观测数据块落地(非 detector)"
- [x] 6.4 运行 `python scripts/check_facts.py`,按输出的 `文件:行号` 逐处修正剩余不一致直至 exit 0(只改当前状态文档,不改 `reports/` 与 `openspec/changes/archive/`)

## 7. 回归验证(收尾)

- [x] 7.1 `python -m pytest tests -q` 全绿:既有 114 测试 **0 处改动**仍绿 + 新增 `test_context_health.py` 全过;确认 `test_disable_analysis_byte_identical_to_v05`、`test_registry_has_five_detectors`、`test_pipeline_no_rule_specific_branch` 仍绿(默认路径逐字节 + registry 仍 5 + 零 rule 特判)
- [x] 7.2 `python scripts/check_facts.py` 通过(exit 0)
- [x] 7.3 `openspec validate detector-ctx-001 --strict` 通过
