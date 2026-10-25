"""评测框架自身的测试：锁、阈值判定、离线守卫、结果结构、verify.py 的退出码。

注意：这里不要求评测 gate 全部通过（那是 scripts/verify.py 的职责），只验证框架行为正确。
"""
import importlib.util
import json
import socket
import sys

import pytest

from app.evaluation import runner
from app.evaluation.netguard import ExternalNetworkBlocked, block_external_network
from app.evaluation.suites import load_cases
from app.settings import ROOT


def test_lock_matches_repository_state():
    """评测集和阈值必须与锁定版本一致；有意修改须走 --update-lock --reason。"""
    assert runner.check_lock(runner.load_eval_config()) == []


def test_lock_detects_threshold_and_case_changes(tmp_path, monkeypatch):
    cfg = runner.load_eval_config()
    tampered = json.loads(json.dumps(cfg))
    tampered["suites"]["regulatory"]["metrics"]["answerable_hit_rate"]["threshold"] = 0.5   # 偷偷降阈值
    assert any("evaluation 段" in p for p in runner.check_lock(tampered))

    (tmp_path / "cases.json").write_text("{}", encoding="utf-8")                           # 评测集内容与锁不符
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    fake = {"suites": {"x": {"cases_file": "cases.json", "metrics": {}}}}
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps({"current": {
        "case_files": {"cases.json": "0" * 64},
        "evaluation_config_sha256": runner.current_hashes(fake)["evaluation_config_sha256"]}, "history": []}),
        encoding="utf-8")
    monkeypatch.setattr(runner, "LOCK_PATH", lock)
    assert any("评测集已变更" in p for p in runner.check_lock(fake))


def test_update_lock_requires_reason_and_keeps_history(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "LOCK_PATH", tmp_path / "lock.json")
    cfg = runner.load_eval_config()
    with pytest.raises(runner.EvalError):
        runner.update_lock(cfg, "   ")
    runner.update_lock(cfg, "第一次")
    lock = runner.update_lock(cfg, "第二次")
    assert [h["reason"] for h in lock["history"]] == ["第一次", "第二次"]


@pytest.mark.parametrize("op,thr,val,ok", [(">=", 0.8, 0.8, True), (">=", 0.8, 0.79, False), ("<=", 0.15, 1 / 7, True),
                                           ("<=", 0.15, 0.16, False), ("==", 0, 0, True), ("==", 0, 1, False)])
def test_judge_operators(op, thr, val, ok):
    spec = {"op": op, "threshold": thr, "gate": True, "formula": "f"}
    assert runner.judge("m", spec, (val, 1, 2))["passed"] is ok


def test_judge_zero_denominator_fails_not_passes():
    r = runner.judge("m", {"op": ">=", "threshold": 0.0, "gate": True, "formula": "f"}, (None, 0, 0))
    assert r["passed"] is False and r["value"] is None and "分母为 0" in r["note"]


def test_config_defines_required_suites_and_sizes():
    cfg = runner.load_eval_config()
    assert set(cfg["suites"]) == {"regulatory", "translation", "report"}
    for suite in cfg["suites"].values():
        for name, spec in suite["metrics"].items():
            assert spec["op"] in runner.OPS and isinstance(spec["threshold"], (int, float)), name
            assert isinstance(spec["gate"], bool) and spec["formula"].strip(), name
    reg = load_cases(ROOT / cfg["suites"]["regulatory"]["cases_file"])
    assert len(reg) == 10 and sum(c["kind"] == "should_refuse" for c in reg) == 3          # 10 例，其中 3 例应拒答
    assert len(load_cases(ROOT / cfg["suites"]["translation"]["cases_file"])) == 5          # 5 例
    assert len(load_cases(ROOT / cfg["suites"]["report"]["cases_file"])) >= 3


def test_eval_questions_are_distinct_from_dev_set():
    cfg = runner.load_eval_config()
    ev = {c["q"] for c in load_cases(ROOT / cfg["suites"]["regulatory"]["cases_file"])}
    dev = {c["q"] for c in load_cases(ROOT / "eval" / "dev" / "regulatory_dev.json")}
    assert not ev & dev


def test_netguard_blocks_external_but_not_loopback():
    with block_external_network():
        with pytest.raises(ExternalNetworkBlocked):
            socket.create_connection(("93.184.216.34", 80), timeout=1)
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        cli = socket.create_connection(srv.getsockname(), timeout=2)                       # 回环地址不受影响
        cli.close()
        srv.close()


def test_run_all_writes_structured_json(tmp_path):
    out = tmp_path / "res.json"
    res = runner.run_all(out)                                                               # 不要求 gate 全过，只验证结构
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["offline"] is True and set(data["suites"]) == {"regulatory", "translation", "report"}
    assert data["locked"] == runner.current_hashes(runner.load_eval_config())
    for suite in data["suites"].values():
        assert suite["n_cases"] >= 3 and suite["cases"]
        for m in suite["metrics"]:
            assert {"name", "value", "numerator", "denominator", "op", "threshold", "gate", "passed", "formula"} <= set(m)
    assert data["overall_passed"] == all(s["passed"] for s in res["suites"].values())
    reg = {m["name"]: m for m in data["suites"]["regulatory"]["metrics"]}
    assert reg["refusal_recall"]["denominator"] == 3


def _load_verify():
    spec = importlib.util.spec_from_file_location("verify_script", ROOT / "scripts" / "verify.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_verify_exit_codes(capsys):
    v = _load_verify()
    ok = [sys.executable, "-c", "raise SystemExit(0)"]
    bad = [sys.executable, "-c", "raise SystemExit(3)"]
    assert v.run_steps([("a", ok), ("b", ok)]) == 0
    assert v.run_steps([("a", ok), ("b", bad)]) == 3                                        # 失败 -> 非零
    assert v.run_steps([("a", bad), ("b", ok)]) == 3                                        # 第一步失败仍运行后续并汇总
    assert v.run_steps([("a", bad), ("b", ok)], fail_fast=True) == 3
    out = capsys.readouterr().out
    assert "[SKIP] b" in out and "验收结果：未通过" in out
