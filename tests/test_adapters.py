"""adapter 与契约测试。

重点验证两件事：
1. 两个项目格式不同，但翻译出来的产物必须同时通过同一份契约
2. 原始报告里「缺失的指标」要变成 null，而不是让字段消失
"""

import json
from pathlib import Path

import pytest

from evalhub.adapters import driver_intent, ride_nl2sql
from evalhub.schema import ContractError, validate_run

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_driver_adapter_produces_valid_run():
    run = driver_intent.adapt(FIXTURES / "driver_report.json", {"provider": "hybrid"})
    run["run_id"] = "20261119T000000-driver.intent"
    validate_run(run)

    assert run["suite"] == "driver.intent"
    assert run["mode"] == "hybrid"
    assert run["metrics"]["accuracy"]["value"] == pytest.approx(0.9821)
    assert run["metrics"]["latency_p95_ms"]["higher_is_better"] is False
    assert run["dataset"]["cases"] == 56
    assert run["dataset"]["sha256"] == "53b0679aba7a"


def test_driver_adapter_maps_failures_to_per_case():
    run = driver_intent.adapt(FIXTURES / "driver_report.json", {"provider": "hybrid"})
    assert len(run["per_case"]) == 1
    case = run["per_case"][0]
    assert case["case_id"] == "refund-02"
    assert case["passed"] is False
    assert case["detail"]["expected"] == "refund_request"
    assert case["detail"]["actual"] == "order_query"


def test_driver_adapter_rejects_broken_provider():
    with pytest.raises(ValueError):
        driver_intent.adapt(FIXTURES / "driver_report.json", {"provider": "jev"})


def test_ride_adapter_keeps_missing_metric_as_null():
    run = ride_nl2sql.adapt(FIXTURES / "ride_report.json")
    run["run_id"] = "20261119T000000-ride.nl2sql"
    validate_run(run)

    assert run["metrics"]["execution_accuracy"]["value"] == pytest.approx(0.6818)
    # semantic_accuracy 本次没算，必须显式为 null，而不是字段消失
    assert "semantic_accuracy" in run["metrics"]
    assert run["metrics"]["semantic_accuracy"]["value"] is None


def test_ride_adapter_per_case_records_failures():
    run = ride_nl2sql.adapt(FIXTURES / "ride_report.json")
    failed = [c for c in run["per_case"] if not c["passed"]]
    assert [c["case_id"] for c in failed] == ["Q09"]


def test_contract_rejects_missing_suite():
    run = ride_nl2sql.adapt(FIXTURES / "ride_report.json")
    run["run_id"] = "20261119T000000-ride.nl2sql"
    run.pop("suite")
    with pytest.raises(ContractError):
        validate_run(run)


def test_contract_rejects_bad_metric_shape():
    run = ride_nl2sql.adapt(FIXTURES / "ride_report.json")
    run["run_id"] = "20261119T000000-ride.nl2sql"
    run["metrics"]["execution_accuracy"] = 0.68  # 必须是对象，不是裸数字
    with pytest.raises(ContractError):
        validate_run(run)


def test_both_projects_share_one_contract():
    """核心主张：两套完全不同的原始报告，翻译后是同一个结构。"""
    driver = driver_intent.adapt(FIXTURES / "driver_report.json", {"provider": "hybrid"})
    ride = ride_nl2sql.adapt(FIXTURES / "ride_report.json")
    for run, suite in ((driver, "driver.intent"), (ride, "ride.nl2sql")):
        run["run_id"] = f"20261119T000000-{suite}"
        validate_run(run)
        assert {"dataset", "metrics", "per_case"} <= set(run)
        assert json.dumps(run["metrics"], sort_keys=True)
