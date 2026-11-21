"""口径对账：把字典里的 SQL 放到快照上执行，与登记基准值比对。

为什么要对账：`metrics.yaml` 里的 formula 过去只是文字，谁也不知道
「发放口径核销率 5%」这个数怎么算出来的、有没有算错。现在把 SQL 和基准值
一起登记，reconcile 每次执行：

    SELECT ... FROM coupons  →  0.05  ==  expected 0.05

数据升版本后基准值必然变，这是设计意图——口径与数据版本绑定，
没重算基准值就不许继续声称「口径没变」。

没写 SQL 的指标（如补贴 ROI 要实验组数据）不会被假装算出来，直接不参与对账。
"""

from __future__ import annotations

import sqlite3

from datahub import manifest as manifest_mod
from datahub import metrics as metrics_mod
from datahub import snapshot as snapshot_mod


def _check_one(conn: sqlite3.Connection, item: dict, variant: dict, tolerance: float) -> dict:
    """执行一条口径 SQL，与 expected 比对。"""
    expected = float(variant["expected"])
    result = {
        "metric": item["id"],
        "variant": variant["key"],
        "expected": expected,
        "actual": None,
        "diff": None,
        "passed": False,
        "error": None,
    }

    try:
        row = conn.execute(variant["sql"]).fetchone()
    except sqlite3.Error as exc:
        result["error"] = f"SQL 执行失败：{exc}"
        return result

    if row is None or len(row) != 1:
        result["error"] = "SQL 必须返回单行单值"
        return result
    if row[0] is None:
        result["error"] = "算出来是 NULL（多半是分母为 0）"
        return result

    actual = float(row[0])
    result["actual"] = actual
    result["diff"] = actual - expected
    result["passed"] = abs(actual - expected) <= tolerance
    return result


def reconcile(dataset_name: str, tolerance: float = 1e-4) -> list[dict]:
    """对某份数据集执行全部可对账口径，返回逐条结果。"""
    item = manifest_mod.load(dataset_name)
    payload, _ = snapshot_mod.snapshot_paths(item)
    if not payload.exists():
        raise FileNotFoundError(
            f"{dataset_name} 还没有快照载荷，先跑 datahub snapshot build --name {dataset_name}"
        )

    pairs = metrics_mod.variants_with_sql(dataset_name)
    if not pairs:
        return []

    conn = sqlite3.connect(f"file:{payload}?mode=ro", uri=True)
    try:
        return [_check_one(conn, metric, variant, tolerance) for metric, variant in pairs]
    finally:
        conn.close()
