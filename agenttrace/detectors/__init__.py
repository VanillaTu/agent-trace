"""detectors 子包:缺陷检测器。"""

from .cmp_001 import CompactionDetector
from .retry_001 import ModelRetryDetector
from .sub_001 import SubagentDelegationDetector
from .think_001 import ReasoningIntensityDetector
from .tool_001 import DuplicateToolCallDetector

ALL_DETECTORS = [
    DuplicateToolCallDetector,
    CompactionDetector,
    ReasoningIntensityDetector,
    ModelRetryDetector,
    SubagentDelegationDetector,
]
