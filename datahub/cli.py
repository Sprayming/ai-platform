"""datahub 命令行入口。

    datahub list                         登记了哪些数据集
    datahub snapshot build --all         生成快照（载荷不进 git，元数据进 git）
    datahub snapshot build --name ride_ops
    datahub verify --all                 当前数据源 vs 快照，检查漂移
    datahub metrics list                 指标清单
    datahub metrics show redemption_rate 某个口径的完整定义
    datahub metrics check                字典完整性校验（CI 用）
"""

from __future__ import annotations

import argparse
import sys

from datahub import manifest as manifest_mod
from datahub import metrics as metrics_mod
from datahub import snapshot as snapshot_mod


def cmd_list(args) -> int:
    items = manifest_mod.load_all()
    if not items:
        print("还没有登记任何数据集")
        return 0
    header = f"{'NAME':<24}{'VER':<8}{'KIND':<8}{'OWNER':<10}SOURCE"
    print(header)
    print("-" * len(header))
    for item in items.values():
        source = item["source"]
        if manifest_mod.missing_source(item):
            source += "  (缺失)"
        print(f"{item['name']:<24}{item['version']:<8}{item['kind']:<8}"
              f"{item.get('owner', ''):<10}{source}")
    return 0


def cmd_snapshot(args) -> int:
    names = list(manifest_mod.load_all()) if args.all else ([args.name] if args.name else [])
    if not names:
        print("需要 --all 或 --name", file=sys.stderr)
        return 2

    failed = 0
    for name in names:
        item = manifest_mod.load(name)
        if manifest_mod.missing_source(item):
            print(f"[skip] {name}: 数据源不存在 {item['_source_path']}", file=sys.stderr)
            failed += 1
            continue
        payload, meta_path, meta = snapshot_mod.build(item)
        summary = f"{meta.get('dataset_sha256') or meta.get('sha256')}"
        print(f"[ok] {name}@{item['version']}  {summary}  → {payload.name}")
        print(f"     元数据 {meta_path.name}（进版本库，载荷不进）")
    return 1 if failed else 0


def cmd_verify(args) -> int:
    names = list(manifest_mod.load_all()) if args.all else ([args.name] if args.name else [])
    if not names:
        print("需要 --all 或 --name", file=sys.stderr)
        return 2

    errors = 0
    for name in names:
        item = manifest_mod.load(name)
        if manifest_mod.missing_source(item):
            print(f"[skip] {name}: 数据源不存在", file=sys.stderr)
            continue
        try:
            drifts = snapshot_mod.verify(item)
        except FileNotFoundError as exc:
            print(f"[skip] {name}: {exc}", file=sys.stderr)
            continue

        if not drifts:
            print(f"[ok] {name}@{item['version']}: 无漂移")
            continue

        print(f"[drift] {name}@{item['version']}")
        for drift in drifts:
            print(f"   {drift['level'].upper():<6}{drift['message']}")
            if drift["level"] == "error":
                errors += 1
    return 1 if errors else 0


def cmd_metrics(args) -> int:
    if args.action == "list":
        for item in metrics_mod.all_metrics():
            canonical = item.get("canonical")
            variants = len(item.get("variants") or [])
            print(f"{item['id']:<28}{item.get('name', ''):<18}"
                  f"{item.get('unit', ''):<8}{variants} 个口径  canonical={canonical}")
        return 0

    if args.action == "show":
        print(metrics_mod.format_metric(metrics_mod.get(args.metric_id)))
        return 0

    problems = metrics_mod.check()
    if problems:
        print(f"发现 {len(problems)} 个问题：")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"口径字典校验通过（{len(metrics_mod.all_metrics())} 个指标）")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="datahub", description="数据集登记、快照与指标口径")
    sub = parser.add_subparsers(dest="command", required=True)

    lst = sub.add_parser("list", help="列出已登记的数据集")
    lst.set_defaults(func=cmd_list)

    snap = sub.add_parser("snapshot", help="生成数据快照")
    snap.add_argument("action", choices=["build"])
    snap.add_argument("--name")
    snap.add_argument("--all", action="store_true")
    snap.set_defaults(func=cmd_snapshot)

    verify = sub.add_parser("verify", help="检查数据源与快照的漂移")
    verify.add_argument("--name")
    verify.add_argument("--all", action="store_true")
    verify.set_defaults(func=cmd_verify)

    met = sub.add_parser("metrics", help="指标口径字典")
    met.add_argument("action", choices=["list", "show", "check"])
    met.add_argument("metric_id", nargs="?")
    met.set_defaults(func=cmd_metrics)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "metrics" and args.action == "show" and not args.metric_id:
        print("metrics show 需要指标 id", file=sys.stderr)
        return 2
    try:
        return args.func(args)
    except (manifest_mod.ManifestError, metrics_mod.MetricsError, FileNotFoundError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
