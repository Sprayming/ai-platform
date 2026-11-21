"""strict 前置校验：内容、载荷、质量与指标对账任一失败都不能进评测。"""

import sqlite3

from datahub import manifest as manifest_mod
from datahub import preflight
from datahub import snapshot


def _manifest(tmp_path, monkeypatch):
    db = tmp_path / "demo.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT)")
    conn.execute("INSERT INTO orders VALUES (1, 'completed')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(snapshot, "SNAPSHOTS_DIR", tmp_path / "snapshots")
    return {
        "name": "demo",
        "version": "v1",
        "kind": "sqlite",
        "project": ".",
        "source": "demo.db",
        "tables": ["orders"],
        "quality": {"non_empty_tables": ["orders"]},
        "_source_path": db,
    }


def test_strict_passes_with_matching_source_payload_and_quality(tmp_path, monkeypatch):
    item = _manifest(tmp_path, monkeypatch)
    snapshot.build(item)
    assert preflight.check_dataset(item) == []


def test_strict_turns_content_update_into_error(tmp_path, monkeypatch):
    item = _manifest(tmp_path, monkeypatch)
    snapshot.build(item)
    conn = sqlite3.connect(item["_source_path"])
    conn.execute("UPDATE orders SET status = 'cancelled' WHERE id = 1")
    conn.commit()
    conn.close()

    problems = preflight.check_dataset(item)
    assert any(row["code"] == "content_changed" for row in problems)
    assert all(row["level"] == "error" for row in problems)


def test_strict_rejects_empty_required_table(tmp_path, monkeypatch):
    item = _manifest(tmp_path, monkeypatch)
    snapshot.build(item)
    conn = sqlite3.connect(item["_source_path"])
    conn.execute("DELETE FROM orders")
    conn.commit()
    conn.close()

    assert any(row["code"] == "empty_table" for row in preflight.check_dataset(item))


def test_strict_rejects_missing_payload(tmp_path, monkeypatch):
    item = _manifest(tmp_path, monkeypatch)
    payload, _, _ = snapshot.build(item)
    payload.unlink()
    assert any(row["code"] == "missing_payload" for row in preflight.check_dataset(item))


def test_strict_includes_metric_reconciliation(tmp_path, monkeypatch):
    item = _manifest(tmp_path, monkeypatch)
    snapshot.build(item)
    monkeypatch.setattr(preflight.metrics_mod, "variants_with_sql", lambda name: [({}, {})])
    monkeypatch.setattr(preflight.reconcile_mod, "reconcile", lambda name, tolerance: [{
        "metric": "demo_rate", "variant": "default", "expected": 1.0,
        "actual": 0.8, "diff": -0.2, "passed": False, "error": None,
    }])
    problems = preflight.check_dataset(item)
    assert [row["code"] for row in problems] == ["metric_mismatch"]


def test_every_repo_suite_has_exactly_one_evaluation_set():
    for suite in ("driver.intent", "rag.ragas", "ride.nl2sql"):
        assets = manifest_mod.for_suite(suite)
        roles = [consumer["role"] for _, consumer in assets]
        assert roles.count("evaluation_set") == 1
        assert roles[0] == "evaluation_set"


def test_ride_evaluation_database_is_not_business_metrics_database():
    assets = manifest_mod.load_all()
    assert assets["ride_eval_ops"]["source"] == "data/eval_nl2sql.db"
    assert assets["ride_ops"]["source"] == "data/ride_hailing.db"
