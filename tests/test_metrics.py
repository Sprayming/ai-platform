"""指标口径字典测试：重点验证「写不清分子分母就不许登记」。"""

import pytest

from datahub import metrics as metrics_mod


def _write(tmp_path, monkeypatch, body: str):
    path = tmp_path / "metrics.yaml"
    path.write_text("version: v1\nowner: tester\n" + body, encoding="utf-8")
    monkeypatch.setattr(metrics_mod, "METRICS_PATH", path)


def test_repo_metrics_are_consistent():
    """仓库里真实的字典必须自洽，否则 CI 直接红。"""
    assert metrics_mod.check() == []


def test_redemption_rate_has_three_variants_and_canonical():
    item = metrics_mod.get("redemption_rate")
    keys = [v["key"] for v in item["variants"]]
    assert keys == ["issued", "claimed", "settled"]
    assert item["canonical"] == "issued"


def test_format_marks_canonical_variant():
    text = metrics_mod.format_metric(metrics_mod.get("redemption_rate"))
    assert "★ issued" in text
    assert "settled" in text


def test_missing_denominator_is_rejected(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, """
metrics:
  - id: bad_rate
    name: 缺分母
    unit: ratio
    owner: tester
    variants:
      - key: only
        name: 唯一口径
        numerator: 命中数
""")
    problems = metrics_mod.check()
    assert any("缺少 denominator" in p for p in problems)


def test_multiple_variants_require_canonical(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, """
metrics:
  - id: two_rates
    name: 两种口径
    unit: ratio
    owner: tester
    variants:
      - key: a
        name: A
        numerator: x
        denominator: y
      - key: b
        name: B
        numerator: x
        denominator: z
""")
    assert any("canonical" in p for p in metrics_mod.check())


def test_duplicate_id_is_rejected(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, """
metrics:
  - id: dup
    name: A
    unit: ratio
    owner: t
    variants: [{key: a, name: A, numerator: x, denominator: y}]
  - id: dup
    name: B
    unit: ratio
    owner: t
    variants: [{key: a, name: A, numerator: x, denominator: y}]
""")
    assert any("id 重复" in p for p in metrics_mod.check())


def test_missing_owner_is_rejected(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, """
metrics:
  - id: no_owner
    name: 无主
    unit: ratio
    variants: [{key: a, name: A, numerator: x, denominator: y}]
""")
    assert any("负责人" in p for p in metrics_mod.check())


def test_unknown_metric_raises():
    with pytest.raises(metrics_mod.MetricsError):
        metrics_mod.get("not_exist")


# --- 门禁指标 × 口径字典交叉校验 ---

def _baseline(suite: str, **metrics):
    return {
        "suite": suite,
        "metrics": {
            name: {"value": value, "higher_is_better": higher, **({"unit": unit} if unit else {})}
            for name, (value, higher, unit) in metrics.items()
        },
    }


def test_audit_passes_on_repo_data():
    """仓库里所有门禁指标都必须能追溯到口径定义。"""
    from evalhub import registry

    assert metrics_mod.audit(registry.load_all_baselines()) == []


def test_audit_flags_unregistered_metric(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, """
metrics:
  - id: known
    name: 已登记
    unit: ratio
    higher_is_better: true
    owner: t
    canonical: a
    eval_keys: ["driver.intent:known"]
    variants: [{key: a, name: A, numerator: x, denominator: y}]
""")
    problems = metrics_mod.audit({"driver.intent": _baseline("driver.intent", mystery=(0.9, True, None))})
    assert any("未登记口径" in p for p in problems)


def test_audit_flags_direction_mismatch(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, """
metrics:
  - id: lat
    name: 延迟
    unit: ms
    higher_is_better: true
    owner: t
    canonical: a
    eval_keys: ["driver.intent:latency_p95_ms"]
    variants: [{key: a, name: A, numerator: x, denominator: y}]
""")
    baselines = {"driver.intent": _baseline("driver.intent", latency_p95_ms=(500, False, "ms"))}
    assert any("方向不一致" in p for p in metrics_mod.audit(baselines))


def test_audit_flags_unit_mismatch(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, """
metrics:
  - id: lat
    name: 延迟
    unit: ratio
    higher_is_better: false
    owner: t
    canonical: a
    eval_keys: ["driver.intent:latency_p95_ms"]
    variants: [{key: a, name: A, numerator: x, denominator: y}]
""")
    baselines = {"driver.intent": _baseline("driver.intent", latency_p95_ms=(500, False, "ms"))}
    assert any("单位不一致" in p for p in metrics_mod.audit(baselines))


def test_audit_flags_eval_key_without_baseline_metric(tmp_path, monkeypatch):
    """基线里没有的指标不算错——套件可能还没跑过。"""
    _write(tmp_path, monkeypatch, """
metrics:
  - id: known
    name: 已登记
    unit: ratio
    higher_is_better: true
    owner: t
    canonical: a
    eval_keys: ["driver.intent:known", "driver.intent:future"]
    variants: [{key: a, name: A, numerator: x, denominator: y}]
""")
    baselines = {"driver.intent": _baseline("driver.intent", known=(0.9, True, None))}
    assert metrics_mod.audit(baselines) == []


def test_check_requires_expected_with_sql(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, """
metrics:
  - id: bad
    name: 有 SQL 无基准
    unit: ratio
    higher_is_better: true
    owner: t
    canonical: a
    dataset: ride_ops
    variants:
      - key: a
        name: A
        numerator: x
        denominator: y
        sql: "SELECT 1"
""")
    assert any("没有 expected" in p for p in metrics_mod.check())


def test_check_requires_higher_is_better(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, """
metrics:
  - id: no_direction
    name: 无方向
    unit: ratio
    owner: t
    canonical: a
    variants: [{key: a, name: A, numerator: x, denominator: y}]
""")
    assert any("higher_is_better" in p for p in metrics_mod.check())
