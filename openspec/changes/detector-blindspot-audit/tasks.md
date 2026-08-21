## 1. 样本收集

- [ ] 1.1 收集待审计会话清单:`~/.dsh/sessions/--D-workspace--/` 下全部会话目录(开发会话、子代理会话、历史样本等),标注每个会话的"已知特点"(长/短、有无错误、有无 subagent)
- [ ] 1.2 对每个会话跑 `python -m agenttrace.cli analyze <会话目录>`,输出报告存档到 `research/detector-blindspot-audit/reports/`

## 2. 盲区排查(核心:人工核对 trace 异常 vs 检出结果)

- [ ] 2.1 对每个会话,解压 session.jsonl.zstd,人工扫描异常事件类型:
      - 工具调用报错(error result: invalid arguments / tool not found / exit code 非零)
      - 失败后重试(同工具同参数重试)
      - 超大工具结果(大 observation)后未被引用
      - 长时间无进展/重复规划
      - 空转/重复读取同一文件
      - 其他看起来"浪费/异常"但非 5 类信号的事件
- [ ] 2.2 对照该会话的 analyze 报告,标记"发生了但没检出"的事件 → 候选盲区
- [ ] 2.3 对每个候选盲区,确认它不属于现有 5 detector 的语义(TOOL-001 重复/ CMP 压缩/ THINK 推理/ RETRY 模型重试/ SUB 委托),排除误判

## 3. 盲区清单落盘

- [ ] 3.1 写 `research/detector-blindspot-audit/blindspot-inventory.md`,每个盲区一条:
      - 盲区 ID(BL-001 起)+ 事件类型 + 真实样本(会话 id + turn/step + 事件摘录)
      - 对应最初设计类别(05 文档:duplicate/retry storm/invalid param retry/oversized observation/low-value unused observation/其他)
      - 建议的新 detector ID(TOOL-004 等)+ 一句话检测思路
      - 优先级(高/中/低,按"发生频率 × 可避免成本"估)
- [ ] 3.2 种子盲区已确认:BL-001 invalid param retry(样本 <session-id>,对应 TOOL-004 候选)——写入清单
- [ ] 3.3 标注哪些盲区当前数据足以实现(有 2+ 样本)、哪些样本不足(仅 1 个,待收集)

## 4. 收尾

- [ ] 4.1 汇报:审计了 N 个会话、发现 M 个盲区、其中 K 个样本充足可立项;盲区清单路径
- [ ] 4.2 `openspec validate detector-blindspot-audit --strict` 通过
- [ ] 4.3 `openspec archive detector-blindspot-audit` 归档(调研型,归档即记录)
- [ ] 4.4 开源后:盲区清单转 GitHub issues(每个高优先级盲区一个 issue)
