#!/usr/bin/env bash
# 本地一键回归：严格数据前置校验 → 口径 → 门禁 → 测试。
#
# 需要同级目录下三个项目都在（verify / reconcile 依赖它们的数据源与快照）。
# 仓库 CI（.github/workflows/ci.yml）只跑不依赖兄弟仓库的那几项。
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"

step() { printf '\n=== %s ===\n' "$1"; }

step "数据前置校验（结构、内容、质量、载荷与对账）"
"$PY" -m datahub.cli verify --all --strict

step "口径字典完整性"
"$PY" -m datahub.cli metrics check

step "门禁指标可追溯"
"$PY" -m datahub.cli metrics audit

step "口径 SQL 对账"
"$PY" -m datahub.cli metrics reconcile

step "回归门禁"
"$PY" -m evalhub.cli gate --all

step "单元测试"
"$PY" -m pytest -q

printf '\n全部检查通过\n'
