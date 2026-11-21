"""门禁判定测试：确认"退化会被拦、改进不会误伤、缺失指标不误判"。"""

from evalhub.gate import compare_runs, gate
from evalhub.report import render


def _run(suite="driver.intent", **metrics):
    return {
        "suite": suite,
        "metrics": {
            name: {"value": value, "higher_is_better": higher}
            for name, (value, higher) in metrics.items()
        },
    }


def test_regression_fails_gate():
    base = _run(accuracy=(0.98, True))
    cur = _run(accuracy=(0.90, True))
    rows, passed = gate([cur], {"driver.intent": base})
    assert passed is False
    assert rows[0].status == "regressed"


def test_small_drop_within_tolerance_passes():
    base = _run(accuracy=(0.98, True))
    cur = _run(accuracy=(0.975, True))  # 掉 0.5%，容差 2%
    rows, passed = gate([cur], {"driver.intent": base}, tolerance=0.02)
    assert passed is True
    assert rows[0].status == "ok"


def test_latency_increase_is_regression():
    base = _run(latency_p95_ms=(900.0, False))
    cur = _run(latency_p95_ms=(1200.0, False))
    rows, passed = gate([cur], {"driver.intent": base})
    assert passed is False
    assert rows[0].status == "regressed"


def test_latency_small_noise_passes():
    base = _run(latency_p95_ms=(900.0, False))
    cur = _run(latency_p95_ms=(910.0, False))  # +1.1%，容差 2%
    _, passed = gate([cur], {"driver.intent": base})
    assert passed is True


def test_missing_metric_is_skipped_not_failed():
    base = _run(execution_accuracy=(0.68, True))
    cur = _run(execution_accuracy=(None, True))
    rows, passed = gate([cur], {"driver.intent": base})
    assert passed is True
    assert rows[0].status == "skipped"


def test_no_baseline_does_not_block():
    rows, passed = gate([_run(accuracy=(0.9, True))], {"driver.intent": None})
    assert rows == []
    assert passed is True


def test_compare_rows_are_serialisable():
    base = _run(accuracy=(0.9, True))
    cur = _run(accuracy=(0.95, True))
    rows = compare_runs(base, cur)
    assert rows[0].as_dict()["status"] == "improved"


def test_report_renders_both_suites():
    base = _run("driver.intent", accuracy=(0.98, True))
    driver = _run("driver.intent", accuracy=(0.90, True))
    ride = _run("ride.nl2sql", execution_accuracy=(0.68, True))
    ride["per_case"] = [{"case_id": "Q09", "passed": False, "detail": {"category": "time_filter"}}]
    rows, _ = gate([driver], {"driver.intent": base})
    html = render([driver, ride], rows)
    assert "driver.intent" in html
    assert "ride.nl2sql" in html
    assert "regressed" in html
    assert "Q09" in html


# --- 指标键消失：只遍历本次指标会把「不再产出」误判成「没退化」 ---

def test_metric_key_disappearing_is_failure():
    base = _run(execution_accuracy=(0.68, True), semantic_accuracy=(1.0, True))
    cur = _run(semantic_accuracy=(1.0, True))  # execution_accuracy 整个键没了
    result = gate([cur], {"driver.intent": base})
    rows = {row.metric: row for row in result.rows}
    assert result.passed is False
    assert rows["execution_accuracy"].status == "missing"
    assert rows["execution_accuracy"].passed is False
    assert rows["semantic_accuracy"].status == "ok"


def test_required_metric_missing_even_without_baseline_metric():
    """基线里也没有的指标，声明 required 后缺失同样算失败。"""
    base = _run(accuracy=(0.98, True))
    cur = _run(accuracy=(0.98, True))
    result = gate([cur], {"driver.intent": base}, required={"driver.intent": ["error_rate"]})
    assert result.passed is False
    assert [row.status for row in result.rows if row.metric == "error_rate"] == ["missing"]


def test_null_value_is_still_skipped_not_missing():
    """值为 null 是「本次没算」，和「键消失」必须区分开。"""
    base = _run(execution_accuracy=(0.68, True))
    cur = _run(execution_accuracy=(None, True))
    result = gate([cur], {"driver.intent": base}, required={"driver.intent": ["execution_accuracy"]})
    assert result.passed is True
    assert result.rows[0].status == "skipped"


# --- 数据集变更：换数据后的涨跌不能归因于代码 ---

def _with_dataset(run, sha, version="v1", git_rev=None):
    run["dataset"] = {"name": "ride_nl2sql", "version": version, "sha256": sha, "cases": 22}
    run["git_rev"] = git_rev
    return run


def test_dataset_change_blocks_gate():
    base = _with_dataset(_run("ride.nl2sql", execution_accuracy=(0.68, True)), "aaaaaaaaaaaa")
    cur = _with_dataset(_run("ride.nl2sql", execution_accuracy=(0.90, True)), "bbbbbbbbbbbb")
    result = gate([cur], {"ride.nl2sql": base})
    assert result.passed is False
    assert [n["code"] for n in result.notices] == ["dataset_changed"]
    assert result.notices[0]["level"] == "error"


def test_dataset_change_allowed_is_warning():
    base = _with_dataset(_run("ride.nl2sql", execution_accuracy=(0.68, True)), "aaaaaaaaaaaa")
    cur = _with_dataset(_run("ride.nl2sql", execution_accuracy=(0.68, True)), "bbbbbbbbbbbb")
    result = gate([cur], {"ride.nl2sql": base}, allow_dataset_change=True)
    assert result.passed is True
    assert result.notices[0]["level"] == "warn"


def test_unknown_dataset_on_one_side_is_not_change():
    """driver 历史产物没有 dataset 字段，缺值不能当成「变了」。"""
    base = _with_dataset(_run("ride.nl2sql", accuracy=(0.9, True)), "aaaaaaaaaaaa")
    cur = _run("ride.nl2sql", accuracy=(0.9, True))
    result = gate([cur], {"ride.nl2sql": base})
    assert result.passed is True
    assert result.notices == []


def test_stale_baseline_is_warning_only():
    base = _with_dataset(_run("ride.nl2sql", accuracy=(0.9, True)), "same", git_rev="305553f")
    cur = _with_dataset(_run("ride.nl2sql", accuracy=(0.9, True)), "same", git_rev="dbe5580")
    result = gate([cur], {"ride.nl2sql": base})
    assert result.passed is True
    assert result.notices[0]["code"] == "stale_baseline"
    assert result.notices[0]["level"] == "warn"


def test_report_renders_dataset_notice():
    base = _with_dataset(_run("ride.nl2sql", accuracy=(0.9, True)), "aaaaaaaaaaaa")
    cur = _with_dataset(_run("ride.nl2sql", accuracy=(0.9, True)), "bbbbbbbbbbbb")
    result = gate([cur], {"ride.nl2sql": base})
    html = render([cur], result.rows, result.notices)
    assert "数据集" in html
    assert "未通过" in html
