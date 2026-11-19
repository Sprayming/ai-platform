"""evalhub —— 把多个项目的评测统一到"一份契约 + 一条命令"。

模块职责：
    schema    契约加载与校验
    adapters  各项目的产物翻译器
    gate      基线对比与回归门禁
    report    跨套件汇总 HTML
    registry  运行产物与基线的读写
    cli       命令行入口
"""

__version__ = "0.1.0"
