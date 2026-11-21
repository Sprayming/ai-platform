# AI Platform · 轻量级数据中台与评测中台

把多个独立 AI 项目之间的两件事收口：**数据可信**、**效果可信**。datahub 管数据资产、
版本、质量与指标口径；evalhub 管评测产物、实验血缘、基线门禁与趋势。

```bash
evalhub run --all        # 先严格校验数据，再跑三个项目并出汇总报告
evalhub gate --all       # 指标退化/指标消失/数据集变更则退出码 1（CI 用）
evalhub trend            # 最近若干次运行的指标走势（读 history/，不跑评测）

datahub list             # 登记了哪些数据资产
datahub verify --all     # 数据源 vs 快照，检查漂移（内容更新仅提示）
datahub verify --all --strict # 结构/内容/质量/载荷/对账全部通过才放行
datahub verify --suite ride.nl2sql --strict # 只查某套件的真实输入
datahub metrics check    # 指标口径字典完整性校验
datahub metrics audit    # 门禁指标是否都登记了口径（CI 用）
datahub metrics reconcile # 口径 SQL 在快照上跑数，与基准值对账（CI 用）

./scripts/ci.sh          # 本地跑数据校验、口径检查、回归门禁和单元测试
```

## 它不是什么

**不是企业级重型数据中台。** 没有同步调度、计算引擎、权限体系和多租户；当前聚焦三个
独立仓库最需要的资产注册、内容版本、最低质量规则、指标口径与血缘。

**不是评测 SaaS。** 没有并发调度、没有 Web 服务，报告是静态 HTML。命令行契约稳定之后，
才值得增加 API 和页面。

## 架构

```
                      contracts/
        eval_run.schema.json · eval_case.schema.json · dataset.schema.json
                     │                              │
                     ▼                              ▼
   ┌──────────────────────────┐      ┌──────────────────────────────┐
   │ evalhub（评测中台）       │      │ datahub（数据中台）            │
   │                          │      │                              │
   │ adapters 翻译各项目产物    │      │ manifests/  资产与消费关系     │
   │ run        前置校验+评测  │      │ snapshot/   内容哈希 + 快照    │
   │ gate/trend 门禁与趋势     │      │ preflight/  严格质量与对账     │
   │ experiment 实验配置追溯   │      │ metrics.yaml 口径字典         │
   └────────────┬─────────────┘      └──────────────┬───────────────┘
                ▼                                   ▼
        reports/ baselines/                  snapshots/*.meta.json
        summary.html                          （载荷可重建，元数据入库）
```

两层靠 `manifests/*.yaml` 的 `consumers` 和统一指纹连起来：每次运行的 `data_assets`
记录题目集、知识库、执行库各自的 `name/version/sha256/role`，`experiment.config`
记录实际命令与配置，才敢说「这次指标是在这份数据、这套配置下测的」。

当前实际对齐情况（`baselines/*.json` vs `snapshots/*.meta.json`）：

| 套件 | 角色 | 数据资产 | 版本与指纹 |
|---|---|---|---|
| `driver.intent` | 题目集 | `driver_intent_cases` | v1 · `53b0679aba7ac94c` |
| `rag.ragas` | 题目集 | `rag_golden` | v1 · `990dc9035915a76f` |
| `rag.ragas` | 知识库 | `rag_wiki` | v1 · 独立指纹 |
| `ride.nl2sql` | 题目集 | `ride_nl2sql` | v2 · `3b46ab027d05eb52` |
| `ride.nl2sql` | 执行库 | `ride_eval_ops` | v2 · `1441bb8650f211b7` |

`ride_ops` 是业务指标 SQL 对账库，不是 NL2SQL 执行库；`ride_eval_ops` 才是 `run_eval.py`
实际使用的数据库。三者分别登记，不共用指纹或指标基准。driver/rag 的历史报告缺版本或只有
12 位指纹时，血缘层只在名称相同且指纹前缀吻合的情况下补全，其余冲突一律拦截。

## 目录

```
contracts/                 三个契约
evalhub/                   评测中台
  adapters/                driver_intent · rag_ragas · ride_nl2sql
  cli.py                   前置校验、资产血缘、实验配置、run/gate/trend
  gate.py  report.py  registry.py  schema.py
datahub/                   数据中台
  manifest.py              资产注册与套件消费关系
  snapshot.py              内容哈希、快照、漂移检查
  preflight.py             strict 内容/质量/载荷/口径校验
  metrics.py               口径字典校验、门禁审计与渲染
  reconcile.py             口径 SQL 在快照上跑数对账
  cli.py                   list / snapshot / verify / metrics
manifests/                 每份数据资产一份声明
metrics.yaml               指标口径字典（核心资产）
snapshots/                 快照元数据（入库）；载荷 *.payload.* 不入库
suites.yaml                评测套件注册表
baselines/                 每个套件一份基线（入库）
history/                   每次运行一行，只留指标值与指纹（入库，CI 靠它复现门禁）
reports/                   每次运行产物与汇总报告（不入库）
scripts/ci.sh              本地一键回归
.github/workflows/ci.yml   仓库 CI（只跑不依赖兄弟仓库的检查）
```

## 快速开始

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 数据中台：首次或数据升版本后重建快照，然后严格校验
.venv/bin/python -m datahub.cli list
.venv/bin/python -m datahub.cli snapshot build --all
.venv/bin/python -m datahub.cli verify --all --strict
.venv/bin/python -m datahub.cli metrics show redemption_rate

# 评测中台：run 会自动再做一次前置校验，失败则不执行任何套件
.venv/bin/python -m evalhub.cli run --all
.venv/bin/python -m evalhub.cli gate --all
```

## 评测中台：统一产物，不统一执行

三套评测的执行逻辑完全不同（规则分类器 / RAGAS 采样 / SQL 执行结果比对），
强行统一执行是灾难；但它们的**产物**可以统一。所以 evalhub 只认
「命令 + 产物 + adapter」，换评测实现不用改框架。

`suites.yaml` 里 `command` 为空时只读既有产物，非空时先跑命令再生产物——
「只想看报告」和「必须重跑」是同一个入口。

### 数据血缘与实验配置

每次 `run` 除了主 `dataset`，还写入完整 `data_assets` 与 `experiment.config`：

```json
{
  "data_assets": [
    {"name": "ride_nl2sql", "version": "v2", "sha256": "3b46ab027d05eb52", "role": "evaluation_set"},
    {"name": "ride_eval_ops", "version": "v2", "sha256": "1441bb8650f211b7", "role": "execution_database"}
  ],
  "experiment": {
    "config": {"seed": 42, "drivers": 60, "orders": 800, "coupons": 400,
               "reference_time": "2026-11-20 12:00:00"}
  }
}
```

实际产物还保存 `model`、`git_rev`、adapter、artifact、command 和 options。由此可以区分指标变化
究竟来自题目、数据库、模型、代码还是运行参数。接入严格血缘时还发现 ride 只固定随机种子、
却使用当前时间生成数据；现在题目集声明 `reference_time`，生成器与相对时间 gold SQL 共用
同一时间，连续两次生成的内容指纹完全一致。

### 门禁的四条规则

| 情况 | 判定 | 理由 |
|---|---|---|
| 指标退化超过 `\|基线值\| × tolerance` | **regressed** | 唯一的阈值来源，不给每个指标单独配容差 |
| 指标键**消失** | **missing** | 适配器不再产出，只遍历本次指标的实现会静默放行 |
| 指标值为 `null` | skipped | 本次确实没算（如 ride 的 `gold_only` 模式），不误判为 0 |
| 数据集指纹/版本变化 | **dataset_changed** | 换数据后的涨跌不能归因于代码；`--allow-dataset-change` 降级为提示 |

另外基线里记着 `git_rev`，与本次不同时只提示 `stale_baseline`，不拦截——正常迭代必然不同，
但拿三个月前的基线卡今天的代码，结论需要人看一眼。

实测：把 accuracy 从 0.9821 改到 0.9018，`gate` 返回 `regressed` 且退出码 1；
把某指标的键删掉，返回 `missing` 且退出码 1。

### 历史与趋势：不重跑评测也能复现门禁

每次 `run` 会往 `history/<suite>.jsonl` 追加一行，只留指标值与数据集指纹：

```json
{"run_id":"20261120T071137-driver.intent","git_rev":"8ec1d3b",
 "dataset_sha256":"53b0679aba7ac94c","metrics":{"accuracy":0.9821,"macro_f1":0.9813}}
```

`evalhub trend` 直接读它打印走势；`evalhub gate --from-history` 把最后一行当成
「本次运行」，配合入库的 baselines 就能在 CI 里判定，不用重新跑三个项目。

为什么 history 入库、reports 不入库：reports 是含逐条样本的完整产物，可重建；
history 是压缩后的结论证据，入库后 CI 才能复现门禁判定。数据集指纹变化时
`trend` 会直接提示「前后数值不可直接比较」，避免把换数据当成模型变好。

## 数据中台：资产、质量与口径

### 1. manifest —— 数据从哪来、谁在用

每份数据资产一份 yaml，声明来源、版本、种子、生成脚本、负责人和变更要求。
`consumers` 记录真实消费套件与角色（`evaluation_set` / `knowledge_base` /
`execution_database`），每个评测套件必须且只能有一个题目集。`quality` 声明最低质量要求，
当前支持 `min_cases` 与 `non_empty_tables`。`source` 不存在时 `list` 会标「缺失」，
`snapshot` 直接跳过，strict 校验则拦截，不会静默产出空快照。

### 2. 快照 —— 哈希算内容，不算文件字节

SQLite 文件会因页布局、VACUUM、插入顺序变化而字节不同，但数据可以完全一样。
所以哈希是按「每张表的行内容」算的：

```
table:sha256(rows) → 再对全部表求一次 dataset_sha256
```

载荷（`*.payload.sqlite`）不入库——有 manifest 就能重建；元数据
（`*.meta.json`，含每张表的列名、行数、内容哈希）入库，CI 才能比对漂移。

### 3. verify —— 日常看漂移，评测前严格放行

项目里真出过这个事故：schema 把 `amount` 改成 `order_amount`，四个消费模块全挂，
而 44 个 mock 测试全绿。普通 `verify` 保留日常演进语义：

| 情况 | 普通 verify | `--strict` |
|---|---|---|
| 表/列消失 | **error** | **error** |
| 新增表/列、行数或内容变化 | warn | **error** |
| 快照载荷缺失或与元数据不一致 | 不检查 | **error** |
| 违反 `min_cases` / `non_empty_tables` | 不检查 | **error** |
| SQL 对账失败或偏离 `expected` | 不检查 | **error** |

`evalhub run` 在任何套件命令执行前自动做 strict 校验；套件命令结束后还会再算一次
实际输入指纹，防止评测脚本重建数据库时悄悄换数据。

### 4. metrics.yaml —— 口径字典

同一句话在不同团队嘴里不是同一个数。「核销率」就是个典型：

```
★ issued　发放口径（运营默认）：已核销券数 / 已发放券数
  claimed　领取口径：已核销券数 / 已领取券数
  settled　结算口径（财务）：已核销券数 / 已结算订单数
```

三个口径能差出好几个百分点，而这几个百分点直接决定下一轮补贴预算。
字典强制五件事：**必须有分子分母、必须有方向（`higher_is_better`）、多口径必须指定默认、
必须有负责人、改动留痕**。没有分子分母的指标一律不允许登记——这是 `metrics check` 的硬规则。

### 5. 口径不只是文档：能跑数、能对账、能追溯

`check` 只保证字典自身自洽，还有两个更硬的问题，分别由两个命令回答：

**`metrics audit` —— 门禁里的数，能不能说清怎么算的。**
扫描 `baselines/*.json` 里每个指标，要求它在字典里有 `eval_keys` 指向自己，
并校验方向与单位一致。方向和字典不一致时，门禁会把改进判成退化，属于必须拦下的硬错误。

```
口径审计通过：门禁指标 13 个全部可追溯（字典 15 条）
```

**`metrics reconcile` —— 口径写下来之后，算出来的是不是那个数。**
口径里可以写 `sql` + `expected`，`reconcile` 把它放到快照上执行：

```
[ok]    redemption_rate.issued      0.050000
[ok]    redemption_rate.settled     0.029630
[ok]    order_completion_rate.all_orders  0.843750
对账：5/5 条与登记基准一致（容差 0.0001）
```

两条设计上的取舍：

- **没 SQL 的指标不假装能算。** 补贴 ROI 需要实验组对照数据，库里没有，就不登记 `sql`，
  自动不参与对账，而不是编一个近似值。
- **`expected` 绑定数据集版本。** 数据升版本后基准值必然变，没重算就报漂移——这是有意为之：
  口径与数据必须一起版本化，不能只改数不改基准。

顺带暴露了一个真实问题：演示数据里没有「领取时间」字段，也没有 `created` 状态的订单，
所以 `claimed` 与 `issued`、`dispatched` 与 `all_orders` 两两数值相同。字典里直接写明了，
不粉饰成「两个口径差异明显」。

## 当前接入

| 套件 | 项目 | 评测集 | 指标 |
|---|---|---|---|
| `driver.intent` | driver-coupon-refund-agent | 56 条意图样本 | accuracy / macro-F1 / p95 |
| `rag.ragas` | rag-knowledge-base | 31 条金标问答 | faithfulness / answer_relevancy / context_precision / context_recall |
| `ride.nl2sql` | ride-hailing-analytics-system | 22 条 NL2SQL | gold_pass_rate / execution_accuracy / semantic_accuracy / semantic_coverage |

| 数据资产 | 类型 | 规模 | 用途 |
|---|---|---|---|
| `ride_ops` | sqlite | 5 表 / seed 42 | 业务指标 SQL 对账 |
| `ride_eval_ops` | sqlite v2 | 5 表 / seed 42 + 固定参考时间 | NL2SQL 实际执行库 |
| `ride_nl2sql` | file v2 | 22 条 | NL2SQL 题目与 gold SQL |
| `rag_wiki` | sqlite | 3 表 | RAG 知识库输入 |
| `driver_intent_cases` | file | 56 条 | 意图识别题目集 |
| `rag_golden` | file | 31 条 | RAG 金标题目集 |

口径字典当前 15 条：13 个门禁指标**全部**有 `eval_keys` 指向，其中 5 条带可执行 SQL（`ride_ops`
上的核销率三口径、完成率两口径），`metrics reconcile` 5/5 一致。

## 已知边界

- 只支持单进程本地运行，没有并发调度与远端执行
- 没有数据库、没有 Web 服务；报告是静态 HTML
- 快照是文件拷贝，不做增量；数据集超过几百 MB 需要换方案
- `verify` 比对结构与内容哈希，不做语义校验（列名对但含义变了它发现不了）；质量规则当前只有非空表和最少样本数，不等同于完整数据质量平台
- `rag.ragas` 的消融产物没有记录实际评估条数，卡片展示的是金标集声明条数（31）
- `ride_ops`（业务指标库）、`ride_eval_ops`（评测执行库）与 `ride_nl2sql`（题目集）是三份独立资产，不能互相解释指纹或复用 SQL 基准值
- `reconcile` 只覆盖能在 SQLite 上算出来的口径（当前 5 条）；意图识别、RAG 类指标依赖模型输出，无法在库上对账
- 字典的 `expected` 是数据集版本下的基准值，数据升版本必须重算，否则对账报漂移
- `history` 只留主数据集指纹与指标值，没有完整 `data_assets`、实验配置和逐条样本；完整证据看 `baselines/` 与可重建的 `reports/`
- 仓库 CI 只跑 check / audit / gate（`--from-history`）/ 测试：兄弟仓库不在同一 checkout，
  `verify` 与 `reconcile` 依赖它们的数据源与快照载荷，留给本地 `scripts/ci.sh`
