# AI Platform · 跨项目评测平台

把多个 AI 项目的评测统一到**一份契约 + 一条命令**。

三个项目的评测集格式不同、指标口径不同、报告格式也不同（一个写 yml、一个写 RAGAS 表格、一个只往终端打印），
改一次 prompt 只能挨个目录手动跑，靠记忆判断有没有退化。这个仓库解决的就是这件事：

```bash
evalhub run --all          # 跑完所有套件，出一份跨项目汇总报告
evalhub gate --all         # 按基线判定，退化就退出码 1（CI 用）
```

## 它不是什么

**不是数据中台。** 没有元数据中心、数据血缘、同步调度、权限体系。
企业级数据中台的六层（采集/存储/计算/资产/治理/服务）一个都没做，也不该做——
三个独立仓库之间最需要的是**契约**，不是**服务**。

## 架构

```
        contracts/                         ← 唯一的真相源
   eval_run.schema.json
   eval_case.schema.json
              │
              ▼
   adapters（每个项目 30~60 行翻译逻辑）
   driver_intent · rag_ragas · ride_nl2sql
                      │
                      ▼
             evalhub run --all
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
   reports/       baselines/     summary.html
  每次运行产物     当前基线        跨套件汇总
                      │
                      ▼
                 gate（退出码）
```

**核心设计决策：抽象放在 adapter，不放执行层。**
三套评测的执行逻辑完全不同（规则分类器 / RAGAS 采样 / SQL 执行结果比对），
强行统一执行是灾难；但它们的**产物**可以统一。所以 evalhub 只认「命令 + 产物 + 翻译器」，
换实现不改框架。

## 目录

```
contracts/                 三个契约（eval_run 是核心）
evalhub/
  cli.py                   run / compare / gate
  schema.py                契约加载与校验
  adapters/                各项目产物翻译器
  gate.py                  基线与相对容差判定
  report.py                自包含 HTML 报告
  registry.py              runs / baselines 读写
suites.yaml                套件注册表（项目路径、命令、产物、adapter）
baselines/                 每个套件一份 latest 基线（入库）
reports/                   每次运行产物与汇总报告（不入库）
tests/                     adapter、契约、门禁、报告
```

## 快速开始

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/python -m evalhub.cli run --all                   # 跑全部套件
.venv/bin/python -m evalhub.cli run --all --update-baseline  # 顺便设为新基线
.venv/bin/python -m evalhub.cli gate --all                   # 回归门禁
.venv/bin/python -m evalhub.cli compare <基线> <本次>          # 两次运行逐指标对比
```

`suites.yaml` 里 `command` 为空时只读既有产物；非空时先跑命令再生产物。
所以「只想看报告」和「必须重跑」是同一个入口。

## 统一产物长什么样

```json
{
  "run_id": "20261119T073001-ride.nl2sql",
  "suite": "ride.nl2sql",
  "project": "ride-hailing-analytics-system",
  "dataset": { "name": "ride_nl2sql", "version": "v1", "sha256": "3213d89ce27b", "cases": 22 },
  "metrics": {
    "gold_pass_rate":      { "value": 1.0,    "higher_is_better": true },
    "execution_accuracy":  { "value": 0.6818, "higher_is_better": true },
    "semantic_accuracy":   { "value": null,   "higher_is_better": true }
  },
  "per_case": [{ "case_id": "Q09", "passed": false, "detail": { "category": "time_filter" } }]
}
```

两个细节值得说：

- **指标缺失写成 `null`，不是删字段。** 否则门禁会把「这次没算」误判成「掉到 0」。
- **`dataset.sha256` 必须存在。** 题目改过而不升版本，历史指标就不可比——先让它在契约层面无法通过。

## 门禁规则

只有一条：**相对容差**。

```
允许波动 = |基线值| × tolerance        （基线为 0 时退化为绝对容差）
```

不按指标单独配阈值，是因为量纲差太远：准确率波动 0.02 已经很大，
p95 延迟波动 20ms 只是噪声。一条规则同时管住两类指标，才不会退化成一张没人维护的阈值表。

实测：把 accuracy 从 0.9821 改到 0.9018，`gate` 返回 `regressed` 且退出码 1。

## 当前接入

| 套件 | 项目 | 评测集 | 指标 |
|---|---|---|---|
| `driver.intent` | driver-coupon-refund-agent | 56 条意图样本 | accuracy / macro-F1 / p95 |
| `rag.ragas` | rag-knowledge-base | 31 条金标问答 | faithfulness / answer_relevancy / context_precision / context_recall |
| `ride.nl2sql` | ride-hailing-analytics-system | 22 条 NL2SQL | gold_pass_rate / execution_accuracy |

三个项目的产物格式互不相同：driver 写多 provider 的 JSON 报告，rag 有两种历史格式
（RAGAS 四指标 / 问答闸门通过率），ride 原本只往终端打印。adapter 把它们统一收口，
**没有要求任何一个项目重写自己的评测脚本**。

## 已知边界

- 只支持单进程本地运行，没有并发调度与远端执行
- 没有数据库、没有 Web 服务；报告是静态 HTML
- 逐条结果只保证记录失败样本，不保证全量逐条落盘
- 不做指标自动修复或归因，归因仍靠各项目自己的分析
- `rag.ragas` 的消融产物没有记录实际评估条数，卡片展示的是金标集声明条数（31）；
  真实消融跑的是其中一部分题，重跑一次即可自动对齐

