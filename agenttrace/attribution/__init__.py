"""attribution 子包:成本归因引擎。"""

from .cmp_001 import Cmp001AttributionEngine
from .retry_001 import Retry001AttributionEngine
from .sub_001 import Sub001AttributionEngine
from .think_001 import Think001AttributionEngine
from .tool_001 import Tool001AttributionEngine

ALL_ATTRIBUTION_ENGINES = {
    "TOOL-001": Tool001AttributionEngine,
    "CMP-001": Cmp001AttributionEngine,
    "THINK-001": Think001AttributionEngine,
    "RETRY-001": Retry001AttributionEngine,
    "SUB-001": Sub001AttributionEngine,
}
