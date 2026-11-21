"""基线对比与回归门禁。

判定规则只有一条：**相对容差**。
    允许波动 = |基线值| × tolerance（基线为 0 时退化成绝对容差 tolerance）

用相对值而不是绝对值，是因为不同指标量纲差太远：
准确率波动 0.02 已经很大，而 p95 延迟波动 20ms 才算正常波动。
同一条规则管两类指标，才不会被「给每个指标单独配阈值」拖垮。

除指标本身，还有两类「比的不是指标、但会让结论失效」的情况：

1. **指标键消失。** 值为 null 表示「本次没算」，键消失表示「适配器不再产出」。
   后者会在只遍历本次指标的实现里静默通过，所以这里按并集比对。
2. **数据集变了。** 换数据后的涨跌不能归因于代码，默认拦截；
   `allow_dataset_change=True` 时降级为提示。
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
    status: str  # ok / regressed / improved / skipped / missing

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


@dataclass
class GateResult:
    """门禁结果。

    仍然支持 `rows, passed = gate(...)` 解包，notices 单独取，
    避免为了提示信息改动所有调用点。
    """

    rows: list[MetricDelta] = field(default_factory=list)
    passed: bool = True
    notices: list[dict] = field(default_factory=list)

    def __iter__(self):
        yield self.rows
        yield self.passed


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.4f}"


def _dataset_fields(run: dict) -> dict:
    """取数据集身份三件套，只保留有值的字段。"""
    dataset = run.get("dataset") or {}
    return {
        key: str(dataset[key])
        for key in ("name", "version", "sha256")
        if dataset.get(key)
    }


def compare_dataset(baseline: dict, current: dict, allow_change: bool = False) -> list[dict]:
    """比对两次运行的数据集身份，返回提示列表。

    只在两边都有值时才比——driver 这类产物历史上不记数据集字段，
    缺值代表「不知道」，不能当成「变了」。
    """
    suite = current.get("suite", baseline.get("suite", "?"))
    base = _dataset_fields(baseline)
    cur = _dataset_fields(current)
    notices: list[dict] = []

    for key in ("name", "version", "sha256"):
        if key not in base or key not in cur or base[key] == cur[key]:
            continue
        notices.append({
            "level": "warn" if allow_change else "error",
            "suite": suite,
            "code": "dataset_changed",
            "message": (
                f"{suite} 数据集 {key} 变化：{base[key]} → {cur[key]}；"
                "指标不可直接比较" + ("（已按 --allow-dataset-change 放行）" if allow_change else "")
            ),
        })
    return notices


def compare_revision(baseline: dict, current: dict) -> list[dict]:
    """基线代码版本与本次不同时提示——不拦截，正常迭代必然如此。"""
    suite = current.get("suite", baseline.get("suite", "?"))
    base_rev = baseline.get("git_rev")
    cur_rev = current.get("git_rev")
    if not base_rev or not cur_rev or base_rev == cur_rev:
        return []
    return [{
        "level": "warn",
        "suite": suite,
        "code": "stale_baseline",
        "message": f"{suite} 基线来自 {base_rev}，本次为 {cur_rev}；结论只在代码差异可解释时有效",
    }]


def compare_runs(
    baseline: dict,
    current: dict,
    tolerance: float = 0.02,
    required_metrics: list[str] | None = None,
) -> list[MetricDelta]:
    """比较同套件的两次运行，返回逐指标结果。

    遍历「基线指标 ∪ 本次指标 ∪ 声明必须产出的指标」，而不是只遍历本次：
    只遍历本次会把「适配器不再产出某指标」误判成「没有退化」。
    """
    suite = current.get("suite", baseline.get("suite", "?"))
    rows: list[MetricDelta] = []

    base_metrics = baseline.get("metrics") or {}
    cur_metrics = current.get("metrics") or {}
    required = list(required_metrics or [])
    names = list(dict.fromkeys([*cur_metrics, *base_metrics, *required]))

    for name in names:
        cur = cur_metrics.get(name)
        base = base_metrics.get(name)
        base_value = None if base is None else base.get("value")

        # 键不存在：适配器不再产出，或声明必跑却没跑——都算失败
        if cur is None:
            rows.append(
                MetricDelta(suite, name, base_value, None, None, None, False, "missing")
            )
            continue

        cur_value = cur.get("value")
        # 键存在但值为 null：本次未产出（如 ride 的 gold_only 模式），不误判
        if cur_value is None or base is None or base_value is None:
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


def gate(
    runs: list[dict],
    baselines: dict[str, dict | None],
    tolerance: float = 0.02,
    required: dict[str, list[str]] | None = None,
    allow_dataset_change: bool = False,
) -> GateResult:
    """对一批运行做门禁判定。

    Args:
        runs: 本次运行的产物列表。
        baselines: suite -> 基线产物（None 表示该套件还没有基线，跳过）。
        tolerance: 相对容差（0.02 = 允许 2% 的相对退化）。
        required: suite -> 该套件必须产出的指标名。
        allow_dataset_change: 数据集变化时只提示、不拦截。

    Returns:
        GateResult：逐指标结果、整体是否通过、以及数据集/版本提示。
    """
    required = required or {}
    result = GateResult()
    for run in runs:
        suite = run["suite"]
        baseline = baselines.get(suite)
        if baseline is None:
            continue

        for row in compare_runs(baseline, run, tolerance, required.get(suite)):
            result.rows.append(row)
            if not row.passed:
                result.passed = False

        for notice in compare_dataset(baseline, run, allow_dataset_change) + compare_revision(baseline, run):
            result.notices.append(notice)
            if notice["level"] == "error":
                result.passed = False
    return result


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


def format_notices(notices: list[dict]) -> str:
    """把提示渲染成 CLI 文本。"""
    lines = []
    for notice in notices:
        mark = "!" if notice["level"] == "error" else "·"
        lines.append(f"  {mark} {notice['message']}")
    return "\n".join(lines)
