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
