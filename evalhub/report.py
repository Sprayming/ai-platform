"""跨套件汇总报告（自包含 HTML，零外网依赖）。

设计目标：一屏看清「三个项目这次谁掉了」。
所以不堆图表，只做一张按套件分组的指标表 + 失败样本清单。
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from evalhub.gate import MetricDelta

STATUS_COLOR = {
    "ok": "#6b7280",
    "improved": "#0f766e",
    "regressed": "#b91c1c",
    "skipped": "#9ca3af",
}

CSS = """
* { box-sizing: border-box; }
body { margin: 0; padding: 28px 32px; background: #f6f7f9; color: #111827;
       font-family: -apple-system, "PingFang SC", "Helvetica Neue", Arial, sans-serif;
       font-size: 13px; line-height: 1.5; }
h1 { font-size: 20px; margin: 0 0 4px; letter-spacing: .5px; }
.meta { color: #6b7280; font-size: 12px; margin-bottom: 22px; }
.card { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px;
        margin-bottom: 18px; overflow: hidden; }
.card-head { padding: 12px 16px; border-bottom: 1px solid #eef0f3; font-size: 12px; color: #4b5563; }
.card-head b { font-size: 14px; color: #111827; margin-right: 6px; }
.sep { color: #cbd5e1; margin: 0 5px; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 7px 16px; text-align: left; border-bottom: 1px solid #f3f4f6; }
th { font-size: 11px; color: #6b7280; font-weight: 500; text-transform: uppercase;
     letter-spacing: .4px; background: #fbfcfd; }
td.num { text-align: right; font-variant-numeric: tabular-nums; width: 110px; }
.status { font-weight: 600; }
.fail { padding: 10px 16px; border-top: 1px solid #f3f4f6; }
.fail code { background: #f6f7f9; padding: 1px 5px; border-radius: 3px; font-size: 11.5px; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 11px; }
.pass { background: #ecfdf5; color: #047857; }
.block { background: #fef2f2; color: #b91c1c; }
.empty { color: #9ca3af; padding: 12px 16px; }
"""


def _fmt(value: float | None, unit: str = "") -> str:
    if value is None:
        return "—"
    if unit == "ms":
        return f"{value:.1f} ms"
    return f"{value:.4f}"


def _delta_text(delta: float | None, unit: str = "") -> str:
    if delta is None:
        return "—"
    sign = "+" if delta > 0 else ""
    if unit == "ms":
        return f"{sign}{delta:.1f} ms"
    return f"{sign}{delta:.4f}"


def render(runs: list[dict], deltas: list[MetricDelta] | None = None) -> str:
    """把一次批量运行渲染成 HTML。"""
    deltas = deltas or []
    by_suite: dict[str, list[MetricDelta]] = {}
    for row in deltas:
        by_suite.setdefault(row.suite, []).append(row)

    overall = "通过" if all(r.passed for r in deltas) else "未通过"
    overall_cls = "pass" if all(r.passed for r in deltas) else "block"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    parts = [
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>",
        "<title>Eval Report</title><style>",
        CSS,
        "</style></head><body>",
        f"<h1>评测汇总报告</h1>",
        f"<div class='meta'>生成于 {now}　|　套件 {len(runs)} 个　|　门禁结果",
        f" <span class='badge {overall_cls}'>{overall}</span></div>",
    ]

    for run in runs:
        ds = run.get("dataset", {})
        parts.append("<div class='card'>")
        parts.append("<div class='card-head'>")
        parts.append(f"<b>{html.escape(run['suite'])}</b>")
        bits = [html.escape(run.get("project", ""))]
        bits.append(
            f"数据集 {html.escape(str(ds.get('name', '')))} "
            f"{html.escape(str(ds.get('version', '')))} · {ds.get('cases', 0)} 条"
        )
        if run.get("model"):
            bits.append(f"模型 {html.escape(str(run['model']))}")
        if run.get("mode"):
            bits.append(html.escape(str(run["mode"])))
        for bit in bits:
            parts.append("<span class='sep'>·</span>" + bit)
        parts.append("</div>")

        suite_rows = by_suite.get(run["suite"], [])
        if suite_rows:
            parts.append("<table><tr><th>指标</th><th class='num'>基线</th>"
                         "<th class='num'>本次</th><th class='num'>变化</th>"
                         "<th class='num'>判定</th></tr>")
            for row in suite_rows:
                unit = "ms" if row.metric.endswith("_ms") else ""
                color = STATUS_COLOR.get(row.status, "#6b7280")
                parts.append(
                    "<tr>"
                    f"<td>{html.escape(row.metric)}</td>"
                    f"<td class='num'>{_fmt(row.baseline, unit)}</td>"
                    f"<td class='num'>{_fmt(row.current, unit)}</td>"
                    f"<td class='num'>{_delta_text(row.delta, unit)}</td>"
                    f"<td class='num status' style='color:{color}'>{row.status}</td>"
                    "</tr>"
                )
            parts.append("</table>")
        else:
            parts.append("<div class='empty'>尚无基线，本次为新基线。</div>")

        failures = [c for c in run.get("per_case", []) if not c.get("passed")]
        if failures:
            parts.append(f"<div class='fail'><b>失败样本 {len(failures)} 条：</b> ")
            shown = failures[:8]
            items = []
            for case in shown:
                detail = case.get("detail") or {}
                bits = [html.escape(str(case.get("case_id")))]
                if detail.get("expected") or detail.get("actual"):
                    bits.append(
                        f"{html.escape(str(detail.get('expected')))} → "
                        f"{html.escape(str(detail.get('actual')))}"
                    )
                if detail.get("category"):
                    bits.append(html.escape(str(detail["category"])))
                items.append("<code>" + " · ".join(bits) + "</code>")
            parts.append("　".join(items))
            if len(failures) > len(shown):
                parts.append(f"　… 另有 {len(failures) - len(shown)} 条")
            parts.append("</div>")

        parts.append("</div>")

    parts.append("</body></html>")
    return "".join(parts)


def write(path: str | Path, runs: list[dict], deltas: list[MetricDelta] | None = None) -> Path:
    """渲染并写入文件。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(runs, deltas), encoding="utf-8")
    return target
