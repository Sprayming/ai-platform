"""口径对账测试：SQL 能跑、基准值能比、缺东西不假装算。"""

import sqlite3

import pytest

from datahub import manifest as manifest_mod
from datahub import metrics as metrics_mod
from datahub import reconcile as reconcile_mod
from datahub import snapshot as snapshot_mod

MANIFEST = """
name: demo
version: v1
project: "."
kind: sqlite
source: data.db
owner: tester
"""

BODY = """
metrics:
  - id: demo_rate
    name: 演示比例
    unit: ratio
    higher_is_better: true
    owner: tester
    canonical: a
    dataset: demo
    variants:
      - key: a
        name: 唯一口径
        numerator: 命中的行
        denominator: 总行数
        sql: "SELECT 1.0 * SUM(x) / 40 FROM t"
        expected: {expected}
  - id: no_sql_rate
    name: 无 SQL 指标
    unit: ratio
    higher_is_better: true
    owner: tester
    canonical: a
    variants:
      - key: a
        name: 唯一口径
        numerator: x
        denominator: y
"""


def _setup(tmp_path, monkeypatch, expected: float = 0.25):
    manifests = tmp_path / "manifests"
    snapshots = tmp_path / "snapshots"
    manifests.mkdir()
    snapshots.mkdir()
    (manifests / "demo.yaml").write_text(MANIFEST, encoding="utf-8")
    monkeypatch.setattr(manifest_mod, "MANIFESTS_DIR", manifests)
    monkeypatch.setattr(manifest_mod, "ROOT", tmp_path)
    monkeypatch.setattr(snapshot_mod, "SNAPSHOTS_DIR", snapshots)

    path = tmp_path / "metrics.yaml"
    path.write_text("version: v2\nowner: tester\n" + BODY.format(expected=expected), encoding="utf-8")
    monkeypatch.setattr(metrics_mod, "METRICS_PATH", path)

    db = snapshots / "demo@v1.payload.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(1,), (2,), (3,), (4,)])
    conn.commit()
    conn.close()


def test_reconcile_matches_expected(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, expected=0.25)  # (1+2+3+4)/40 = 0.25
    rows = reconcile_mod.reconcile("demo")
    assert len(rows) == 1  # 没有 sql 的指标不参与
    assert rows[0]["passed"] is True
    assert rows[0]["actual"] == pytest.approx(0.25)


def test_reconcile_detects_drift(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, expected=0.40)
    rows = reconcile_mod.reconcile("demo")
    assert rows[0]["passed"] is False
    assert rows[0]["diff"] == pytest.approx(-0.15)


def test_reconcile_within_tolerance(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, expected=0.25005)
    assert reconcile_mod.reconcile("demo", tolerance=1e-4)[0]["passed"] is True


def test_reconcile_reports_bad_sql(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    text = metrics_mod.METRICS_PATH.read_text(encoding="utf-8")
    metrics_mod.METRICS_PATH.write_text(
        text.replace("SELECT 1.0 * SUM(x) / 40 FROM t", "SELECT * FROM no_such_table"),
        encoding="utf-8",
    )
    rows = reconcile_mod.reconcile("demo")
    assert rows[0]["passed"] is False
    assert "SQL 执行失败" in rows[0]["error"]


def test_reconcile_without_snapshot_raises(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    for path in snapshot_mod.SNAPSHOTS_DIR.glob("*.sqlite"):
        path.unlink()
    with pytest.raises(FileNotFoundError):
        reconcile_mod.reconcile("demo")


def test_variants_with_sql_filters_by_dataset():
    pairs = metrics_mod.variants_with_sql("ride_ops")
    assert pairs, "仓库里至少应有 ride_ops 的可对账口径"
    assert all(item["dataset"] == "ride_ops" for item, _ in pairs)
