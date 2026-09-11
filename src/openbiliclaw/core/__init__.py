"""跨模块共享的中性基础件（无领域语义）。

分层规则：storage / sources / soul / discovery 等均可依赖 core；
core 不依赖任何业务模块（保持零反向依赖）。
"""
