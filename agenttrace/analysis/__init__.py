"""analysis 子包:分析层(反证 + 置信度完善 + 会话画像 + 上下文健康度)。

纯规则、无 LLM。默认关闭(enable_analysis=False),开启时由 pipeline Stage 3 挂载。
LLM 语义层为设计预留(见 design.md D6),本次不实现。
"""

from .ab_validation import ABResult, SEMANTIC_DEBATED_TOOLS, build_ab_validation
from .context_health import ContextHealth, build_context_health
from .counter_evidence import analyze_finding, refine_findings
from .profile import SessionProfile, build_profile
from .session_lineage import SessionLineage, build_session_lineage
from .token_invariant import TokenInvariant, build_token_invariant

__all__ = [
    "ABResult",
    "SEMANTIC_DEBATED_TOOLS",
    "build_ab_validation",
    "ContextHealth",
    "build_context_health",
    "analyze_finding",
    "refine_findings",
    "SessionProfile",
    "build_profile",
    "SessionLineage",
    "build_session_lineage",
    "TokenInvariant",
    "build_token_invariant",
]
