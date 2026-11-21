"""历史与趋势测试：一行一次运行、方向取基线、数据指纹变化要提示。"""

from evalhub import registry
from evalhub.cli import _run_from_history
from evalhub.report import render_trend


def _run(run_id: str, value: float, sha: str = "aaaa", **extra_metrics):
    metrics = {"accuracy": {"value": value, "higher_is_better": True}}
    metrics.update(extra_metrics)
    return {
        "run_id": run_id,
        "suite": "driver.intent",
        "git_rev": "abc1234",
        "mode": "hybrid",
        "dataset": {"name": "driver_intent_cases", "sha256": sha},
        "metrics": metrics,
    }


def test_append_and_read_history(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "HISTORY_DIR", tmp_path / "history")
    registry.append_history(_run("20261120T000001-driver.intent", 0.90))
    registry.append_history(_run("20261120T000002-driver.intent", 0.80))

    rows = registry.read_history("driver.intent")
    assert len(rows) == 2
    assert [row["metrics"]["accuracy"] for row in rows] == [0.90, 0.80]
    # 只保留指标值，不把 per_case 拖进历史
    assert "per_case" not in rows[0]

    assert "000002" in registry.read_history("driver.intent", limit=1)[0]["run_id"]


def test_read_history_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "HISTORY_DIR", tmp_path / "history")
    assert registry.read_history("driver.intent") == []


def test_render_trend_shows_values_and_gaps():
    records = [
        {"run_id": "a", "metrics": {"accuracy": 0.90, "macro_f1": 0.88}, "dataset_sha256": "aaaa"},
        {"run_id": "b", "metrics": {"accuracy": 0.91, "macro_f1": None}, "dataset_sha256": "aaaa"},
    ]
    text = render_trend("driver.intent", records)
    assert "0.9000" in text and "0.9100" in text
    assert "—" in text  # 缺失值留空，不补 0
    assert "指纹变过" not in text


def test_render_trend_warns_when_dataset_changed():
    records = [
        {"run_id": "a", "metrics": {"accuracy": 0.9}, "dataset_sha256": "aaaa"},
        {"run_id": "b", "metrics": {"accuracy": 0.7}, "dataset_sha256": "bbbb"},
    ]
    assert "指纹变过" in render_trend("driver.intent", records)


def test_render_trend_empty_message():
    assert "还没有历史记录" in render_trend("driver.intent", [])


def test_run_from_history_takes_direction_from_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "HISTORY_DIR", tmp_path / "history")
    registry.append_history(
        _run(
            "20261120T000001-driver.intent",
            0.98,
            latency_p95_ms={"value": 500.0, "higher_is_better": False},
        )
    )
    baseline = {"metrics": {"latency_p95_ms": {"value": 480.0, "higher_is_better": False}}}

    run = _run_from_history("driver.intent", baseline)
    # history 不存方向，必须从基线补，否则延迟下降会被判成退化
    assert run["metrics"]["latency_p95_ms"]["higher_is_better"] is False
    assert run["metrics"]["latency_p95_ms"]["value"] == 500.0
    assert run["dataset"]["sha256"] == "aaaa"


def test_run_from_history_without_records(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "HISTORY_DIR", tmp_path / "history")
    assert _run_from_history("driver.intent", {}) is None
