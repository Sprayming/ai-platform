"""rag adapter 测试：一个项目两种历史格式，必须都翻译成同一契约。"""

from pathlib import Path

import pytest

from evalhub.adapters import rag_ragas
from evalhub.schema import validate_run

FIXTURES = Path(__file__).resolve().parent / "fixtures"
OPTIONS = {"dataset": "golden_set.json", "project_path": str(FIXTURES)}


def _validated(run):
    run["run_id"] = "20261119T000000-rag.ragas"
    validate_run(run)
    return run


def test_ragas_format_maps_four_metrics():
    run = _validated(rag_ragas.adapt(FIXTURES / "rag_ragas.json", OPTIONS))
    assert run["suite"] == "rag.ragas"
    assert run["mode"] == "full_hybrid"
    assert run["metrics"]["faithfulness"]["value"] == pytest.approx(1.0)
    assert run["metrics"]["context_precision"]["value"] == pytest.approx(0.898, abs=1e-4)
    assert all(m["higher_is_better"] is True for m in run["metrics"].values())


def test_dataset_fingerprint_from_golden_set():
    run = rag_ragas.adapt(FIXTURES / "rag_ragas.json", OPTIONS)
    assert run["dataset"]["cases"] == 3
    assert len(run["dataset"]["sha256"]) == 12
    assert run["dataset"]["version"] == "unversioned"


def test_ragas_format_has_no_per_case():
    run = rag_ragas.adapt(FIXTURES / "rag_ragas.json", OPTIONS)
    assert run["per_case"] == []


def test_qa_gate_format_converts_percentage():
    run = _validated(rag_ragas.adapt(FIXTURES / "rag_qa_gate.json", OPTIONS))
    assert run["mode"] == "qa_gate"
    # 旧脚本写的是 90.0，统一成 0.9
    assert run["metrics"]["pass_rate"]["value"] == pytest.approx(0.9)
    assert run["metrics"]["avg_latency_ms"]["value"] == pytest.approx(812.4)
    assert run["metrics"]["avg_latency_ms"]["higher_is_better"] is False


def test_qa_gate_per_case_and_dataset_count():
    run = rag_ragas.adapt(FIXTURES / "rag_qa_gate.json", OPTIONS)
    assert run["dataset"]["cases"] == 10  # 以实际跑的条数为准，不是金标集声明数
    failed = [c for c in run["per_case"] if not c["passed"]]
    assert [c["case_id"] for c in failed] == ["2"]
    assert failed[0]["detail"]["reasons"]


def test_unknown_format_raises():
    with pytest.raises(ValueError):
        rag_ragas.adapt(FIXTURES / "driver_report.json", OPTIONS)
