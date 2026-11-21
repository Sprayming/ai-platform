"""指标口径字典。

存在的理由：同一个词在不同团队嘴里不是同一个数。核销率按「发放数」还是「领取数」
还是「已结算订单数」做分母，能差出好几个百分点，而这几个百分点直接决定补贴预算。

所以字典里每个指标都必须写清：分子、分母、口径、责任人、改过的历史。
没有分子分母的指标一律不允许登记——这是 `check` 的唯一硬规则。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
METRICS_PATH = ROOT / "metrics.yaml"

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_EVAL_KEY_RE = re.compile(r"^[a-z0-9]+\.[a-z0-9_]+:[a-z0-9_]+$")
_ALLOWED_UNITS = {"ratio", "count", "cny", "ms", "score"}


class MetricsError(ValueError):
    """指标字典不合法。"""


def load() -> dict:
    """读取 metrics.yaml。"""
    if not METRICS_PATH.exists():
        raise MetricsError(f"缺少指标字典: {METRICS_PATH}")
    data = yaml.safe_load(METRICS_PATH.read_text(encoding="utf-8")) or {}
    return data


def all_metrics() -> list[dict]:
    return load().get("metrics", []) or []


def get(metric_id: str) -> dict:
    """按 id 取指标，找不到抛错。"""
    for item in all_metrics():
        if item.get("id") == metric_id:
            return item
    raise MetricsError(f"未登记的指标: {metric_id}")


def check() -> list[str]:
    """校验字典完整性，返回问题列表（空表示通过）。"""
    problems: list[str] = []
    seen_ids: set[str] = set()
    seen_aliases: dict[str, str] = {}

    for index, item in enumerate(all_metrics()):
        where = f"metrics[{index}]"
        metric_id = item.get("id", "")
        if not _ID_RE.match(metric_id or ""):
            problems.append(f"{where}: id 非法（{metric_id!r}）")
            continue
        if metric_id in seen_ids:
            problems.append(f"{where}: id 重复（{metric_id}）")
        seen_ids.add(metric_id)

        if not item.get("name"):
            problems.append(f"{metric_id}: 缺少中文名")
        if item.get("unit") not in _ALLOWED_UNITS:
            problems.append(f"{metric_id}: unit 必须是 {sorted(_ALLOWED_UNITS)}")
        if not item.get("owner"):
            problems.append(f"{metric_id}: 缺少负责人——没人负责的口径一定会漂")
        if not isinstance(item.get("higher_is_better"), bool):
            problems.append(f"{metric_id}: 必须声明 higher_is_better（与评测产物比对方向）")

        for key in item.get("eval_keys") or []:
            if not _EVAL_KEY_RE.match(str(key)):
                problems.append(f"{metric_id}: eval_keys 格式应为 <suite>:<metric>（{key!r}）")

        has_sql = False
        for variant in item.get("variants") or []:
            if variant.get("sql"):
                has_sql = True
                if variant.get("expected") is None:
                    problems.append(
                        f"{metric_id}.{variant.get('key', '?')}: 有 sql 却没有 expected，无法对账"
                    )
            elif variant.get("expected") is not None:
                problems.append(
                    f"{metric_id}.{variant.get('key', '?')}: 有 expected 却没有 sql，无从核对"
                )
        if has_sql and not item.get("dataset"):
            problems.append(f"{metric_id}: 写了 sql 必须声明 dataset（去哪份数据上跑）")

        variants = item.get("variants") or []
        if not variants:
            problems.append(f"{metric_id}: 至少要有一个口径定义")
            continue

        keys = []
        for variant in variants:
            key = variant.get("key", "")
            keys.append(key)
            for field in ("numerator", "denominator"):
                if not variant.get(field):
                    problems.append(f"{metric_id}.{key or '?'}: 缺少 {field}")
            if not variant.get("name"):
                problems.append(f"{metric_id}.{key or '?'}: 缺少口径名称")

        canonical = item.get("canonical")
        if canonical and canonical not in keys:
            problems.append(f"{metric_id}: canonical={canonical} 不在 variants 里")
        if not canonical and len(variants) > 1:
            problems.append(f"{metric_id}: 有多个口径时必须指定 canonical")
        if keys.count(canonical) > 1:
            problems.append(f"{metric_id}: canonical 只能有一个")

        for alias in item.get("aliases") or []:
            if alias in seen_aliases:
                problems.append(f"{metric_id}: 别名 {alias} 与 {seen_aliases[alias]} 冲突")
            seen_aliases[alias] = metric_id

    return problems


def variants_with_sql(dataset: str | None = None) -> list[tuple[dict, dict]]:
    """列出带可执行 SQL 的口径，返回 (指标, 口径) 对。

    指定 dataset 时只返回在其上对账的口径；没写 SQL 的指标（如补贴 ROI
    需要实验组数据）自然被排除，不会假装能算。
    """
    pairs: list[tuple[dict, dict]] = []
    for item in all_metrics():
        if dataset and item.get("dataset") != dataset:
            continue
        for variant in item.get("variants") or []:
            if variant.get("sql"):
                pairs.append((item, variant))
    return pairs


def audit(baselines: dict[str, dict] | None = None) -> list[str]:
    """门禁指标 × 口径字典交叉校验，返回问题列表（空表示通过）。

    门禁里出现的每个指标都必须能追溯到一条口径定义，否则「这个数怎么算的」
    在评审会上就是无解的；反过来，方向（越高越好/越低越好）不一致会导致
    门禁把改进判成退化，属于必须拦下的硬错误。
    """
    if baselines is None:
        from evalhub import registry

        baselines = registry.load_all_baselines()

    registered: dict[tuple[str, str], dict] = {}
    for item in all_metrics():
        for key in item.get("eval_keys") or []:
            suite, _, metric = str(key).partition(":")
            registered[(suite, metric)] = item

    problems: list[str] = []
    for suite, run in sorted(baselines.items()):
        for metric, spec in sorted((run.get("metrics") or {}).items()):
            item = registered.get((suite, metric))
            if item is None:
                problems.append(
                    f"{suite}:{metric} 未登记口径（metrics.yaml 没有 eval_keys 指向它）"
                )
                continue

            baseline_direction = bool(spec.get("higher_is_better", True))
            if bool(item["higher_is_better"]) != baseline_direction:
                problems.append(
                    f"{suite}:{metric} 方向不一致：字典={item['higher_is_better']}，"
                    f"评测={baseline_direction}"
                )

            unit = spec.get("unit")
            if unit and item.get("unit") != unit:
                problems.append(
                    f"{suite}:{metric} 单位不一致：字典={item.get('unit')}，评测={unit}"
                )
    return problems


def format_metric(item: dict) -> str:
    """把指标渲染成便于放进文档/评审的文本。"""
    lines = [
        f"{item['id']}　{item.get('name', '')}",
        f"  单位：{item.get('unit')}　负责人：{item.get('owner')}",
    ]
    if item.get("definition"):
        lines.append(f"  定义：{item['definition']}")
    for variant in item.get("variants", []):
        mark = "★" if variant.get("key") == item.get("canonical") else " "
        lines.append(
            f"  {mark} {variant.get('key')}　{variant.get('name')}："
            f"{variant.get('numerator')} / {variant.get('denominator')}"
        )
        if variant.get("formula"):
            lines.append(f"      {variant['formula']}")
        if variant.get("scope"):
            lines.append(f"      适用：{variant['scope']}")
    for rev in item.get("revisions", []):
        lines.append(f"  · {rev.get('date')} {rev.get('change')}（{rev.get('by')}）")
    return "\n".join(lines)
