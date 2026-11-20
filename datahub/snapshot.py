"""数据集快照与漂移校验。

两个关键判断：

1. **哈希算内容，不算文件字节。** SQLite 文件会因页布局、VACUUM、插入顺序变化而
   字节不同，但数据可以完全一样；按「每张表的行内容」算哈希，才能回答
   「数据到底变没变」。

2. **verify 主要防 schema 漂移，而不是防数据更新。** 项目历史上真的出过
   「schema 改了列名、四个消费模块全挂」的事故，所以缺列/改列必须是 error，
   新增列和行数变化只是 warn。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS_DIR = ROOT / "snapshots"


def _ensure_dir() -> None:
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
    return [row[1] for row in rows]


def _table_state(conn: sqlite3.Connection, table: str) -> dict:
    """一张表的行数、列名与内容哈希。"""
    columns = _table_columns(conn, table)
    if not columns:
        return {"rows": 0, "columns": [], "sha256": ""}

    cursor = conn.execute(f"SELECT * FROM {_quote(table)}")
    # 按行排序后哈希，避免受物理顺序影响
    lines = sorted("|".join(repr(value) for value in row) for row in cursor.fetchall())
    digest = hashlib.sha256()
    digest.update(table.encode("utf-8"))
    for line in lines:
        digest.update(line.encode("utf-8", "backslashreplace"))
    return {
        "rows": len(lines),
        "columns": columns,
        "sha256": digest.hexdigest()[:16],
    }


def _business_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [row[0] for row in rows]


def sqlite_state(path: Path, tables: list[str] | None = None) -> dict:
    """计算一个 SQLite 库的状态（只读打开，不改原库）。"""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = tables or _business_tables(conn)
        state = {name: _table_state(conn, name) for name in names}
    finally:
        conn.close()

    digest = hashlib.sha256()
    for name in sorted(state):
        digest.update(f"{name}:{state[name]['sha256']}\n".encode("utf-8"))
    return {"tables": state, "dataset_sha256": digest.hexdigest()[:16]}


def file_state(path: Path, count_path: str | None = None) -> dict:
    """计算一个普通文件的状态；count_path 指向条目数组，便于记录条数。"""
    raw = path.read_bytes()
    state = {"sha256": hashlib.sha256(raw).hexdigest()[:16], "bytes": len(raw)}
    if not count_path:
        return state

    suffix = path.suffix.lower()
    if suffix == ".json":
        node = json.loads(raw.decode("utf-8"))
    elif suffix in {".yml", ".yaml"}:
        import yaml

        node = yaml.safe_load(raw.decode("utf-8"))
    else:
        return state

    for key in count_path.split("."):
        node = node[key] if isinstance(node, dict) else node[int(key)]
    state["cases"] = len(node)
    return state


def current_state(manifest: dict) -> dict:
    """按 manifest 计算当前数据源的状态。"""
    path = manifest["_source_path"]
    if manifest["kind"] == "sqlite":
        return sqlite_state(path, manifest.get("tables") or None)
    return file_state(path, manifest.get("count_path"))


def snapshot_paths(manifest: dict) -> tuple[Path, Path]:
    """快照的载荷与元数据路径。

    载荷带 .payload 前缀是为了和元数据区分开——否则 json 类型的数据集
    会和自己的元数据同名。
    """
    suffix = ".sqlite" if manifest["kind"] == "sqlite" else manifest["_source_path"].suffix
    base = f"{manifest['name']}@{manifest['version']}"
    return (
        SNAPSHOTS_DIR / f"{base}.payload{suffix}",
        SNAPSHOTS_DIR / f"{base}.meta.json",
    )


def build(manifest: dict) -> tuple[Path, Path, dict]:
    """生成快照：拷贝只读载荷 + 写元数据。"""
    _ensure_dir()
    source = manifest["_source_path"]
    if not source.exists():
        raise FileNotFoundError(f"{manifest['name']} 的数据源不存在: {source}")

    payload_path, meta_path = snapshot_paths(manifest)
    if payload_path.exists():
        payload_path.unlink()

    if manifest["kind"] == "sqlite":
        conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        try:
            conn.execute(f"VACUUM INTO '{payload_path.as_posix()}'")
        finally:
            conn.close()
        state = sqlite_state(payload_path, manifest.get("tables") or None)
    else:
        shutil.copy2(source, payload_path)
        state = file_state(payload_path, manifest.get("count_path"))

    meta = {
        "name": manifest["name"],
        "version": manifest["version"],
        "kind": manifest["kind"],
        "project": manifest["project"],
        "source": manifest["source"],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "seed": manifest.get("seed"),
        **state,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload_path, meta_path, meta


def load_meta(manifest: dict) -> dict | None:
    """读取该数据集当前版本的快照元数据。"""
    _, meta_path = snapshot_paths(manifest)
    if not meta_path.exists():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


def verify(manifest: dict) -> list[dict]:
    """把当前数据源与快照元数据比对，返回漂移列表。

    约定：缺表/缺列 = error（会让消费方直接报错）；多表/多列/行数变化 = warn。
    """
    meta = load_meta(manifest)
    if meta is None:
        raise FileNotFoundError(f"{manifest['name']} 还没有快照，先跑 datahub snapshot build")

    if manifest["kind"] == "file":
        state = file_state(manifest["_source_path"], manifest.get("count_path"))
        drifts = []
        if state["sha256"] != meta.get("sha256"):
            drifts.append({
                "level": "warn",
                "code": "content_changed",
                "message": f"文件内容已变化（{meta.get('sha256')} → {state['sha256']}）",
            })
        return drifts

    current = sqlite_state(manifest["_source_path"], manifest.get("tables") or None)
    return _diff_sqlite(meta.get("tables", {}), current["tables"])


def _diff_sqlite(before: dict, after: dict) -> list[dict]:
    drifts: list[dict] = []

    for table in sorted(set(before) - set(after)):
        drifts.append({"level": "error", "code": "missing_table", "message": f"缺少表 {table}"})
    for table in sorted(set(after) - set(before)):
        drifts.append({"level": "warn", "code": "extra_table", "message": f"新增表 {table}"})

    for table in sorted(set(before) & set(after)):
        old, new = before[table], after[table]
        old_cols, new_cols = set(old["columns"]), set(new["columns"])

        for col in sorted(old_cols - new_cols):
            drifts.append({
                "level": "error",
                "code": "missing_column",
                "message": f"{table}.{col} 不存在（很可能被改名或删除）",
            })
        for col in sorted(new_cols - old_cols):
            drifts.append({
                "level": "warn",
                "code": "extra_column",
                "message": f"{table}.{col} 是新增列",
            })

        if old["rows"] != new["rows"]:
            drifts.append({
                "level": "warn",
                "code": "row_delta",
                "message": f"{table} 行数 {old['rows']} → {new['rows']}",
            })
        elif old["sha256"] != new["sha256"] and old_cols == new_cols:
            drifts.append({
                "level": "warn",
                "code": "content_changed",
                "message": f"{table} 行数不变但内容变化（{old['sha256']} → {new['sha256']}）",
            })

    return drifts
