"""evalhub 命令行入口。

    evalhub run --all                     跑全部套件，出汇总报告
    evalhub run --suite driver.intent     只跑一个套件
    evalhub run --all --update-baseline   跑完顺手把本次设为新基线
    evalhub compare <基线run_id> <新run_id>
    evalhub gate                          按基线判定，退化则退出码 1
    evalhub trend                         看最近若干次运行的指标走势

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

from datahub import manifest as dataset_manifest
from datahub import preflight as dataset_preflight
from datahub import snapshot as dataset_snapshot
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


def _required_metrics(suites: dict, names: list[str]) -> dict[str, list[str]]:
    """取各套件声明必须产出的指标；未声明就是空列表（退化为按并集比对）。"""
    return {name: list((suites.get(name) or {}).get("required_metrics") or []) for name in names}


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


def _verify_data(names: list[str]) -> bool:
    """在任何评测命令执行前，校验这些套件声明的全部真实输入。"""
    failed = 0
    checked: set[str] = set()
    for suite in names:
        assets = dataset_manifest.for_suite(suite)
        evaluation_sets = [pair for pair in assets if pair[1]["role"] == "evaluation_set"]
        if len(evaluation_sets) != 1:
            print(f"[data] {suite}: 必须登记且只登记一个 evaluation_set", file=sys.stderr)
            failed += 1
            continue

        print(f"[data] {suite}")
        for item, _ in assets:
            if item["name"] in checked:
                continue
            checked.add(item["name"])
            problems = dataset_preflight.check_dataset(item)
            if not problems:
                print(f"  [ok] {item['name']}@{item['version']}")
                continue
            for problem in problems:
                print(f"  [block] {item['name']}: {problem['message']}", file=sys.stderr)
                failed += 1

    if failed:
        print(f"数据前置校验未通过（{failed} 个问题），未执行任何评测", file=sys.stderr)
    return failed == 0


def _state_fingerprint(state: dict) -> str:
    return state.get("dataset_sha256") or state.get("sha256") or ""


def _attach_lineage(run: dict, suite: str, cfg: dict, provider: str | None = None) -> None:
    """把 adapter 产物接到注册表，并固化数据资产与实验配置。"""
    registered = dataset_manifest.for_suite(suite)
    evaluation_sets = [pair for pair in registered if pair[1]["role"] == "evaluation_set"]
    if len(evaluation_sets) != 1:
        raise ValueError(f"{suite} 必须登记且只登记一个 evaluation_set")

    assets = []
    evaluation_sha = ""
    evaluation_name = ""
    evaluation_version = ""
    for item, consumer in registered:
        meta = dataset_snapshot.load_meta(item)
        if meta is None:
            raise ValueError(f"{item['name']} 缺少快照元数据")
        expected_sha = _state_fingerprint(meta)
        # command 可能重建评测库；执行后再确认它仍与登记快照一致。
        actual_sha = _state_fingerprint(dataset_snapshot.current_state(item))
        if actual_sha != expected_sha:
            raise ValueError(
                f"{item['name']} 在评测后与登记快照不一致：{expected_sha} → {actual_sha}；"
                "请确认变更并升版本、重建快照"
            )
        assets.append({
            "name": item["name"],
            "version": item["version"],
            "sha256": expected_sha,
            "role": consumer["role"],
        })
        if consumer["role"] == "evaluation_set":
            evaluation_name = item["name"]
            evaluation_version = item["version"]
            evaluation_sha = expected_sha

    dataset = run.get("dataset") or {}
    if dataset.get("name") != evaluation_name:
        raise ValueError(
            f"{suite} 主数据集不一致：报告={dataset.get('name')}，注册表={evaluation_name}"
        )
    raw_sha = dataset.get("sha256") or ""
    if raw_sha and raw_sha not in {evaluation_sha, evaluation_sha[:12]}:
        raise ValueError(
            f"{suite} 评测集指纹不一致：报告={raw_sha}，注册表={evaluation_sha}"
        )
    raw_version = str(dataset.get("version") or "")
    if raw_version not in {"", "unknown", "unversioned", evaluation_version}:
        raise ValueError(
            f"{suite} 评测集版本不一致：报告={raw_version}，注册表={evaluation_version}"
        )
    dataset["version"] = evaluation_version
    dataset["sha256"] = evaluation_sha
    run["data_assets"] = assets

    options = dict(cfg.get("options") or {})
    options.pop("project_path", None)
    if provider:
        options["provider"] = provider
    experiment_config = dict((run.get("experiment") or {}).get("config") or {})
    experiment_config.update({
        "adapter": cfg["adapter"],
        "artifact": cfg["artifact"],
        "command": cfg.get("command") or "",
        "options": options,
    })
    run["experiment"] = {"config": experiment_config}


def run_suite(name: str, cfg: dict, provider: str | None = None) -> dict:
    """跑一个套件：可选先执行命令，再读产物、翻译、挂接血缘、校验、落盘。"""
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
    # adapter 可能需要读项目内其它文件（比如金标集算数据集指纹）
    options.setdefault("project_path", str(project))
    if provider:
        options["provider"] = provider

    adapter = adapters.load_adapter(cfg["adapter"])
    run = adapter.adapt(artifact, options)
    run["run_id"] = make_run_id(name)
    run["git_rev"] = git_rev(project)
    _attach_lineage(run, name, cfg, provider)
    validate_run(run)

    path = registry.save_run(run)
    registry.append_history(run)
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

    if not _verify_data(names):
        return 1

    # 先取基线，再跑——否则 --update-baseline 会用自己的结果当基线，永远通过
    baselines = {n: registry.load_baseline(n) for n in names}

    runs = []
    for name in names:
        print(f"[run] {name}")
        runs.append(run_suite(name, suites[name], args.provider))

    result = gate_mod.gate(
        runs,
        baselines,
        args.tolerance,
        required=_required_metrics(suites, names),
        allow_dataset_change=args.allow_dataset_change,
    )
    if result.rows:
        print()
        print(gate_mod.format_rows(result.rows))
    if result.notices:
        print()
        print(gate_mod.format_notices(result.notices))
    if result.rows:
        print(f"\n门禁：{'通过' if result.passed else '未通过（指标退化、指标缺失或数据集变更）'}")

    out = report.write(Path(args.report), runs, result.rows, result.notices)
    print(f"报告：{out}")

    if args.update_baseline:
        for run in runs:
            path = registry.save_baseline(run)
            print(f"基线已更新：{path.name}")

    return 0 if result.passed or not result.rows else 1


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

    baselines = {n: registry.load_baseline(n) for n in names}
    runs = []
    for name in names:
        if args.from_history:
            run = _run_from_history(name, baselines.get(name) or {})
        else:
            run = registry.load_run(args.run) if args.run else _latest_run(name)
        if run is None:
            print(f"[skip] {name}: 没有可用的运行记录", file=sys.stderr)
            continue
        runs.append(run)

    result = gate_mod.gate(
        runs,
        baselines,
        args.tolerance,
        required=_required_metrics(suites, names),
        allow_dataset_change=args.allow_dataset_change,
    )
    if not result.rows:
        print("没有可比对的基线，门禁跳过")
        return 0

    print(gate_mod.format_rows(result.rows))
    if result.notices:
        print()
        print(gate_mod.format_notices(result.notices))
    print(f"\n门禁：{'通过' if result.passed else '未通过（指标退化、指标缺失或数据集变更）'}")
    return 0 if result.passed else 1


def _latest_run(suite: str) -> dict | None:
    """取该套件最近一次运行产物。"""
    for path in registry.list_runs(limit=200):
        run = registry.load_run(str(path))
        if run.get("suite") == suite:
            return run
    return None


def _run_from_history(suite: str, baseline: dict) -> dict | None:
    """把 history 最后一行还原成可用于门禁的运行记录。

    history 只存指标值，指标方向（越高越好/越低越好）取基线里的权威声明——
    这样 CI 不需要重新跑评测，也能拦住已经提交上来的退化。
    """
    records = registry.read_history(suite, limit=1)
    if not records:
        return None

    record = records[-1]
    metrics = {}
    for name, value in (record.get("metrics") or {}).items():
        direction = ((baseline.get("metrics") or {}).get(name) or {}).get(
            "higher_is_better", True
        )
        metrics[name] = {"value": value, "higher_is_better": direction}

    return {
        "run_id": record.get("run_id", ""),
        "suite": suite,
        "metrics": metrics,
        "dataset": {"sha256": record.get("dataset_sha256") or ""},
        "git_rev": record.get("git_rev"),
    }


def cmd_trend(args) -> int:
    names = args.suite or sorted(path.stem for path in registry.HISTORY_DIR.glob("*.jsonl"))
    if not names:
        print("还没有任何历史记录：先跑一次 evalhub run")
        return 0

    unknown = [name for name in names if name not in load_suites()]
    if unknown:
        print(f"未知套件: {unknown}", file=sys.stderr)
        return 2

    for index, name in enumerate(names):
        if index:
            print()
        print(report.render_trend(name, registry.read_history(name, args.limit)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evalhub", description="跨项目评测汇总与回归门禁")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="运行套件并生成报告")
    run.add_argument("--all", action="store_true", help="跑全部套件")
    run.add_argument("--suite", action="append", help="指定套件，可重复")
    run.add_argument("--provider", help="driver.intent 选哪一路（rules/llm/hybrid）")
    run.add_argument("--update-baseline", action="store_true", help="把本次设为新基线")
    run.add_argument("--tolerance", type=float, default=0.02, help="相对容差，默认 0.02")
    run.add_argument("--allow-dataset-change", action="store_true",
                     help="数据集变化时只提示、不拦截（默认拦截）")
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
    g.add_argument("--allow-dataset-change", action="store_true",
                   help="数据集变化时只提示、不拦截（默认拦截）")
    g.add_argument("--from-history", action="store_true",
                   help="用 history 最后一行当本次运行（CI 里无需重跑评测）")
    g.set_defaults(func=cmd_gate)

    tr = sub.add_parser("trend", help="查看指标走势（读 history/，不跑评测）")
    tr.add_argument("--suite", action="append", help="指定套件，可重复；默认全部")
    tr.add_argument("--limit", type=int, default=8, help="每次看最近 N 次，默认 8")
    tr.set_defaults(func=cmd_trend)

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
