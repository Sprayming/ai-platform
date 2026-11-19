"""基线对比与回归门禁。

判定规则只有一条：**相对容差**。
    允许波动 = |基线值| × tolerance（基线为 0 时退化成绝对容差 tolerance）

用相对值而不是绝对值，是因为不同指标量纲差太远：
准确率波动 0.02 已经很大，而 p95 延迟波动 20ms 才算正常波动。
同一条规则管两类指标，才不会被「给每个指标单独配阈值」拖垮。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MetricDelta:
    """单个指标的对比结果。"""

    suite: str
    metric: str
    baseline: float | None
    current: float | None
    delta: float | None
    allowed: float | None
    passed: bool
    status: str  # ok / regressed / improved / skipped

    def as_dict(self) -> dict:
        return {
            "suite": self.suite,
            "metric": self.metric,
            "baseline": self.baseline,
            "current": self.current,
            "delta": self.delta,
            "allowed": self.allowed,
            "passed": self.passed,
            "status": self.status,
        }


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.4f}"


def compare_runs(baseline: dict, current: dict, tolerance: float = 0.02) -> list[MetricDelta]:
    """比较同套件的两次运行，返回逐指标结果。"""
    suite = current.get("suite", baseline.get("suite", "?"))
    rows: list[MetricDelta] = []

    for name, cur in current.get("metrics", {}).items():
        base = baseline.get("metrics", {}).get(name)
        cur_value = None if cur is None else cur.get("value")
        base_value = None if base is None else base.get("value")

        if base is None or base_value is None or cur_value is None:
            rows.append(
                MetricDelta(suite, name, base_value, cur_value, None, None, True, "skipped")
            )
            continue

        delta = cur_value - base_value
        allowed = abs(base_value) * tolerance if base_value else tolerance
        higher_is_better = bool(cur.get("higher_is_better", True))

        if higher_is_better:
            passed = delta >= -allowed
            status = "improved" if delta > allowed else ("regressed" if not passed else "ok")
        else:
            passed = delta <= allowed
            status = "improved" if delta < -allowed else ("regressed" if not passed else "ok")

        rows.append(
            MetricDelta(suite, name, base_value, cur_value, delta, allowed, passed, status)
        )
    return rows


def gate(runs: list[dict], baselines: dict[str, dict | None], tolerance: float = 0.02):
    """对一批运行做门禁判定。

    Args:
        runs: 本次运行的产物列表。
        baselines: suite -> 基线产物（None 表示该套件还没有基线，跳过）。
        tolerance: 相对容差（0.02 = 允许 2% 的相对退化）。

    Returns:
        (rows, passed)：逐指标结果与整体是否通过。
    """
    rows: list[MetricDelta] = []
    passed = True
    for run in runs:
        baseline = baselines.get(run["suite"])
        if baseline is None:
            continue
        for row in compare_runs(baseline, run, tolerance):
            rows.append(row)
            if not row.passed:
                passed = False
    return rows, passed


def format_rows(rows: list[MetricDelta]) -> str:
    """把结果渲染成等宽表格（CLI 用）。"""
    header = f"{'SUITE':<16}{'METRIC':<22}{'BASE':>10}{'NOW':>10}{'DELTA':>10}  STATUS"
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            f"{row.suite:<16}{row.metric:<22}"
            f"{_fmt(row.baseline):>10}{_fmt(row.current):>10}"
            f"{_fmt(row.delta):>10}  {row.status}"
        )
    return "\n".join(lines)
