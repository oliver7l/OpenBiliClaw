"""跨模块迭代合成引擎。

将日记分析、聊天分析等模块的洞察结果综合起来，通过迭代 LLM 合成
不断优化个人认知画像。每次运行从上次结果出发，只处理新增数据，
并将新洞察合并回已有结果，形成持续进化。

核心流程：
  1. 拉取上次运行以来所有模块的新增分析结果
  2. 加载上次合成结果作为上下文
  3. LLM 合成：旧结果 + 新数据 → 更新后的洞察
  4. 增量存储（版本化），同时更新源模块的分析记录
"""

from .engine import SynthesisEngine
from .models import (
    CrossModulePattern,
    DiarySynthesisResult,
    SynthesisConfig,
    SynthesisState,
    SynthesisVersion,
)

__all__ = [
    "SynthesisEngine",
    "SynthesisConfig",
    "SynthesisState",
    "SynthesisVersion",
    "DiarySynthesisResult",
    "CrossModulePattern",
]
