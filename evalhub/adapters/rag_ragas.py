"""rag 知识库套件：把两种历史产物翻译成统一产物。

这个项目在生产过程中留下过两种评测格式，adapter 都能吃：

1. RAGAS 格式（`evaluation_ablation_*.json` / `evaluation_report.json`）
       {"config": "full_hybrid", "metrics": {"faithfulness": 1.0, ...}}
   用于检索配置消融，指标是四个质量分。

2. 问答闸门格式（`tests/eval/run_eval.py --report`）
       {"total": 31, "passed": 29, "pass_rate": 93.5, "results": [...]}
   用于上线前回归，指标是通过率与延迟。

不要求项目改产物结构——adapter 存在的意义就在这里：**吸收历史格式差异**，
而不是让每个项目为了统一而重写自己的评测脚本。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from evalhub.schema import metric

SUITE = "rag.ragas"
PROJECT = "rag-knowledge-base"

# RAGAS 四个指标全部「越高越好」
RAGAS_METRICS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")


def _dataset_meta(project_path: Path, dataset_rel: str | None) -> dict:
    """从金标集算数据集指纹与声明条数；没有金标集就留空。"""
    if not dataset_rel:
        return {"name": "rag_golden", "version": "unversioned", "sha256": "", "cases": 0}

    path = project_path / dataset_rel
    if not path.exists():
        return {"name": "rag_golden", "version": "unversioned", "sha256": "", "cases": 0}

    payload = json.loads(path.read_text(encoding="utf-8"))
    meta = payload.get("_meta", {}) if isinstance(payload, dict) else {}
    questions = payload.get("questions", payload) if isinstance(payload, dict) else payload
    return {
        "name": "rag_golden",
        "version": str(meta.get("version") or "unversioned"),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()[:12],
        "cases": int(meta.get("total") or len(questions or [])),
    }


def _adapt_ragas(raw: dict, dataset: dict, path: Path) -> dict:
    metrics = {
        name: metric(raw["metrics"].get(name), True)
        for name in RAGAS_METRICS
        if name in raw["metrics"]
    }
    if not metrics:
        raise ValueError(f"{path} 里没有可识别的 RAGAS 指标")
    return {
        "mode": str(raw.get("config") or "ragas"),
        "dataset": dataset,
        "model": raw.get("model"),
        "metrics": metrics,
        "per_case": [],
    }


def _adapt_qa_gate(raw: dict, dataset: dict) -> dict:
    total = int(raw.get("total") or 0)
    passed = int(raw.get("passed") or 0)
    pass_rate = raw.get("pass_rate")
    # 旧脚本里 pass_rate 是百分数（93.5），统一成 0~1
    if isinstance(pass_rate, (int, float)):
        pass_rate = pass_rate / 100 if pass_rate > 1 else float(pass_rate)

    metrics = {
        "pass_rate": metric(pass_rate, True),
        "avg_latency_ms": metric(raw.get("avg_duration_ms"), False, "ms"),
    }

    per_case = [
        {
            "case_id": str(item.get("no", "")),
            "passed": bool(item.get("passed")),
            "latency_ms": item.get("duration_ms"),
            "detail": {
                "query": item.get("query"),
                "citations": item.get("citations"),
                "reasons": item.get("reasons") or None,
            },
        }
        for item in raw.get("results", [])
    ]

    dataset = dict(dataset)
    if total:
        dataset["cases"] = total
    return {
        "mode": "qa_gate",
        "dataset": dataset,
        "model": raw.get("model"),
        "metrics": metrics,
        "per_case": per_case,
        "_passed_note": f"{passed}/{total}",
    }


def adapt(artifact: str | Path, options: dict | None = None) -> dict:
    """读取 rag 的评测产物，产出统一 EvalRun dict。"""
    options = options or {}
    path = Path(artifact)
    raw = json.loads(path.read_text(encoding="utf-8"))

    project_path = Path(options.get("project_path") or ".")
    dataset = _dataset_meta(project_path, options.get("dataset"))

    if isinstance(raw.get("metrics"), dict):
        body = _adapt_ragas(raw, dataset, path)
    elif "pass_rate" in raw:
        body = _adapt_qa_gate(raw, dataset)
    else:
        raise ValueError(f"{path} 既不是 RAGAS 格式也不是问答闸门格式")

    body.pop("_passed_note", None)
    return {
        "run_id": "",
        "suite": SUITE,
        "project": PROJECT,
        "generated_at": raw.get("generated_at", ""),
        "mode": body["mode"],
        "dataset": body["dataset"],
        "model": body["model"],
        "metrics": body["metrics"],
        "per_case": body["per_case"],
        "source_artifact": str(path),
    }
