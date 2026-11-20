"""快照与漂移测试：重点验证「schema 改名必须被拦下」。"""

import sqlite3
from pathlib import Path

import pytest

from datahub import snapshot


def _make_db(path: Path, rows: int = 3) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, amount REAL, status TEXT)")
    conn.executemany(
        "INSERT INTO orders (id, amount, status) VALUES (?, ?, ?)",
        [(i, 10.0 * i, "completed") for i in range(1, rows + 1)],
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def manifest(tmp_path, monkeypatch):
    db = tmp_path / "ride.db"
    _make_db(db)
    monkeypatch.setattr(snapshot, "SNAPSHOTS_DIR", tmp_path / "snapshots")
    return {
        "name": "demo",
        "version": "v1",
        "kind": "sqlite",
        "project": ".",
        "source": "ride.db",
        "tables": ["orders"],
        "_source_path": db,
    }


def test_build_creates_snapshot_and_meta(manifest):
    payload, meta_path, meta = snapshot.build(manifest)
    assert payload.exists() and meta_path.exists()
    assert meta["tables"]["orders"]["rows"] == 3
    assert len(meta["dataset_sha256"]) == 16
    assert meta["tables"]["orders"]["columns"] == ["id", "amount", "status"]


def test_verify_clean_after_build(manifest):
    snapshot.build(manifest)
    assert snapshot.verify(manifest) == []


def test_verify_flags_renamed_column_as_error(manifest):
    snapshot.build(manifest)
    conn = sqlite3.connect(manifest["_source_path"])
    conn.execute("ALTER TABLE orders RENAME COLUMN amount TO order_amount")
    conn.commit()
    conn.close()

    drifts = snapshot.verify(manifest)
    codes = {d["code"] for d in drifts}
    assert "missing_column" in codes
    assert "extra_column" in codes
    assert any(d["level"] == "error" for d in drifts)


def test_verify_flags_row_delta_as_warning(manifest):
    snapshot.build(manifest)
    conn = sqlite3.connect(manifest["_source_path"])
    conn.execute("INSERT INTO orders (id, amount, status) VALUES (99, 1.0, 'completed')")
    conn.commit()
    conn.close()

    drifts = snapshot.verify(manifest)
    assert [d["code"] for d in drifts] == ["row_delta"]
    assert drifts[0]["level"] == "warn"


def test_verify_without_snapshot_raises(manifest):
    with pytest.raises(FileNotFoundError):
        snapshot.verify(manifest)


def test_file_snapshot_detects_content_change(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "SNAPSHOTS_DIR", tmp_path / "snapshots")
    data = tmp_path / "golden.json"
    data.write_text('{"questions": [1, 2, 3]}', encoding="utf-8")
    manifest = {
        "name": "golden",
        "version": "v1",
        "kind": "file",
        "project": ".",
        "source": "golden.json",
        "count_path": "questions",
        "_source_path": data,
    }

    _, _, meta = snapshot.build(manifest)
    assert meta["cases"] == 3

    data.write_text('{"questions": [1, 2, 3, 4]}', encoding="utf-8")
    drifts = snapshot.verify(manifest)
    assert drifts[0]["code"] == "content_changed"
    assert drifts[0]["level"] == "warn"
