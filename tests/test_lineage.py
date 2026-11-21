"""运行血缘：注册表是数据身份权威，套件配置必须随产物保存。"""

from types import SimpleNamespace

import pytest

from evalhub import cli
from evalhub.schema import validate_run

EVAL_SHA = "38ca064c09e18f16"
DB_SHA = "531560bcf3d98515"


def _registered(suite):
    return [
        ({"name": "ride_nl2sql", "version": "v1"}, {"role": "evaluation_set"}),
        ({"name": "ride_eval_ops", "version": "v1"}, {"role": "execution_database"}),
    ]


def _mock_assets(monkeypatch):
    monkeypatch.setattr(cli.dataset_manifest, "for_suite", _registered)
    monkeypatch.setattr(
        cli.dataset_snapshot,
        "load_meta",
        lambda item: {"sha256": EVAL_SHA} if item["name"] == "ride_nl2sql"
        else {"dataset_sha256": DB_SHA},
    )
    monkeypatch.setattr(
        cli.dataset_snapshot,
        "current_state",
        lambda item: {"sha256": EVAL_SHA} if item["name"] == "ride_nl2sql"
        else {"dataset_sha256": DB_SHA},
    )


def _run():
    return {
        "run_id": "20261121T000000-ride.nl2sql",
        "suite": "ride.nl2sql",
        "project": "ride-hailing-analytics-system",
        "generated_at": "2026-11-21T00:00:00",
        "mode": "gold_only",
        "dataset": {
            "name": "ride_nl2sql", "version": "unknown",
            "sha256": EVAL_SHA[:12], "cases": 22,
        },
        "model": None,
        "metrics": {"gold_pass_rate": {"value": 1.0, "higher_is_better": True}},
        "per_case": [],
    }


def _config():
    return {
        "adapter": "ride_nl2sql",
        "artifact": "eval/nl2sql/reports/latest.json",
        "command": ".venv/bin/python eval/nl2sql/run_eval.py --seed 42",
        "options": {"seed": 42, "project_path": "/machine/specific/path"},
    }


def test_attach_lineage_canonicalises_legacy_dataset_and_saves_config(monkeypatch):
    _mock_assets(monkeypatch)
    run = _run()
    cli._attach_lineage(run, "ride.nl2sql", _config())
    validate_run(run)

    assert run["dataset"]["version"] == "v1"
    assert run["dataset"]["sha256"] == EVAL_SHA
    assert [asset["role"] for asset in run["data_assets"]] == [
        "evaluation_set", "execution_database",
    ]
    assert run["data_assets"][1]["sha256"] == DB_SHA
    config = run["experiment"]["config"]
    assert config["options"] == {"seed": 42}
    assert "--seed 42" in config["command"]


def test_attach_lineage_rejects_dataset_fingerprint_mismatch(monkeypatch):
    _mock_assets(monkeypatch)
    run = _run()
    run["dataset"]["sha256"] = "aaaaaaaaaaaa"
    with pytest.raises(ValueError, match="指纹不一致"):
        cli._attach_lineage(run, "ride.nl2sql", _config())


def test_attach_lineage_rejects_database_changed_by_eval_command(monkeypatch):
    _mock_assets(monkeypatch)
    original = cli.dataset_snapshot.current_state

    def changed(item):
        if item["name"] == "ride_eval_ops":
            return {"dataset_sha256": "bbbbbbbbbbbbbbbb"}
        return original(item)

    monkeypatch.setattr(cli.dataset_snapshot, "current_state", changed)
    with pytest.raises(ValueError, match="评测后与登记快照不一致"):
        cli._attach_lineage(_run(), "ride.nl2sql", _config())


def test_run_stops_before_loading_baseline_when_data_check_fails(monkeypatch):
    monkeypatch.setattr(cli, "load_suites", lambda: {"driver.intent": {}})
    monkeypatch.setattr(cli, "_verify_data", lambda names: False)
    monkeypatch.setattr(
        cli.registry,
        "load_baseline",
        lambda name: pytest.fail("数据校验失败后不应继续执行评测流程"),
    )
    args = SimpleNamespace(all=False, suite=["driver.intent"])
    assert cli.cmd_run(args) == 1


def test_repo_baselines_have_complete_registered_lineage():
    """入库基线必须在无兄弟仓库的 CI 中也能证明资产身份与实验配置。"""
    from evalhub import registry

    for suite, run in registry.load_all_baselines().items():
        validate_run(run)
        assert run["experiment"]["config"]["adapter"]
        expected = [
            (item["name"], consumer["role"])
            for item, consumer in cli.dataset_manifest.for_suite(suite)
        ]
        actual = [(asset["name"], asset["role"]) for asset in run["data_assets"]]
        assert actual == expected

        for item, consumer in cli.dataset_manifest.for_suite(suite):
            meta = cli.dataset_snapshot.load_meta(item)
            asset = run["data_assets"][expected.index((item["name"], consumer["role"]))]
            assert asset["version"] == item["version"]
            assert asset["sha256"] == cli._state_fingerprint(meta)
