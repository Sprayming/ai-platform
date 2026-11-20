"""ride NL2SQL 套件：把 eval/nl2sql 的报告翻译成统一产物。

原始报告由 `eval/nl2sql/run_eval.py --json-out` 写出，两种模式：
- gold_only：只校验 gold SQL 本身（无需 LLM key，CI 可跑）
- with_llm：额外产出 execution_accuracy（生成 SQL 与 gold 的执行结果集比对）

两种模式的指标集合不同，统一产物里用 value=null 表示「本次未产出」，
而不是让字段消失——这样跨模式对比时不会误判为「指标退化为 0」。
"""

from __future__ import annotations

import json
from pathlib import Path

from evalhub.schema import metric

SUITE = "ride.nl2sql"
PROJECT = "ride-hailing-analytics-system"


def adapt(artifact: str | Path, options: dict | None = None) -> dict:
    """读取 ride 的评测报告，产出统一 EvalRun dict。"""
    path = Path(artifact)
    raw = json.loads(path.read_text(encoding="utf-8"))

    raw_metrics = raw.get("metrics", {})
    metrics = {
        "gold_pass_rate": metric(raw_metrics.get("gold_pass_rate"), True),
        "execution_accuracy": metric(raw_metrics.get("execution_accuracy"), True),
        "semantic_accuracy": metric(raw_metrics.get("semantic_accuracy"), True),
        "semantic_coverage": metric(raw_metrics.get("semantic_coverage"), True),
    }

    per_case = [
        {
            "case_id": item.get("case_id", ""),
            "passed": bool(item.get("passed")),
            "detail": {
                "category": item.get("category"),
                "question": item.get("question"),
                "pred_sql": item.get("pred_sql"),
                "error": item.get("error"),
            },
        }
        for item in raw.get("cases", [])
        if item.get("case_id")
    ]

    dataset = raw.get("dataset") or {}
    return {
        "run_id": "",
        "suite": SUITE,
        "project": PROJECT,
        "generated_at": raw.get("generated_at", ""),
        "mode": raw.get("mode", "gold_only"),
        "dataset": {
            "name": dataset.get("name", "ride_nl2sql"),
            "version": dataset.get("version", "unknown"),
            "sha256": dataset.get("sha256", ""),
            "cases": int(dataset.get("cases") or len(per_case)),
        },
        "model": raw.get("model") or None,
        "metrics": metrics,
        "per_case": per_case,
        "source_artifact": str(path),
    }
