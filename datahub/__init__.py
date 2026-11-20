"""datahub —— 数据集登记、快照与指标口径字典。

模块职责：
    manifest  读取并校验 manifests/*.yaml
    snapshot  生成只读快照、计算内容哈希、检查漂移
    metrics   指标口径字典（分子/分母/口径/责任人/变更）
    cli       命令行入口
"""

__version__ = "0.1.0"
