# AI Platform · 评测层与数据契约层

把多个独立 AI 项目之间的两件事收口：**数据从哪来**、**改动有没有变差**。

```bash
evalhub run --all        # 三个项目评测跑完，出一份汇总报告
evalhub gate --all       # 指标退化则退出码 1（CI 用）

datahub list             # 登记了哪些数据集
datahub verify --all     # 数据源 vs 快照，检查 schema 漂移
datahub metrics check    # 指标口径字典完整性校验
```

## 它不是什么

**不是数据中台。** 没有元数据中心、数据血缘、同步调度、权限体系。
企业级数据中台的六层（采集/存储/计算/资产/治理/服务）一个都没做，也不该做——
三个独立仓库之间最需要的是**契约**，不是**服务**。

**不是评测 SaaS。** 没有并发调度、没有 Web 服务，报告是静态 HTML。

## 架构

```
                      contracts/
        eval_run.schema.json · eval_case.schema.json · dataset.schema.json
                     │                              │
                     ▼                              ▼
   ┌──────────────────────────┐      ┌──────────────────────────────┐
   │ evalhub（评测层）         │      │ datahub（数据契约层）          │
   │                          │      │                              │
   │ adapters 翻译各项目产物    │      │ manifests/  数据集从哪来       │
   │ run --all  跨套件汇总     │      │ snapshot/   内容哈希 + 漂移    │
   │ gate       基线门禁       │      │ metrics.yaml 口径字典         │
   └────────────┬─────────────┘      └──────────────┬───────────────┘
                ▼                                   ▼
        reports/ baselines/                  snapshots/*.meta.json
        summary.html                          （载荷可重建，元数据入库）
```

两层靠**数据集指纹**连起来：driver 的评测报告里记着 `dataset_sha256: 53b0679aba7a`，
datahub 对同一份 `intent_cases.yml` 算出来的哈希也是 `53b0679aba7a` —— 两边对得上，
才敢说「这次指标是在这份数据上测的」。

## 目录

```
contracts/                 三个契约
evalhub/                   评测层
  adapters/                driver_intent · rag_ragas · ride_nl2sql
  cli.py  gate.py  report.py  registry.py  schema.py
datahub/                   数据契约层
  manifest.py              读取并校验 manifests/*.yaml
  snapshot.py              内容哈希、快照、漂移检查
  metrics.py               口径字典校验与渲染
  cli.py                   list / snapshot / verify / metrics
manifests/                 每个数据集一份声明
metrics.yaml               指标口径字典（核心资产）
snapshots/                 快照元数据（入库）；载荷 *.payload.* 不入库
suites.yaml                评测套件注册表
baselines/                 每个套件一份基线（入库）
reports/                   每次运行产物与汇总报告（不入库）
```

## 快速开始

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 评测层
.venv/bin/python -m evalhub.cli run --all
.venv/bin/python -m evalhub.cli gate --all

# 数据层
.venv/bin/python -m datahub.cli list
.venv/bin/python -m datahub.cli snapshot build --all
.venv/bin/python -m datahub.cli verify --all
.venv/bin/python -m datahub.cli metrics show redemption_rate
```

## 评测层：统一产物，不统一执行

三套评测的执行逻辑完全不同（规则分类器 / RAGAS 采样 / SQL 执行结果比对），
强行统一执行是灾难；但它们的**产物**可以统一。所以 evalhub 只认
「命令 + 产物 + adapter」，换评测实现不用改框架。

`suites.yaml` 里 `command` 为空时只读既有产物，非空时先跑命令再生产物——
「只想看报告」和「必须重跑」是同一个入口。

两个细节：

- **指标缺失写成 `null`，不删字段。** 否则门禁会把「这次没算」误判成「掉到 0」。
- **门禁只有一条相对容差规则**：`允许波动 = |基线值| × tolerance`。
  准确率波动 2% 和 p95 延迟波动 2% 用同一条规则，避免退化成一张没人维护的阈值表。

实测：把 accuracy 从 0.9821 改到 0.9018，`gate` 返回 `regressed` 且退出码 1。

## 数据契约层：三件事

### 1. manifest —— 数据从哪来

每个数据集一份 yaml，声明来源、版本、种子、生成脚本、负责人、变更要求。
`source` 不存在时 `list` 会标「缺失」，`snapshot` 直接跳过，不会静默产出空快照。

### 2. 快照 —— 哈希算内容，不算文件字节

SQLite 文件会因页布局、VACUUM、插入顺序变化而字节不同，但数据可以完全一样。
所以哈希是按「每张表的行内容」算的：

```
table:sha256(rows) → 再对全部表求一次 dataset_sha256
```

载荷（`*.payload.sqlite`）不入库——有 manifest 就能重建；元数据
（`*.meta.json`，含每张表的列名、行数、内容哈希）入库，CI 才能比对漂移。

### 3. verify —— 主要防 schema 漂移

项目里真出过这个事故：schema 把 `amount` 改成 `order_amount`，四个消费模块全挂，
而 44 个 mock 测试全绿。所以判定标准定得很死：

| 情况 | 级别 | 理由 |
|---|---|---|
| 表/列消失 | **error** | 消费方会直接 `no such column` |
| 新增表/列 | warn | 可能是有意的演进 |
| 行数变化 | warn | 数据更新是常态 |
| 行数不变但内容变了 | warn | 需要人确认不是脏写 |

### 4. metrics.yaml —— 口径字典

同一句话在不同团队嘴里不是同一个数。「核销率」就是个典型：

```
★ issued　发放口径（运营默认）：已核销券数 / 已发放券数
  claimed　领取口径：已核销券数 / 已领取券数
  settled　结算口径（财务）：已核销券数 / 已结算订单数
```

三个口径能差出好几个百分点，而这几个百分点直接决定下一轮补贴预算。
字典强制四件事：**必须有分子分母、多口径必须指定默认、必须有负责人、改动留痕**。
没有分子分母的指标一律不允许登记——这是 `metrics check` 的唯一硬规则，也是 CI 门禁。

## 当前接入

| 套件 | 项目 | 评测集 | 指标 |
|---|---|---|---|
| `driver.intent` | driver-coupon-refund-agent | 56 条意图样本 | accuracy / macro-F1 / p95 |
| `rag.ragas` | rag-knowledge-base | 31 条金标问答 | faithfulness / answer_relevancy / context_precision / context_recall |
| `ride.nl2sql` | ride-hailing-analytics-system | 22 条 NL2SQL | gold_pass_rate / execution_accuracy |

| 数据集 | 类型 | 规模 | 来源 |
|---|---|---|---|
| `ride_ops` | sqlite | 5 表 / seed 42 | generate_data.py |
| `rag_wiki` | sqlite | 3 表 | 人工整理 + Few-shot |
| `driver_intent_cases` | file | 56 条 | 自建 |
| `rag_golden` | file | 31 条 | 自建 |

## 已知边界

- 只支持单进程本地运行，没有并发调度与远端执行
- 没有数据库、没有 Web 服务；报告是静态 HTML
- 快照是文件拷贝，不做增量；数据集超过几百 MB 需要换方案
- `verify` 只比对结构与内容哈希，不做语义校验（列名对但含义变了它发现不了）
- `rag.ragas` 的消融产物没有记录实际评估条数，卡片展示的是金标集声明条数（31）
