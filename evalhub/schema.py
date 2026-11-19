"""契约加载与校验。

所有 adapter 的产出必须先过 `validate_run`，再落盘。这样「格式漂移」会
在写入前就炸掉，而不是等到三个月后对比基线时才发现字段对不上。
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

CONTRACTS_DIR = Path(__file__).resolve().parent.parent / "contracts"


class ContractError(ValueError):
    """产物不符合契约。"""


def load_schema(kind: str) -> dict:
    """读取 contracts/<kind>.schema.json。"""
    path = CONTRACTS_DIR / f"{kind}.schema.json"
    if not path.exists():
        raise ContractError(f"契约不存在: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate(instance: dict, kind: str) -> None:
    """按契约校验，失败抛 ContractError（带字段路径）。"""
    try:
        jsonschema.validate(instance, load_schema(kind))
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "<root>"
        raise ContractError(f"{kind} 契约校验失败 @ {where}: {exc.message}") from exc


def validate_run(run: dict) -> None:
    validate(run, "eval_run")


def validate_case(case: dict) -> None:
    validate(case, "eval_case")


def metric(value, higher_is_better: bool, unit: str | None = None) -> dict:
    """构造一个指标对象；value 为 None 表示本次没产出该指标。"""
    item = {"value": value, "higher_is_better": higher_is_better}
    if unit:
        item["unit"] = unit
    return item
