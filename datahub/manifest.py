"""数据集注册表：读取 manifests/*.yaml 并按契约校验。

manifest 只有一份职责——回答「这份数据从哪来、怎么复现、谁负责」。
它不存数据本身，也不描述业务含义（那是 metrics.yaml 的事）。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from evalhub.schema import validate

ROOT = Path(__file__).resolve().parent.parent
MANIFESTS_DIR = ROOT / "manifests"


class ManifestError(ValueError):
    """manifest 缺失或不符合契约。"""


def load(name: str) -> dict:
    """按名字加载单个 manifest，校验后把 source 解析成绝对路径。"""
    path = MANIFESTS_DIR / f"{name}.yaml"
    if not path.exists():
        raise ManifestError(f"未登记的数据集: {name}（应有 {path}）")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    validate(data, "dataset")
    if data["name"] != name:
        raise ManifestError(f"{path.name} 的 name 字段为 {data['name']}，与文件名不一致")

    project = (ROOT / data["project"]).resolve()
    data["_project_path"] = project
    data["_source_path"] = (project / data["source"]).resolve()
    data["_manifest_path"] = path
    return data


def load_all() -> dict[str, dict]:
    """加载全部 manifest，按 name 索引。"""
    if not MANIFESTS_DIR.exists():
        return {}
    result = {}
    for path in sorted(MANIFESTS_DIR.glob("*.yaml")):
        item = load(path.stem)
        result[item["name"]] = item
    return result


def for_suite(suite: str) -> list[tuple[dict, dict]]:
    """返回套件实际消费的 (manifest, consumer) 对，评测集排在其他输入之前。"""
    assets = [
        (item, consumer)
        for item in load_all().values()
        for consumer in item.get("consumers") or []
        if consumer["suite"] == suite
    ]
    return sorted(assets, key=lambda pair: pair[1]["role"] != "evaluation_set")


def missing_source(manifest: dict) -> bool:
    """数据源文件是否不存在（换机器/未生成数据时会这样）。"""
    return not manifest["_source_path"].exists()
