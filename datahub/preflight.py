"""评测前的数据可信校验。

普通 snapshot.verify 允许数据更新（warn）；这里面向发布与评测，要求数据源、
快照载荷和入库元数据指向同一份内容，并执行 manifest 声明的最低质量要求。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from datahub import manifest as manifest_mod
from datahub import metrics as metrics_mod
from datahub import reconcile as reconcile_mod
from datahub import snapshot as snapshot_mod


def _error(code: str, message: str) -> dict:
    return {"level": "error", "code": code, "message": message}


def _at_source(manifest: dict, path: Path) -> dict:
    item = dict(manifest)
    item["_source_path"] = path
    return item


def _fingerprint(state: dict) -> str:
    return state.get("dataset_sha256") or state.get("sha256") or ""


def _strict_drifts(manifest: dict, label: str) -> list[dict]:
    """strict 下任何漂移都拦截，包括普通 verify 中的内容更新 warn。"""
    try:
        drifts = snapshot_mod.verify(manifest)
    except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
        return [_error("source_unreadable", f"{label}无法读取：{exc}")]
    return [
        _error(drift["code"], f"{label}{drift['message']}")
        for drift in drifts
    ]


def _check_quality(manifest: dict) -> list[dict]:
    quality = manifest.get("quality") or {}
    if not quality:
        return []
    try:
        state = snapshot_mod.current_state(manifest)
    except (OSError, sqlite3.Error, ValueError, KeyError):
        return []  # 读取失败已经由结构校验报告，不重复制造异常

    problems = []
    minimum = quality.get("min_cases")
    if minimum is not None and int(state.get("cases") or 0) < minimum:
        problems.append(_error(
            "too_few_cases",
            f"样本数 {state.get('cases') or 0} 低于质量要求 {minimum}",
        ))

    tables = state.get("tables") or {}
    for table in quality.get("non_empty_tables") or []:
        if table in tables and tables[table]["rows"] == 0:
            problems.append(_error("empty_table", f"质量要求失败：{table} 是空表"))
    return problems


def _check_metrics(manifest: dict, tolerance: float) -> list[dict]:
    name = manifest["name"]
    if not metrics_mod.variants_with_sql(name):
        return []
    try:
        rows = reconcile_mod.reconcile(name, tolerance)
    except (FileNotFoundError, manifest_mod.ManifestError) as exc:
        return [_error("metric_reconcile_failed", str(exc))]

    problems = []
    for row in rows:
        label = f"{row['metric']}.{row['variant']}"
        if row["error"]:
            problems.append(_error("metric_reconcile_failed", f"{label}：{row['error']}"))
        elif not row["passed"]:
            problems.append(_error(
                "metric_mismatch",
                f"{label} 对账漂移：expected {row['expected']:.6f}，"
                f"actual {row['actual']:.6f}",
            ))
    return problems


def check_dataset(manifest: dict, tolerance: float = 1e-4) -> list[dict]:
    """严格校验一份资产；返回空列表才允许它进入评测。"""
    name = manifest["name"]
    if manifest_mod.missing_source(manifest):
        return [_error("missing_source", f"数据源不存在：{manifest['_source_path']}")]

    meta = snapshot_mod.load_meta(manifest)
    if meta is None:
        return [_error("missing_snapshot", f"{name} 还没有快照，先跑 datahub snapshot build")]

    problems = []
    if meta.get("name") != name or meta.get("version") != manifest["version"]:
        problems.append(_error("snapshot_identity_mismatch", "快照元数据的 name/version 与注册表不一致"))

    problems.extend(_strict_drifts(manifest, "当前数据源："))
    problems.extend(_check_quality(manifest))

    payload, _ = snapshot_mod.snapshot_paths(manifest)
    if not payload.exists():
        problems.append(_error("missing_payload", f"快照载荷不存在：{payload.name}"))
        return problems

    payload_manifest = _at_source(manifest, payload)
    problems.extend(_strict_drifts(payload_manifest, "快照载荷："))
    try:
        payload_state = snapshot_mod.current_state(payload_manifest)
        expected_sha = _fingerprint(meta)
        if _fingerprint(payload_state) != expected_sha:
            problems.append(_error(
                "payload_fingerprint_mismatch",
                f"快照载荷指纹 {_fingerprint(payload_state)} 与元数据 {expected_sha} 不一致",
            ))
    except (OSError, sqlite3.Error, ValueError, KeyError):
        pass  # _strict_drifts 已报告读取失败

    problems.extend(_check_metrics(manifest, tolerance))
    return problems
