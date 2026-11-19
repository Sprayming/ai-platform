"""运行产物的落盘、基线与历史查询。

目录约定：
    reports/<run_id>.json      每次运行的统一产物（不进版本库）
    baselines/<suite>.json     该套件当前基线（进版本库，用于 CI 门禁）
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "reports"
BASELINES_DIR = ROOT / "baselines"


def _ensure_dirs() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    BASELINES_DIR.mkdir(parents=True, exist_ok=True)


def save_run(run: dict) -> Path:
    """写入 reports/<run_id>.json。"""
    _ensure_dirs()
    path = REPORTS_DIR / f"{run['run_id']}.json"
    path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_run(identifier: str) -> dict:
    """按 run_id 或路径加载一次运行（也接受 baselines/ 下的文件名）。"""
    path = Path(identifier)
    if not path.exists():
        path = REPORTS_DIR / f"{identifier}.json"
    if not path.exists():
        path = BASELINES_DIR / identifier
    if not path.exists():
        raise FileNotFoundError(f"找不到运行记录: {identifier}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_baseline(run: dict, name: str = "latest") -> Path:
    """把一次运行设为该套件基线（默认每个 suite 一份 latest）。"""
    _ensure_dirs()
    path = BASELINES_DIR / f"{run['suite']}.{name}.json"
    path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_baseline(suite: str, name: str = "latest") -> dict | None:
    """读取套件基线；不存在返回 None（首次运行不拦）。"""
    path = BASELINES_DIR / f"{suite}.{name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_runs(limit: int = 20) -> list[Path]:
    """按时间倒序列出最近的运行产物。"""
    if not REPORTS_DIR.exists():
        return []
    return sorted(REPORTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
