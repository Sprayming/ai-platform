"""adapter 注册表。

新增一个套件 = 新增一个 adapter 模块 + 在 suites.yaml 里登记，
不需要改 runner、报告或门禁。
"""

from __future__ import annotations

from importlib import import_module

# suite -> adapter 模块名（与 suites.yaml 的 adapter 字段一致）
_ADAPTER_MODULES = {
    "driver_intent": "evalhub.adapters.driver_intent",
    "rag_ragas": "evalhub.adapters.rag_ragas",
    "ride_nl2sql": "evalhub.adapters.ride_nl2sql",
}


def load_adapter(name: str):
    """按名字加载 adapter 模块（模块需提供 adapt(artifact, options)）。"""
    module_path = _ADAPTER_MODULES.get(name)
    if module_path is None:
        raise KeyError(f"未注册的 adapter: {name}，可选: {sorted(_ADAPTER_MODULES)}")
    return import_module(module_path)
