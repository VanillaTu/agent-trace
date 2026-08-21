"""analysis 子包:分析层(反证 + 置信度完善 + 会话画像)。

纯规则、无 LLM。默认关闭(enable_analysis=False),开启时由 pipeline Stage 3 挂载。
LLM 语义层为设计预留(见 design.md D6),本次不实现。
"""

from .counter_evidence import analyze_finding, refine_findings
from .profile import SessionProfile, build_profile

__all__ = [
    "analyze_finding",
    "refine_findings",
    "SessionProfile",
    "build_profile",
]
