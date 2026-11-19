"""evalhub 命令行入口。

    evalhub run --all                     跑全部套件，出汇总报告
    evalhub run --suite driver.intent     只跑一个套件
    evalhub run --all --update-baseline   跑完顺手把本次设为新基线
    evalhub compare <基线run_id> <新run_id>
    evalhub gate                          按基线判定，退化则退出码 1

设计要点：evalhub 不知道任何项目的内部实现，只知道 suites.yaml 里的
「命令 + 产物 + adapter」。所以换评测实现不需要动这里。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from evalhub import adapters, gate as gate_mod, registry, report
from evalhub.schema import ContractError, validate_run

ROOT = Path(__file__).resolve().parent.parent


def load_suites() -> dict:
    """读取 suites.yaml 里的套件定义。"""
    path = ROOT / "suites.yaml"
    if not path.exists():
        raise FileNotFoundError(f"缺少套件配置: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")).get("suites", {}) or {}


def make_run_id(suite: str) -> str:
    """UTC 时间戳 + 套件名，保证全局唯一且可排序。"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{suite}"


def git_rev(project: Path) -> str | None:
    """取项目当前 commit，产物出问题时能回溯到代码版本。"""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def run_suite(name: str, cfg: dict, provider: str | None = None) -> dict:
    """跑一个套件：可选先执行命令，再读产物、翻译、校验、落盘。"""
    project = (ROOT / cfg["project"]).resolve()
    if not project.exists():
        raise FileNotFoundError(f"项目不存在: {project}")

    command = cfg.get("command")
    if command:
        print(f"  $ {command}  (cwd={project.name})")
        subprocess.run(command, cwd=project, shell=True, check=True)

    artifact = project / cfg["artifact"]
    if not artifact.exists():
        raise FileNotFoundError(
            f"{name} 的产物不存在: {artifact}\n"
            f"提示：在 suites.yaml 里给该套件配置 command，或先手动生成产物。"
        )

    options = dict(cfg.get("options") or {})
    if provider:
        options["provider"] = provider

    adapter = adapters.load_adapter(cfg["adapter"])
    run = adapter.adapt(artifact, options)
    run["run_id"] = make_run_id(name)
    run["git_rev"] = git_rev(project)
    validate_run(run)

    path = registry.save_run(run)
    print(f"  → {name}: {path.name}")
    return run


def cmd_run(args) -> int:
    suites = load_suites()
    names = list(suites) if args.all else (args.suite or [])
    if not names:
        print("需要 --all 或 --suite", file=sys.stderr)
        return 2

    unknown = [n for n in names if n not in suites]
    if unknown:
        print(f"未知套件: {unknown}，可选: {sorted(suites)}", file=sys.stderr)
        return 2

    # 先取基线，再跑——否则 --update-baseline 会用自己的结果当基线，永远通过
    baselines = {n: registry.load_baseline(n) for n in names}

    runs = []
    for name in names:
        print(f"[run] {name}")
        runs.append(run_suite(name, suites[name], args.provider))

    rows, passed = gate_mod.gate(runs, baselines, args.tolerance)
    if rows:
        print()
        print(gate_mod.format_rows(rows))
        print(f"\n门禁：{'通过' if passed else '未通过（存在指标退化）'}")

    out = report.write(Path(args.report), runs, rows)
    print(f"报告：{out}")

    if args.update_baseline:
        for run in runs:
            path = registry.save_baseline(run)
            print(f"基线已更新：{path.name}")

    return 0 if passed or not rows else 1


def cmd_compare(args) -> int:
    baseline = registry.load_run(args.baseline)
    current = registry.load_run(args.current)
    if baseline["suite"] != current["suite"]:
        print("两个运行的套件不同，无法对比", file=sys.stderr)
        return 2
    rows = gate_mod.compare_runs(baseline, current, args.tolerance)
    print(gate_mod.format_rows(rows))
    return 0 if all(r.passed for r in rows) else 1


def cmd_gate(args) -> int:
    suites = load_suites()
    names = list(suites) if args.all else (args.suite or [])
    if not names:
        print("需要 --all 或 --suite", file=sys.stderr)
        return 2

    runs = []
    for name in names:
        run = registry.load_run(args.run) if args.run else _latest_run(name)
        if run is None:
            print(f"[skip] {name}: 没有可用的运行记录", file=sys.stderr)
            continue
        runs.append(run)

    baselines = {n: registry.load_baseline(n) for n in names}
    rows, passed = gate_mod.gate(runs, baselines, args.tolerance)
    if not rows:
        print("没有可比对的基线，门禁跳过")
        return 0

    print(gate_mod.format_rows(rows))
    print(f"\n门禁：{'通过' if passed else '未通过（存在指标退化）'}")
    return 0 if passed else 1


def _latest_run(suite: str) -> dict | None:
    """取该套件最近一次运行产物。"""
    for path in registry.list_runs(limit=200):
        run = registry.load_run(str(path))
        if run.get("suite") == suite:
            return run
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evalhub", description="跨项目评测汇总与回归门禁")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="运行套件并生成报告")
    run.add_argument("--all", action="store_true", help="跑全部套件")
    run.add_argument("--suite", action="append", help="指定套件，可重复")
    run.add_argument("--provider", help="driver.intent 选哪一路（rules/llm/hybrid）")
    run.add_argument("--update-baseline", action="store_true", help="把本次设为新基线")
    run.add_argument("--tolerance", type=float, default=0.02, help="相对容差，默认 0.02")
    run.add_argument("--report", default=str(ROOT / "reports" / "summary.html"))
    run.set_defaults(func=cmd_run)

    cmp_ = sub.add_parser("compare", help="对比两次运行")
    cmp_.add_argument("baseline")
    cmp_.add_argument("current")
    cmp_.add_argument("--tolerance", type=float, default=0.02)
    cmp_.set_defaults(func=cmd_compare)

    g = sub.add_parser("gate", help="按基线做回归门禁（CI 用）")
    g.add_argument("--all", action="store_true")
    g.add_argument("--suite", action="append")
    g.add_argument("--run", help="指定 run_id；默认取每套件最近一次")
    g.add_argument("--tolerance", type=float, default=0.02)
    g.set_defaults(func=cmd_gate)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ContractError, FileNotFoundError, KeyError, ValueError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
