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
