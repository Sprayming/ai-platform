"""driver 意图分类套件：把 backend/evals 的报告翻译成统一产物。

原始报告为「一个 dataset + 多个 provider」的结构：
    providers.{rules,llm,hybrid}.{accuracy,macro_f1,latency,failures}

一个 provider 就是一次独立的评测运行，所以 adapter 一次只翻译一个 provider，
默认取 hybrid（线上实际用的那一路）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from evalhub.schema import metric

SUITE = "driver.intent"
PROJECT = "driver-coupon-refund-agent"


def _dataset_meta(project_path: Path, dataset_rel: str | None, raw: dict) -> dict:
    """数据集身份：优先取报告自带的值，缺失时对题目集文件算指纹。

    真实报告是历史产物，不带 dataset 字段；这时按 options.dataset 指向的
    题目集算哈希，位数（16）与 datahub 快照一致，两层的指纹才对得上。
    """
    declared = raw.get("dataset") or {}
    sha = declared.get("sha256") or raw.get("dataset_sha256") or ""
    if not sha and dataset_rel:
        path = project_path / dataset_rel
        if path.exists():
            sha = hashlib.sha256(path.read_bytes()).hexdigest()[:16]

    return {
        "name": "driver_intent_cases",  # 与 datahub 的 manifest 同名，两层才对得上
        "version": str(declared.get("version") or raw.get("dataset_version") or "unknown"),
        "sha256": sha,
        "cases": int(raw.get("cases_count") or 0),
    }


def adapt(artifact: str | Path, options: dict | None = None) -> dict:
    """读取 driver 的评测报告，产出统一 EvalRun dict。"""
    options = options or {}
    path = Path(artifact)
    raw = json.loads(path.read_text(encoding="utf-8"))

    provider_name = options.get("provider", "hybrid")
    providers = raw.get("providers", {})
    if provider_name not in providers:
        raise KeyError(f"{path} 里没有 provider={provider_name}，实际有 {sorted(providers)}")
    provider = providers[provider_name]
    if provider.get("status") != "ok":
        raise ValueError(f"provider={provider_name} 状态异常: {provider.get('status')}")

    latency = provider.get("latency") or {}
    metrics = {
        "accuracy": metric(provider.get("accuracy"), True),
        "macro_f1": metric(provider.get("macro_f1"), True),
        "latency_avg_ms": metric(latency.get("avg_ms"), False, "ms"),
        "latency_p95_ms": metric(latency.get("p95_ms"), False, "ms"),
        "error_rate": metric(provider.get("error_rate", 0.0), False),
    }

    # 原始报告只记录失败项，统一契约允许 per_case 只含失败样本
    per_case = [
        {
            "case_id": item.get("case_id", ""),
            "passed": False,
            "detail": {
                "expected": item.get("expected"),
                "actual": item.get("actual"),
                "tags": item.get("tags"),
                "kind": item.get("kind"),
                "error": item.get("error") or None,
            },
        }
        for item in provider.get("failures", [])
        if item.get("case_id")
    ]

    dataset = _dataset_meta(
        Path(options.get("project_path") or "."), options.get("dataset"), raw
    )
    if not dataset["cases"]:
        dataset["cases"] = int(provider.get("total") or 0)
    return {
        "run_id": "",
        "suite": SUITE,
        "project": PROJECT,
        "generated_at": raw.get("generated_at", ""),
        "mode": provider_name,
        "dataset": dataset,
        "model": raw.get("llm_model") or None,
        "metrics": metrics,
        "per_case": per_case,
        "source_artifact": str(path),
    }
